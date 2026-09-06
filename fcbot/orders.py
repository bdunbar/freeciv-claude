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
        done.append("build %s" % order["build"][1])

    if "worklist" in order:
        entries = [_production(game, spec) for spec in order["worklist"]]
        client.set_worklist(cid, entries)
        done.append("worklist of %d" % len(entries))

    if order.get("buy"):
        client.buy_production(cid)
        done.append("buy for %s gold" % city.get("buy_cost"))

    if not done:
        raise OrderError("city %d: nothing to do (build, worklist, buy)" % cid)
    return "city %s: sent %s" % (city.get("name", cid), ", ".join(done))


def _player_order(client, order):
    game = client.game

    if "research" in order:
        tid, tech = _lookup(game.ruleset.techs, order["research"], "tech")
        client.set_research(tid)
        return "sent research %s" % tech.get("name")

    if "research_goal" in order:
        tid, tech = _lookup(game.ruleset.techs, order["research_goal"], "tech")
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
            elif "city" in order:
                results.append(_city_order(client, order))
            else:
                results.append(_player_order(client, order))
        except OrderError as exc:
            results.append("FAILED %s" % exc)
        except Exception as exc:            # a bad id should not kill the turn
            results.append("FAILED %r: %s" % (order, exc))
    return results
