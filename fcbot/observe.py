"""Everything we can see, shaped for a reader rather than for a program.

The server already limits this to what our player knows -- unexplored tiles
and units outside our vision simply are not in `GameState` -- so nothing
here needs to filter for fog of war. What it does is turn a pile of decoded
packets into something worth reading: named things instead of ids, and a map
you can actually look at instead of a list of tile dictionaries.
"""

from . import state

#: Cap on the empire overview's radius, in tiles.
MAX_OVERVIEW_RADIUS = 22
#: How much of the map around a city its local view covers.
CITY_VIEW_RADIUS = 3


# -- naming -------------------------------------------------------------

def _name(table, key, default="?"):
    item = table.get(key)
    if not item:
        return default
    return item.get("rule_name") or item.get("name") or default


def terrain_letters(ruleset):
    """One distinct letter per terrain, so the map render stays one char
    wide whatever ruleset is loaded."""
    letters = {}
    used = set()
    for tid, terrain in sorted(ruleset.terrains.items()):
        rule = terrain.get("rule_name") or terrain.get("name") or "?"
        for candidate in list(rule.lower()) + list("abcdefghijklmnopqrstuvwxyz"):
            if candidate.isalpha() and candidate not in used:
                used.add(candidate)
                letters[tid] = candidate
                break
        else:
            letters[tid] = "?"
    return letters


def production_name(game, kind, value):
    if kind == state.VUT_UTYPE:
        return "unit:" + _name(game.ruleset.units, value)
    if kind == state.VUT_IMPROVEMENT:
        return "improvement:" + _name(game.ruleset.buildings, value)
    return "kind%d:%d" % (kind, value)


def production_cost(game, kind, value):
    if kind == state.VUT_UTYPE:
        return game.ruleset.units.get(value, {}).get("build_cost")
    if kind == state.VUT_IMPROVEMENT:
        return game.ruleset.buildings.get(value, {}).get("build_cost")
    return None


# -- map ----------------------------------------------------------------

class MapView(object):
    """An ASCII window on the map, in map coordinates.

    Map coordinates are the ones the eight movement directions operate on,
    so north really is up and east really is right; on isometric maps the
    tile *indices* are laid out differently and would not render sensibly.
    """

    def __init__(self, game, center, radius):
        self.game = game
        self.center = center
        self.radius = radius
        self.letters = terrain_letters(game.ruleset)

    def _river_extras(self):
        out = set()
        for eid, extra in self.game.ruleset.extras.items():
            rule = (extra.get("rule_name") or "").lower()
            if "river" in rule:
                out.add(eid)
        return out

    def cell(self, index):
        """(terrain char, marker char) for one tile."""
        game = self.game
        if index is None:
            return "  "
        tile = game.tiles.get(index)
        if tile is None or tile["known"] == state.TILE_UNKNOWN:
            return "? "
        char = self.letters.get(tile["terrain"], "?")

        # Markers are uppercase or punctuation and terrain letters are
        # lowercase, so the two halves of a cell can never be confused.
        city = game.city_at(index)
        if city is not None:
            return char + ("C" if city["owner"] == game.player_no else "X")
        units = game.units_at(index)
        if units:
            mine = any(u["owner"] == game.player_no for u in units)
            return char + ("U" if mine else "E")
        if tile.get("resource", state.NO_RESOURCE) != state.NO_RESOURCE:
            return char + "*"
        if tile.get("extras", 0) & self._rivers:
            return char + "~"
        return char + " "

    def render(self):
        game = self.game
        topo = game.topo
        self._rivers = 0
        for eid in self._river_extras():
            self._rivers |= 1 << eid
        cx, cy = topo.index_to_map(self.center)
        r = self.radius
        lines = [self._header(cx, r)]
        for dy in range(-r, r + 1):
            row = "".join(self.cell(topo.map_to_index(cx + dx, cy + dy))
                          for dx in range(-r, r + 1))
            lines.append("%5d %s" % (cy + dy, row))
        return lines

    @staticmethod
    def _header(cx, r):
        """An x-axis ruler. Cells are two chars, so a label every five of
        them has ten columns to sit in and never gets clipped."""
        header = [" "] * (6 + 2 * (2 * r + 1))
        for i, dx in enumerate(range(-r, r + 1)):
            if dx % 5:
                continue
            label = str(cx + dx)
            at = 6 + 2 * i
            header[at:at + len(label)] = label
        return "".join(header).rstrip()

    def legend(self):
        rules = self.game.ruleset
        terrains = ", ".join(
            "%s=%s" % (self.letters[tid], _name(rules.terrains, tid))
            for tid in sorted(self.letters))
        return {
            "coordinates": "map (x, y); north is up, east is right",
            "cells": ("two chars per tile: a lowercase terrain letter, then "
                      "a marker -- C=our city X=foreign city U=our unit "
                      "E=foreign unit *=resource ~=river, or a space. "
                      "'? ' is unexplored and '  ' is off the map."),
            "terrain": terrains,
            "directions": self.game.topo.direction_names(),
        }


def _overview_radius(game, center):
    known = [i for i, t in game.tiles.items()
             if t["known"] != state.TILE_UNKNOWN]
    if not known:
        return 5
    reach = max(game.topo.real_distance(center, i) for i in known)
    return max(4, min(MAX_OVERVIEW_RADIUS, reach))


def _my_centre(game):
    cities = game.my_cities()
    if cities:
        return sorted(cities.values(), key=lambda c: c["id"])[0]["tile"]
    units = game.my_units()
    if units:
        return sorted(units.values(), key=lambda u: u["id"])[0]["tile"]
    return 0


# -- the observation ----------------------------------------------------

def _meta(game):
    me = game.me or {}
    gov = _name(game.ruleset.governments, me.get("government"), "?")
    revolution = me.get("revolution_finishes", -1)
    return {
        "turn": game.turn,
        "year": year_label(game.year),
        "gold": me.get("gold"),
        "rates": {"tax": me.get("tax"), "luxury": me.get("luxury"),
                  "science": me.get("science")},
        "government": gov,
        "in_revolution": revolution > game.turn,
        "revolution_finishes_turn": revolution if revolution > game.turn else None,
        "nation": _name(game.ruleset.nations, me.get("nation")),
        "leader": me.get("name"),
        "score": me.get("score"),
    }


def _research(game, agent_techs=None):
    r = game.my_research
    if not r:
        return None
    rules = game.ruleset
    inv = r.get("inventions") or ""

    def known(tid):
        return 0 <= tid < len(inv) and inv[tid] == "2"

    def researchable(tid):
        return 0 <= tid < len(inv) and inv[tid] == "1"

    return {
        "researching": _name(rules.techs, r.get("researching"), "nothing"),
        "bulbs": r.get("bulbs_researched"),
        "cost": r.get("researching_cost"),
        "bulbs_per_turn": r.get("total_bulbs_prod"),
        "goal": _name(rules.techs, r.get("tech_goal"), "none"),
        "known": sorted(_name(rules.techs, t) for t in rules.techs if known(t)),
        "researchable_now": sorted(_name(rules.techs, t)
                                   for t in rules.techs if researchable(t)),
    }


def _unit(game, u):
    topo = game.topo
    x, y = topo.index_to_map(u["tile"])
    frags = game.move_fragments
    out = {
        "id": u["id"],
        "type": _name(game.ruleset.units, u["type"]),
        "tile": u["tile"],
        "at": [x, y],
        "moves_left": round(u.get("movesleft", 0) / float(frags), 2),
        "hp": u.get("hp"),
        "veteran": u.get("veteran"),
        "activity": _activity_name(u.get("activity")),
        "done_moving": u.get("done_moving"),
    }
    if u.get("homecity"):
        city = game.cities.get(u["homecity"])
        out["home_city"] = city["name"] if city else u["homecity"]
    ssa = u.get("ssa_controller", state.SSA_NONE)
    if ssa == state.SSA_AUTOSETTLER:
        out["server_agent"] = "auto worker"
    elif ssa == state.SSA_AUTOEXPLORE:
        out["server_agent"] = "auto explore"
    if u.get("has_orders"):
        out["orders"] = {
            "steps_left": u.get("orders_length", 0) - u.get("orders_index", 0),
            "going_to": u.get("goto_tile"),
        }
    return out


_ACTIVITY_NAMES = {
    state.ACTIVITY_IDLE: "idle",
    state.ACTIVITY_CULTIVATE: "cultivating",
    state.ACTIVITY_MINE: "mining",
    state.ACTIVITY_IRRIGATE: "irrigating",
    state.ACTIVITY_FORTIFIED: "fortified",
    state.ACTIVITY_SENTRY: "sentry",
    state.ACTIVITY_PILLAGE: "pillaging",
    state.ACTIVITY_GOTO: "goto",
    state.ACTIVITY_EXPLORE: "exploring",
    state.ACTIVITY_TRANSFORM: "transforming",
    state.ACTIVITY_FORTIFYING: "fortifying",
    state.ACTIVITY_CLEAN: "cleaning",
    state.ACTIVITY_BASE: "building base",
    state.ACTIVITY_GEN_ROAD: "building road",
    state.ACTIVITY_CONVERT: "converting",
    state.ACTIVITY_PLANT: "planting",
}


def _activity_name(activity):
    return _ACTIVITY_NAMES.get(activity, "activity%s" % activity)


def _city(game, c):
    topo = game.topo
    x, y = topo.index_to_map(c["tile"])
    surplus = c.get("surplus") or [0] * 6
    kind, value = c.get("production_kind"), c.get("production_value")
    building = production_name(game, kind, value)
    cost = production_cost(game, kind, value)
    shields = c.get("shield_stock", 0)
    per_turn = surplus[1]
    if cost is None:
        turns = None
    elif shields >= cost:
        turns = 0
    elif per_turn > 0:
        turns = -(-(cost - shields) // per_turn)     # ceiling division
    else:
        turns = "never at this rate"

    built = c.get("improvements", 0)
    improvements = sorted(_name(game.ruleset.buildings, b)
                          for b in game.ruleset.buildings if built >> b & 1)
    garrison = [u["id"] for u in game.my_units().values()
                if u["tile"] == c["tile"]]
    return {
        "id": c["id"],
        "name": c.get("name"),
        "tile": c["tile"],
        "at": [x, y],
        "size": c.get("size"),
        "food": {"surplus": surplus[0], "stock": c.get("food_stock")},
        "shields": {"surplus": per_turn, "stock": shields},
        "trade": surplus[2],
        "building": building,
        "build_cost": cost,
        "turns_to_completion": turns,
        "buy_cost": c.get("buy_cost"),
        "worklist": [production_name(game, k, v)
                     for k, v in (c.get("worklist") or [])],
        "improvements": improvements,
        "garrison": garrison,
        "in_disorder": bool(c.get("anarchy", 0)),
        "celebrating": bool(c.get("rapture", 0)),
    }


def _foreign(game):
    my_cities = game.my_cities().values()

    def distance(index):
        if not my_cities:
            return None
        return min(game.topo.real_distance(index, c["tile"]) for c in my_cities)

    units = []
    for u in game.foreign_units().values():
        units.append({
            "id": u["id"],
            "type": _name(game.ruleset.units, u["type"]),
            "owner": _player_name(game, u["owner"]),
            "tile": u["tile"],
            "at": list(game.topo.index_to_map(u["tile"])),
            "distance_from_my_nearest_city": distance(u["tile"]),
        })
    cities = []
    for c in game.foreign_cities().values():
        cities.append({
            "id": c["id"],
            "name": c.get("name"),
            "owner": _player_name(game, c["owner"]),
            "size": c.get("size"),
            "tile": c["tile"],
            "at": list(game.topo.index_to_map(c["tile"])),
            "distance_from_my_nearest_city": distance(c["tile"]),
        })
    units.sort(key=lambda u: (u["distance_from_my_nearest_city"] is None,
                              u["distance_from_my_nearest_city"]))
    cities.sort(key=lambda c: (c["distance_from_my_nearest_city"] is None,
                               c["distance_from_my_nearest_city"]))
    return {"units": units, "cities": cities}


def _player_name(game, playerno):
    p = game.players.get(playerno)
    return p.get("name") if p else "player %s" % playerno


def _diplomacy(game):
    out = []
    for pn, p in sorted(game.players.items()):
        if pn == game.player_no or not p.get("is_alive", True):
            continue
        ds = game.diplstate(pn)
        out.append({
            "player": p.get("name"),
            "nation": _name(game.ruleset.nations, p.get("nation")),
            "is_ai": game.is_ai(p),
            "state": state.DIPLSTATE_NAMES.get(ds["type"], "unknown")
                     if ds else "no contact",
            "turns_left": ds.get("turns_left") if ds else None,
            "we_have_embassy": game.has_embassy_with(pn),
        })
    return out


def _messages(game, since):
    chat = [{"turn": m.turn, "from": m.speaker or m.sender, "text": m.text}
            for m in game.chat_since(since)]
    events = {}
    for m in game.events_since(since):
        events.setdefault(m.event_name, []).append(m.text)
    return chat, events


def observation(game, since_message=0):
    """The whole situation, as a JSON-serialisable dict."""
    centre = _my_centre(game)
    view = MapView(game, centre, _overview_radius(game, centre))
    chat, events = _messages(game, since_message)

    cities = [_city(game, c) for c in
              sorted(game.my_cities().values(), key=lambda c: c["id"])]
    city_views = {}
    for c in sorted(game.my_cities().values(), key=lambda c: c["id"]):
        local = MapView(game, c["tile"], CITY_VIEW_RADIUS)
        city_views[c["name"]] = local.render()

    return {
        "meta": _meta(game),
        "research": _research(game),
        "units": [_unit(game, u) for u in
                  sorted(game.my_units().values(), key=lambda u: u["id"])],
        "cities": cities,
        "map": {
            "legend": view.legend(),
            "centre_tile": centre,
            "overview": view.render(),
            "around_each_city": city_views,
        },
        "foreign": _foreign(game),
        "diplomacy": _diplomacy(game),
        "chat_since_last_turn": chat,
        "events_since_last_turn": events,
        "message_mark": len(game.messages),
    }


def year_label(year):
    return "%d BC" % -year if year < 0 else "%d AD" % year


# -- human-readable rendering -------------------------------------------

def to_text(obs):
    """The same observation as something to read in a terminal."""
    out = []
    m = obs["meta"]
    out.append("Turn %s, %s -- %s of the %s" %
               (m["turn"], m["year"], m["leader"], m["nation"]))
    out.append("  %s, %s gold, tax/lux/sci %s/%s/%s%s" %
               (m["government"], m["gold"], m["rates"]["tax"],
                m["rates"]["luxury"], m["rates"]["science"],
                ", IN REVOLUTION" if m["in_revolution"] else ""))
    r = obs.get("research")
    if r:
        out.append("  researching %s (%s/%s bulbs, +%s/turn), goal %s" %
                   (r["researching"], r["bulbs"], r["cost"],
                    r["bulbs_per_turn"], r["goal"]))

    out.append("")
    out.append("CITIES (%d)" % len(obs["cities"]))
    for c in obs["cities"]:
        out.append("  %-16s #%-4s size %-3s at %s  food %+d (%s)  shields %+d"
                   "  trade %d" %
                   (c["name"], c["id"], c["size"], c["at"],
                    c["food"]["surplus"], c["food"]["stock"],
                    c["shields"]["surplus"], c["trade"]))
        out.append("      building %s, %s turns left%s" %
                   (c["building"], c["turns_to_completion"],
                    "  IN DISORDER" if c["in_disorder"] else ""))
        if c["worklist"]:
            out.append("      then: %s" % ", ".join(c["worklist"]))

    out.append("")
    out.append("UNITS (%d)" % len(obs["units"]))
    for u in obs["units"]:
        extra = []
        if u.get("server_agent"):
            extra.append(u["server_agent"])
        if u.get("orders"):
            extra.append("%s steps left" % u["orders"]["steps_left"])
        out.append("  #%-4s %-12s at %-10s %s moves, %s%s" %
                   (u["id"], u["type"], u["at"], u["moves_left"],
                    u["activity"], (" [" + ", ".join(extra) + "]") if extra else ""))

    out.append("")
    out.append("MAP")
    for line in obs["map"]["overview"]:
        out.append("  " + line)
    legend = obs["map"]["legend"]
    out.append("  " + legend["cells"])
    out.append("  terrain: " + legend["terrain"])
    out.append("  " + legend["directions"])

    foreign = obs["foreign"]
    if foreign["cities"] or foreign["units"]:
        out.append("")
        out.append("FOREIGN")
        for c in foreign["cities"][:10]:
            out.append("  city %s (%s, size %s) at %s, %s tiles away" %
                       (c["name"], c["owner"], c["size"], c["at"],
                        c["distance_from_my_nearest_city"]))
        for u in foreign["units"][:10]:
            out.append("  unit %s (%s) at %s, %s tiles away" %
                       (u["type"], u["owner"], u["at"],
                        u["distance_from_my_nearest_city"]))

    if obs["diplomacy"]:
        out.append("")
        out.append("DIPLOMACY")
        for d in obs["diplomacy"]:
            out.append("  %-14s %-10s %s%s" %
                       (d["player"], d["nation"], d["state"],
                        "  (embassy)" if d["we_have_embassy"] else ""))

    if obs["chat_since_last_turn"]:
        out.append("")
        out.append("SAID TO YOU")
        for c in obs["chat_since_last_turn"]:
            out.append("  <%s> %s" % (c["from"], c["text"]))

    if obs["events_since_last_turn"]:
        out.append("")
        out.append("EVENTS")
        for name, texts in sorted(obs["events_since_last_turn"].items()):
            for t in texts[:4]:
                out.append("  %-26s %s" % (name, t))
    return "\n".join(out)
