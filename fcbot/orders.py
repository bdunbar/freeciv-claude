"""Apply a list of orders written as JSON.

Orders name things the way a player thinks of them -- "Phalanx", "Currency",
"fortify" -- and this resolves those to the ids the protocol wants.

Each order returns a line saying what was done with it. Anything caught here
-- an unknown name, a unit we do not own, a route that does not exist -- is
reported as FAILED and the remaining orders still run. What cannot be caught
here is the server's own verdict: "sent" means the packet went out, and
whether the server honoured it shows up in the next turn's observation.
"""

from . import state

ACTIVITIES = {
    "idle": state.ACTIVITY_IDLE,
    "fortify": state.ACTIVITY_FORTIFYING,
    "fortifying": state.ACTIVITY_FORTIFYING,
    "sentry": state.ACTIVITY_SENTRY,
    "pillage": state.ACTIVITY_PILLAGE,
    "irrigate": state.ACTIVITY_IRRIGATE,
    "mine": state.ACTIVITY_MINE,
    "cultivate": state.ACTIVITY_CULTIVATE,
    "plant": state.ACTIVITY_PLANT,
    "transform": state.ACTIVITY_TRANSFORM,
    "clean": state.ACTIVITY_CLEAN,
    "convert": state.ACTIVITY_CONVERT,
    # Not activities in 3.2 -- the server drives these itself.
    "explore": "explore",
    "auto_worker": "auto_worker",
    "auto worker": "auto_worker",
}


class OrderError(Exception):
    pass


def _tile(game, value):
    """A tile index, given either an index or an [x, y] map coordinate."""
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise OrderError("a tile coordinate needs [x, y], got %r" % (value,))
        index = game.topo.map_to_index(int(value[0]), int(value[1]))
        if index is None:
            raise OrderError("no tile at map position %r" % (value,))
        return index
    index = int(value)
    if not 0 <= index < game.topo.size():
        raise OrderError("tile index %d is off the map" % index)
    return index


def _lookup(table, wanted, what):
    wanted_l = str(wanted).lower()
    for key, item in table.items():
        if wanted_l in (str(item.get("rule_name", "")).lower(),
                        str(item.get("name", "")).lower()):
            return key, item
    raise OrderError("no %s called %r" % (what, wanted))


def _production(game, spec):
    """['unit', 'Phalanx'] or ['improvement', 'Temple'] -> (kind, value)."""
    if not isinstance(spec, (list, tuple)) or len(spec) != 2:
        raise OrderError("production needs ['unit'|'improvement', name], "
                         "got %r" % (spec,))
    kind, name = spec
    kind = str(kind).lower()
    if kind in ("unit", "units"):
        uid, _ = _lookup(game.ruleset.units, name, "unit type")
        return state.VUT_UTYPE, uid
    if kind in ("improvement", "building", "wonder"):
        bid, _ = _lookup(game.ruleset.buildings, name, "improvement")
        return state.VUT_IMPROVEMENT, bid
    raise OrderError("production kind must be 'unit' or 'improvement', "
                     "got %r" % (kind,))


#: RESEARCH_INFO's `inventions` string, one character per tech.
UNKNOWN, RESEARCHABLE, KNOWN = "0", "1", "2"


def _invention(game, tech_id):
    research = game.my_research or {}
    inventions = research.get("inventions") or ""
    if 0 <= tech_id < len(inventions):
        return inventions[tech_id]
    return UNKNOWN


def _check_researchable(game, tech_id, tech):
    """What `handle_player_research` requires: not already known, and every
    prerequisite in hand."""
    status = _invention(game, tech_id)
    if status == KNOWN:
        raise OrderError("we already know %s -- researching it again would "
                         "be dropped silently and cost a turn"
                         % tech.get("name"))
    if status != RESEARCHABLE:
        raise OrderError("%s is not researchable yet: its prerequisites are "
                         "not known. Set it as a research_goal instead and "
                         "the server will path to it." % tech.get("name"))


# -- individual orders --------------------------------------------------

def _unit_order(client, order):
    game = client.game
    uid = int(order["unit"])
    unit = game.my_units().get(uid)
    if unit is None:
        raise OrderError("we have no unit %d" % uid)

    if "goto" in order:
        dest = _tile(game, order["goto"])
        then = None
        follow = order.get("then")
        if follow == "found_city":
            then = {"order": state.ORDER_PERFORM_ACTION,
                    "action": state.ACTION_FOUND_CITY, "target": dest}
        elif follow == "fortify":
            then = {"order": state.ORDER_PERFORM_ACTION,
                    "action": state.ACTION_FORTIFY, "target": dest}
        elif follow == "sentry":
            then = {"order": state.ORDER_ACTIVITY,
                    "activity": state.ACTIVITY_SENTRY}
        elif follow is not None:
            raise OrderError("unit %d: unknown 'then' %r "
                             "(found_city, fortify, sentry)" % (uid, follow))
        if not client.goto(uid, dest, then=then):
            raise OrderError("unit %d: no route to tile %d over terrain we "
                             "know" % (uid, dest))
        return "unit %d: sent route to tile %d%s" % (
            uid, dest, " then " + follow if follow else "")

    if "activity" in order:
        name = str(order["activity"]).lower()
        if name not in ACTIVITIES:
            raise OrderError("unit %d: unknown activity %r (known: %s)" %
                             (uid, order["activity"],
                              ", ".join(sorted(ACTIVITIES))))
        activity = ACTIVITIES[name]
        if activity == "explore":
            client.explore(uid)
            return "unit %d: sent auto explore" % uid
        if activity == "auto_worker":
            client.auto_settler(uid)
            return "unit %d: sent auto worker" % uid
        client.do_activity(uid, activity)
        return "unit %d: sent %s" % (uid, name)

    if "found_city" in order:
        name = order["found_city"]
        if not client.build_city(uid, str(name)):
            raise OrderError("unit %d cannot found a city here" % uid)
        return "unit %d: sent found city %r" % (uid, str(name))

    if "disband" in order:
        client.do_action(uid, state.ACTION_DISBAND_UNIT, unit["tile"])
        return "unit %d: sent disband" % uid

    raise OrderError("unit %d: nothing to do (goto, activity, found_city, "
                     "disband)" % uid)


def _city_order(client, order):
    game = client.game
    cid = int(order["city"])
    city = game.my_cities().get(cid)
    if city is None:
        raise OrderError("we have no city %d" % cid)
    done = []

    if "build" in order:
        kind, value = _production(game, order["build"])
        client.change_production(cid, kind, value)
        note = ""
        if kind == state.VUT_UTYPE:
            # Not an error -- you queue this while the city grows -- but it
            # is the kind of thing that quietly costs a turn.
            pop_cost = game.pop_cost(value)
            size = city.get("size", 0)
            if pop_cost and size <= pop_cost:
                note = (" (NOTE: costs %d population, city is size %d, so it "
                        "cannot finish until size %d)"
                        % (pop_cost, size, pop_cost + 1))
        done.append("build %s%s" % (order["build"][1], note))

    if "worklist" in order:
        entries = [_production(game, spec) for spec in order["worklist"]]
        client.set_worklist(cid, entries)
        done.append("worklist of %d" % len(entries))

    if order.get("buy"):
        client.buy_production(cid)
        done.append("buy for %s gold" % city.get("buy_cost"))

    for key in ("work_tile", "work_tiles"):
        if key in order:
            tiles = order[key]
            if not isinstance(tiles, list) or (tiles and not
                                               isinstance(tiles[0], (list, tuple))):
                tiles = [tiles]
            for spec in tiles:
                index = _tile(game, spec)
                _check_in_city_radius(game, city, index)
                client.make_worker(cid, index)
            done.append("work %d tile(s)" % len(tiles))

    for key in ("stop_working", "unwork_tile"):
        if key in order:
            tiles = order[key]
            if not isinstance(tiles, list) or (tiles and not
                                               isinstance(tiles[0], (list, tuple))):
                tiles = [tiles]
            for spec in tiles:
                index = _tile(game, spec)
                _check_in_city_radius(game, city, index)
                client.make_specialist(cid, index)
            done.append("free %d tile(s) into specialists" % len(tiles))

    if "specialist" in order:
        spec = order["specialist"]
        if not isinstance(spec, dict) or "from" not in spec or "to" not in spec:
            raise OrderError('specialist needs {"from": ..., "to": ...}, '
                             "got %r" % (spec,))
        from_id, from_s = _lookup(game.ruleset.specialists, spec["from"],
                                  "specialist")
        to_id, to_s = _lookup(game.ruleset.specialists, spec["to"],
                              "specialist")
        if not (city.get("specialists") or [])[from_id:from_id + 1] or \
                not city["specialists"][from_id]:
            raise OrderError("city %d has no %s to reassign"
                             % (cid, from_s.get("rule_name")))
        client.change_specialist(cid, from_id, to_id)
        done.append("a %s becomes a %s" % (from_s.get("rule_name"),
                                           to_s.get("rule_name")))

    if not done:
        raise OrderError("city %d: nothing to do (build, worklist, buy, "
                         "work_tile, stop_working, specialist)" % cid)
    return "city %s: sent %s" % (city.get("name", cid), ", ".join(done))


def _check_in_city_radius(game, city, index):
    radius_sq = city.get("city_radius_sq", 5)
    if game.topo.sq_distance(city["tile"], index) > radius_sq:
        x, y = game.topo.index_to_map(index)
        raise OrderError("tile [%d, %d] is outside %s's work radius"
                         % (x, y, city.get("name")))
    if index == city["tile"]:
        raise OrderError("the city centre tile is worked for free and cannot "
                         "be reassigned")


# -- diplomacy ----------------------------------------------------------
#
# A treaty is a meeting with clauses on the table, and it only takes effect
# when both sides have accepted the table as it stands. Adding a clause
# clears both acceptances, so "offer" always accepts last.

CLAUSE_TYPES = {
    "advance": state.CLAUSE_ADVANCE,
    "tech": state.CLAUSE_ADVANCE,
    "gold": state.CLAUSE_GOLD,
    "map": state.CLAUSE_MAP,
    "worldmap": state.CLAUSE_MAP,
    "seamap": state.CLAUSE_SEAMAP,
    "city": state.CLAUSE_CITY,
    "ceasefire": state.CLAUSE_CEASEFIRE,
    "cease-fire": state.CLAUSE_CEASEFIRE,
    "peace": state.CLAUSE_PEACE,
    "alliance": state.CLAUSE_ALLIANCE,
    "vision": state.CLAUSE_VISION,
    "shared_vision": state.CLAUSE_VISION,
    "embassy": state.CLAUSE_EMBASSY,
    "shared_tiles": state.CLAUSE_SHARED_TILES,
}

#: Which existing diplomatic states a pact clause can be reached from
#: (common/player.c pplayer_can_make_treaty).
_PACT_FROM = {
    state.CLAUSE_CEASEFIRE: (state.DS_WAR,),
    state.CLAUSE_PEACE: (state.DS_WAR, state.DS_CEASEFIRE),
}


def _player(game, wanted):
    """A player, named by leader, nation, username or number."""
    if isinstance(wanted, bool):
        raise OrderError("'with' needs a player, got %r" % (wanted,))
    if isinstance(wanted, int):
        if wanted not in game.players:
            raise OrderError("there is no player %d" % wanted)
        return wanted
    wanted_l = str(wanted).lower()
    for pn, p in sorted(game.players.items()):
        if pn == game.player_no:
            continue
        nation = game.ruleset.nations.get(p.get("nation"), {})
        names = [p.get("name"), p.get("username"),
                 nation.get("name"), nation.get("rule_name"),
                 nation.get("adjective"), nation.get("plural")]
        if wanted_l in [str(n).lower() for n in names if n]:
            return pn
    raise OrderError("no player called %r -- the diplomacy section of the "
                     "observation lists everyone we have met" % (wanted,))


def _clause(client, other, spec):
    """One clause, as (giver, clause type, value).

    Accepts a bare name ("peace"), a {"type": ..., "from": ..., "value": ...}
    object, or the compact {"gold": 50, "from": "me"} form.
    """
    game = client.game
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        raise OrderError("a clause is a name or an object, got %r" % (spec,))
    spec = dict(spec)
    giver_spec = spec.pop("from", "me")
    value = spec.pop("value", None)

    if "type" in spec:
        name = str(spec.pop("type")).lower()
    else:
        # compact form: the clause name is the key, its value the value
        keys = [k for k in spec if str(k).lower() in CLAUSE_TYPES]
        if len(keys) != 1:
            raise OrderError("clause %r: say which clause it is, e.g. "
                             '"peace" or {"type": "gold", "value": 50}'
                             % (spec,))
        name = str(keys[0]).lower()
        value = spec.pop(keys[0])
    if name not in CLAUSE_TYPES:
        raise OrderError("unknown clause %r (known: %s)" %
                         (name, ", ".join(sorted(CLAUSE_TYPES))))
    ctype = CLAUSE_TYPES[name]

    if str(giver_spec).lower() in ("me", "us", "self"):
        giver = game.player_no
    elif str(giver_spec).lower() in ("them", "they", "other", "you"):
        giver = other
    else:
        giver = _player(game, giver_spec)
    if giver not in (game.player_no, other):
        raise OrderError("a clause has to be given by us or by the other "
                         "side, not by %s" % _player_label(game, giver))

    number = 0
    if ctype == state.CLAUSE_ADVANCE:
        if value is None:
            raise OrderError("an advance clause needs which tech to trade")
        number, _tech = _lookup(game.ruleset.techs, value, "tech")
    elif ctype == state.CLAUSE_GOLD:
        if value is None:
            raise OrderError("a gold clause needs an amount")
        number = int(value)
        if number <= 0:
            raise OrderError("gold has to be more than nothing, got %d" % number)
        if giver == game.player_no:
            have = (game.me or {}).get("gold", 0)
            if number > have:
                raise OrderError("we cannot give %d gold; we have %d"
                                 % (number, have))
    elif ctype == state.CLAUSE_CITY:
        if value is None:
            raise OrderError("a city clause needs which city to hand over")
        number = _city_id(game, value, giver)

    # What the server would reject anyway, caught here where it can be read.
    ds = game.diplstate(other)
    now = ds.get("type") if ds else None
    if ctype in state.PACT_CLAUSES:
        already = {state.CLAUSE_CEASEFIRE: (state.DS_CEASEFIRE,),
                   state.CLAUSE_PEACE: (state.DS_PEACE, state.DS_ARMISTICE),
                   state.CLAUSE_ALLIANCE: (state.DS_ALLIANCE,)}[ctype]
        if now in already:
            raise OrderError("we are already at %s with %s" %
                             (name, _player_label(game, other)))
        allowed = _PACT_FROM.get(ctype)
        if allowed is not None and now not in allowed:
            raise OrderError(
                "%s is only reachable from %s, and we are at %s with %s" %
                (name, " or ".join(state.DIPLSTATE_NAMES[d] for d in allowed),
                 state.DIPLSTATE_NAMES.get(now, "no contact"),
                 _player_label(game, other)))
    if ctype == state.CLAUSE_EMBASSY:
        # The giver's nation is the one an embassy gets established in.
        if giver == game.player_no and game.gives_embassy_to(other):
            raise OrderError("%s already has an embassy with us" %
                             _player_label(game, other))
        if giver == other and game.has_embassy_with(other):
            raise OrderError("we already have an embassy with %s" %
                             _player_label(game, other))
    return giver, ctype, number


def _city_id(game, value, giver):
    if isinstance(value, int) or str(value).isdigit():
        cid = int(value)
        if cid in game.cities or cid in game.short_cities:
            return cid
        raise OrderError("we know of no city %d" % cid)
    for table in (game.cities, game.short_cities):
        for cid, c in table.items():
            if str(c.get("name", "")).lower() == str(value).lower():
                return cid
    raise OrderError("we know of no city called %r" % (value,))


def _player_label(game, pn):
    p = game.players.get(pn)
    return p.get("name") if p else "player %s" % pn


def _clause_text(game, giver, ctype, value):
    name = state.CLAUSE_NAMES.get(ctype, str(ctype))
    who = "we give" if giver == game.player_no else "they give"
    if ctype in state.PACT_CLAUSES:
        return name
    if ctype == state.CLAUSE_ADVANCE:
        return "%s %s" % (who, _name_of(game.ruleset.techs, value))
    if ctype == state.CLAUSE_GOLD:
        return "%s %d gold" % (who, value)
    if ctype == state.CLAUSE_CITY:
        city = game.cities.get(value) or game.short_cities.get(value) or {}
        return "%s %s" % (who, city.get("name", value))
    return "%s %s" % (who, name)


def _name_of(table, key):
    item = table.get(key) or {}
    return item.get("rule_name") or item.get("name") or str(key)


def _diplomacy_order(client, order):
    game = client.game
    what = str(order["diplomacy"]).lower().replace("-", "_").replace(" ", "_")
    if "with" not in order:
        raise OrderError("a diplomacy order needs 'with': which player")
    other = _player(game, order["with"])
    label = _player_label(game, other)
    treaty = game.treaties.get(other)

    if what in ("meet", "open", "init", "init_meeting"):
        if treaty is not None:
            return "already in a meeting with %s" % label
        if not game.can_meet(other):
            raise OrderError("cannot meet %s: no embassy and no recent "
                             "contact" % label)
        client.init_meeting(other)
        return "asked %s for a meeting" % label

    if what in ("offer", "propose"):
        specs = order.get("clauses")
        if specs is None:
            raise OrderError("an offer needs 'clauses', e.g. "
                             '["peace"] or [{"type": "gold", "value": 50}]')
        if not isinstance(specs, list):
            specs = [specs]
        clauses = [_clause(client, other, spec) for spec in specs]
        opened = False
        if treaty is None:
            if not game.can_meet(other):
                raise OrderError("cannot meet %s: no embassy and no recent "
                                 "contact" % label)
            client.init_meeting(other)
            opened = True
        for giver, ctype, value in clauses:
            client.create_clause(other, giver, ctype, value)
        said = ", ".join(_clause_text(game, *c) for c in clauses)
        # Accepting last is what makes the offer an offer: every clause we
        # just added cleared both sides' acceptance.
        if order.get("accept", True):
            client.accept_treaty(other)
            return "offered %s: %s (and accepted our side)%s" % (
                label, said, " -- meeting opened" if opened else "")
        return "put to %s: %s%s" % (label, said,
                                    " -- meeting opened" if opened else "")

    if what == "accept":
        if treaty is None:
            raise OrderError("no meeting with %s to accept -- offer "
                             "something first" % label)
        if treaty.i_accepted:
            return "already accepted the treaty with %s" % label
        client.accept_treaty(other)
        return "accepted the treaty with %s (%d clauses)" % (
            label, len(treaty.clauses))

    if what in ("unaccept", "retract"):
        if treaty is None or not treaty.i_accepted:
            raise OrderError("we have not accepted anything with %s" % label)
        client.accept_treaty(other)       # the request toggles
        return "withdrew our acceptance with %s" % label

    if what in ("withdraw", "remove", "remove_clause"):
        specs = order.get("clauses")
        if specs is None:
            raise OrderError("say which 'clauses' to take off the table")
        if not isinstance(specs, list):
            specs = [specs]
        if treaty is None:
            raise OrderError("no meeting with %s" % label)
        for spec in specs:
            giver, ctype, value = _clause(client, other, spec)
            client.remove_clause(other, giver, ctype, value)
        return "took %d clause(s) off the table with %s" % (len(specs), label)

    if what in ("cancel_meeting", "cancel", "close", "walk_away"):
        if treaty is None:
            return "no meeting with %s to cancel" % label
        client.cancel_meeting(other)
        return "walked out of the meeting with %s" % label

    if what in ("break", "break_treaty", "cancel_pact", "declare_war"):
        ds = game.diplstate(other)
        now = state.DIPLSTATE_NAMES.get(ds.get("type")) if ds else "no contact"
        client.cancel_pact(other)
        return ("broke our %s with %s -- one step down (alliance -> peace "
                "-> war)" % (now, label))

    if what in ("stop_vision", "cancel_vision", "stop_shared_vision"):
        client.cancel_pact(other, state.CLAUSE_VISION)
        return "stopped giving %s shared vision" % label

    if what in ("stop_shared_tiles", "cancel_shared_tiles"):
        client.cancel_pact(other, state.CLAUSE_SHARED_TILES)
        return "stopped sharing tiles with %s" % label

    raise OrderError("unknown diplomacy action %r (meet, offer, accept, "
                     "withdraw, cancel_meeting, break, stop_vision)" % what)


def _player_order(client, order):
    game = client.game

    if "research" in order:
        tid, tech = _lookup(game.ruleset.techs, order["research"], "tech")
        # The server drops a request whose prerequisites are not met without
        # saying so, and a turn of research goes with it.
        _check_researchable(game, tid, tech)
        client.set_research(tid)
        return "sent research %s" % tech.get("name")

    if "research_goal" in order:
        tid, tech = _lookup(game.ruleset.techs, order["research_goal"], "tech")
        if _invention(game, tid) == KNOWN:
            raise OrderError("we already know %s" % tech.get("name"))
        client.set_research_goal(tid)
        return "sent research goal %s" % tech.get("name")

    if "rates" in order:
        r = order["rates"]
        tax, lux, sci = r.get("tax", 0), r.get("luxury", 0), r.get("science", 0)
        if tax + lux + sci != 100:
            raise OrderError("rates must add up to 100, got %d" %
                             (tax + lux + sci))
        client.set_rates(tax, lux, sci)
        return "sent rates tax/lux/sci %d/%d/%d" % (tax, lux, sci)

    if "government" in order:
        gid, gov = _lookup(game.ruleset.governments, order["government"],
                           "government")
        client.change_government(gid)
        return "sent revolution towards %s" % gov.get("name")

    if "chat" in order:
        client.chat(str(order["chat"]))
        return "said: %s" % order["chat"]

    raise OrderError("unrecognised order %r" % (sorted(order),))


def apply_orders(client, orders):
    """Run every order, and return a line about each.

    One bad order does not stop the rest: the result says which failed, and
    that report goes into the next observation.
    """
    if not isinstance(orders, list):
        raise OrderError("orders must be a list, got %s" % type(orders).__name__)
    results = []
    for order in orders:
        if not isinstance(order, dict):
            results.append("SKIPPED %r: each order must be an object" % (order,))
            continue
        try:
            if "unit" in order:
                results.append(_unit_order(client, order))
            elif "diplomacy" in order:
                results.append(_diplomacy_order(client, order))
            elif "city" in order:
                results.append(_city_order(client, order))
            else:
                results.append(_player_order(client, order))
        except OrderError as exc:
            results.append("FAILED %s" % exc)
        except Exception as exc:            # a bad id should not kill the turn
            results.append("FAILED %r: %s" % (order, exc))
    return results
