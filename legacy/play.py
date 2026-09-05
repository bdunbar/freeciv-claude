#!/usr/bin/env python3
"""Command-line harness for playing Freeciv 2.6 by editing savegames.

  play.py new     --work DIR [--players N] [--size N] [--seed N]
  play.py observe --work DIR [--save PATH]
  play.py act     --work DIR --orders orders.json [--turns N]

`observe` prints the fog-limited state; `act` applies a JSON order spec and
advances the game. The decision-making happens outside this file -- that is the
agent's job.
"""

import argparse
import glob
import json
import os
import sys

import fcmap
import fcorders
import fcpath
import fcstate
import secfile
from fcrun import Runner

STATE = 'agent_state.json'


def _meta_path(work):
    return os.path.join(work, STATE)


def load_meta(work):
    with open(_meta_path(work)) as f:
        return json.load(f)


def save_meta(work, meta):
    with open(_meta_path(work), 'w') as f:
        json.dump(meta, f, indent=2)


def latest_save(work):
    return load_meta(work)['save']


def view_of(save, section='player0'):
    return fcstate.PlayerView(secfile.SecFile(save), section)


# ---------------- commands ----------------

def cmd_new(args):
    r = Runner(args.work)
    save = r.new_game(players=args.players, size=args.size,
                      topology=args.topology, seed=args.seed, skill=args.skill)
    sf = secfile.SecFile(save)
    meta = {'save': save, 'section': 'player0',
            'player': sf.get('player0', 'name'),
            'nation': sf.get('player0', 'nation'),
            'turn': sf.get('game', 'turn')}
    save_meta(args.work, meta)
    print('New game. You are %s of the %s.' % (meta['player'], meta['nation']))
    print(view_of(save).summary())


def cmd_board(args):
    meta = load_meta(args.work)
    v = view_of(args.save or meta['save'], meta['section'])
    print(v.summary())
    print()
    print(fcstate.board(v, tuple(args.centre) if args.centre else None,
                        args.radius))


def cmd_observe(args):
    meta = load_meta(args.work)
    save = args.save or meta['save']
    v = view_of(save, meta['section'])
    print(v.summary())
    if args.map:
        centre = v.cities[0]['pos'] if v.cities else (
            v.units[0]['pos'] if v.units else (0, 0))
        print('\n--- known terrain around %s (radius %d) ---' % (str(centre), args.map))
        print(v.local_map(centre, args.map))
    if args.json:
        print(json.dumps({'turn': v.turn, 'gold': v.gold,
                          'cities': v.cities, 'units': v.units,
                          'research': v.research,
                          'sightings': v.foreign_sightings()}, indent=2))


def _resolve_orders(view, unit, spec):
    """Turn one unit's JSON order spec into a list of fcorders.Order."""
    out = []
    for step in spec:
        if 'move' in step:
            d = step['move']
            out.append(fcorders.move(fcmap.DIR_BY_NAME[d] if isinstance(d, str) else d))
        elif 'goto' in step:
            goal = tuple(step['goto'])
            dirs = fcpath.find_path(view, unit['pos'], goal,
                                    domain=step.get('domain', 'land'))
            if dirs is None:
                raise ValueError('no known path from %s to %s for unit %s'
                                 % (unit['pos'], goal, unit['id']))
            out.extend(fcorders.move(d) for d in dirs)
        elif 'found_city' in step:
            out.append(fcorders.found_city())
        elif 'activity' in step:
            act = step['activity']
            tgt = step.get('target')
            if isinstance(tgt, str):
                tgt = view.extra_index(tgt)
            elif tgt is None:
                # ACTIVITY_GEN_ROAD/BASE need an explicit extra to build;
                # the terrain-altering activities do not.
                # freeciv-server 2.6.6 segfaults if an ACTIVITY order that
                # builds an extra arrives with EXTRA_NONE and has to fall back
                # to unit_assign_specific_activity_target() -- reproducible with
                # move-onto-Grassland followed by irrigate. Always name the
                # extra explicitly, which is what the real client does too.
                default = {'road': 'Road', 'base': 'Fortress',
                           'irrigate': 'Irrigation', 'mine': 'Mine'}.get(act)
                if default:
                    tgt = view.extra_index(default)
            out.append(fcorders.do_activity(act, tgt))
        elif 'wait' in step:
            out.append(fcorders.wait_full_mp())
        elif 'disband' in step:
            out.append(fcorders.Order('disband'))
        else:
            raise ValueError('unrecognised order step: %r' % step)
    return out


def apply_orders(sf, view, spec):
    """Apply a full order spec to a savegame. Returns a list of log lines."""
    log = []
    sec = view.section
    ut = sf.table(sec, 'u')
    ct = sf.table(sec, 'c')

    for uid, ospec in (spec.get('units') or {}).items():
        uid = int(uid)
        unit = next((u for u in view.units if u['id'] == uid), None)
        if unit is None:
            log.append('skip unit %d: not ours / no longer exists' % uid)
            continue
        orders = _resolve_orders(view, unit, ospec.get('orders', []))
        fcorders.set_orders(sf, ut, unit['row'], orders,
                            repeat=ospec.get('repeat', False))
        log.append('unit %d (%s) <- %d orders' % (uid, unit['type'], len(orders)))

    for cid, cspec in (spec.get('cities') or {}).items():
        cid = int(cid)
        city = next((c for c in view.cities if c['id'] == cid), None)
        if city is None:
            log.append('skip city %d: not ours' % cid)
            continue
        if 'build' in cspec:
            kind, name = cspec['build']
            sf.update_row(ct, city['row'],
                          currently_building_kind=kind,
                          currently_building_name=name)
            log.append('city %d (%s) <- build %s' % (cid, city['name'], name))

    rates = spec.get('rates')
    if rates:
        total = rates.get('tax', 0) + rates.get('science', 0) + rates.get('luxury', 0)
        if total != 100:
            raise ValueError('tax/science/luxury must sum to 100, got %d' % total)
        for key, field in (('tax', 'rates.tax'), ('science', 'rates.science'),
                           ('luxury', 'rates.luxury')):
            if key in rates:
                sf.set(sec, field, rates[key])
        log.append('rates <- %s' % rates)

    res = spec.get('research')
    if res:
        rt = sf.table('research', 'r')
        num = sf.get(sec, 'team_no', 0)
        for i, row in enumerate(rt.rows):
            if row.get('number') == num:
                changes = {}
                if 'now' in res:
                    changes['now_name'] = res['now']
                if 'goal' in res:
                    changes['goal_name'] = res['goal']
                sf.update_row(rt, i, **changes)
                log.append('research <- %s' % res)
                break
    return log


def cmd_act(args):
    meta = load_meta(args.work)
    save = meta['save']
    sf = secfile.SecFile(save)
    view = fcstate.PlayerView(sf, meta['section'])

    spec = json.load(open(args.orders)) if args.orders else {}
    for line in apply_orders(sf, view, spec):
        print('  ' + line)

    staged = os.path.join(args.work, 'saves',
                          'staged-T%04d.sav.bz2' % view.turn)
    sf.write(staged)

    r = Runner(args.work)
    new_save = r.step(staged, turns=args.turns, human_player=meta['player'])
    meta['save'] = new_save
    meta['turn'] = secfile.SecFile(new_save).get('game', 'turn')
    save_meta(args.work, meta)
    print('advanced to turn %s' % meta['turn'])
    print(view_of(new_save, meta['section']).summary())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True)
    sub = p.add_subparsers(dest='cmd', required=True)

    n = sub.add_parser('new')
    n.add_argument('--players', type=int, default=3)
    n.add_argument('--size', type=int, default=2)
    n.add_argument('--seed', type=int)
    n.add_argument('--topology', default='WRAPX')
    n.add_argument('--skill', default='hard')
    n.set_defaults(func=cmd_new)

    b = sub.add_parser('board')
    b.add_argument('--save')
    b.add_argument('--radius', type=int, default=7)
    b.add_argument('--centre', type=int, nargs=2)
    b.set_defaults(func=cmd_board)

    o = sub.add_parser('observe')
    o.add_argument('--save')
    o.add_argument('--map', type=int, default=0)
    o.add_argument('--json', action='store_true')
    o.set_defaults(func=cmd_observe)

    a = sub.add_parser('act')
    a.add_argument('--orders')
    a.add_argument('--turns', type=int, default=1)
    a.set_defaults(func=cmd_act)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
