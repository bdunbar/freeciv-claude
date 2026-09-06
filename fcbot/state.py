"""Game state assembled from the server's packet stream.

Everything here is fog-of-war limited: the server only sends what our
player can see, so this state is exactly what a human client would show.
"""

from . import chat as chatmod
from . import fcmap

# player_num sentinel meaning "no player attached": the slot count itself.
NO_PLAYER = 512

# enum known_type (common/tile.h)
TILE_UNKNOWN, TILE_KNOWN_UNSEEN, TILE_KNOWN_SEEN = 0, 1, 2

# enum unit_activity (common/fc_types.h; values are wire-visible).
# 3.2 renumbered this: ACTIVITY_POLLUTION/FALLOUT collapsed into
# ACTIVITY_CLEAN, and ACTIVITY_CULTIVATE / ACTIVITY_PLANT replaced the old
# "irrigate/mine changes terrain" overloading.
ACTIVITY_IDLE = 0
ACTIVITY_CULTIVATE = 1
ACTIVITY_MINE = 2
ACTIVITY_IRRIGATE = 3
ACTIVITY_FORTIFIED = 4
ACTIVITY_SENTRY = 5
ACTIVITY_PILLAGE = 6
ACTIVITY_GOTO = 7
ACTIVITY_EXPLORE = 8
ACTIVITY_TRANSFORM = 9
ACTIVITY_FORTIFYING = 10
ACTIVITY_CLEAN = 11
ACTIVITY_BASE = 12
ACTIVITY_GEN_ROAD = 13
ACTIVITY_CONVERT = 14
ACTIVITY_PLANT = 15

# enum unit_orders (common/unit.h). 3.2 folded the per-purpose orders
# (build city, disband, trade route, ...) into ORDER_PERFORM_ACTION plus an
# action id from enum gen_action.
ORDER_MOVE = 0
ORDER_ACTIVITY = 1
ORDER_FULL_MP = 2
ORDER_ACTION_MOVE = 3
ORDER_PERFORM_ACTION = 4

# enum gen_action (common/actions.h); only the ones we issue.
ACTION_FOUND_CITY = 27
ACTION_JOIN_CITY = 28
ACTION_DISBAND_UNIT = 39
ACTION_HOME_CITY = 40
ACTION_FORTIFY = 61
ACTION_CULTIVATE = 62
ACTION_PLANT = 63
ACTION_TRANSFORM_TERRAIN = 64
ACTION_ROAD = 65
ACTION_IRRIGATE = 66
ACTION_MINE = 67
ACTION_BASE = 68
ACTION_PILLAGE = 69
ACTION_CLEAN = 119

# bv_plr_flags bits (enum plr_flag_id). 3.2 moved PLAYER_INFO's `ai`
# boolean into this flag vector.
PLRF_AI = 1 << 0
PLRF_SCENARIO_RESERVED = 1 << 1
PLRF_FIRST_CITY = 1 << 2

# enum server_side_agent (common/unit.h): what the server drives for us.
SSA_NONE = 0
SSA_AUTOSETTLER = 1
SSA_AUTOEXPLORE = 2

# universals_n kinds used by city production
VUT_IMPROVEMENT = 3
VUT_UTYPE = 6


class Ruleset(object):
    """Static game rules, learned from the RULESET_* packet stream."""

    def __init__(self):
        self.units = {}
        self.techs = {}
        self.buildings = {}
        self.terrains = {}
        self.governments = {}
        self.nations = {}
        self.extras = {}
        self.resources = {}
        self.control = {}
        self.game = {}

    # name lookups are how the agent refers to things
    def unit_by_name(self, name):
        for u in self.units.values():
            if u["rule_name"] == name or u["name"] == name:
                return u
        return None

    def building_by_name(self, name):
        for b in self.buildings.values():
            if b["rule_name"] == name or b["name"] == name:
                return b
        return None

    def tech_by_name(self, name):
        for t in self.techs.values():
            if t["rule_name"] == name or t["name"] == name:
                return t
        return None

    def terrain_name(self, tid):
        t = self.terrains.get(tid)
        return t["rule_name"] if t else "?"

    def unit_name(self, tid):
        u = self.units.get(tid)
        return u["rule_name"] if u else "?"


class GameState(object):
    def __init__(self):
        self.ruleset = Ruleset()
        self.topo = None
        self.tiles = {}          # tile index -> last TILE_INFO
        self.units = {}          # unit id -> UNIT_INFO (ours, detailed)
        self.short_units = {}    # unit id -> UNIT_SHORT_INFO (foreign)
        self.cities = {}         # city id -> CITY_INFO (ours)
        self.short_cities = {}   # city id -> CITY_SHORT_INFO (foreign)
        self.players = {}        # player number -> PLAYER_INFO
        self.research = {}       # research id -> RESEARCH_INFO
        self.conns = {}
        self.player_no = None
        self.conn_id = None
        self.turn = 0
        self.year = 0
        self.phase = None
        self.game_started = False
        self.messages = []       # every Message, chat and notification alike

    # -- convenience ---------------------------------------------------
    @property
    def me(self):
        return self.players.get(self.player_no)

    @property
    def my_research(self):
        me = self.me
        if me is None:
            return None
        # Without teams, research id == player number.
        return self.research.get(me.get("playerno", self.player_no))

    def my_units(self):
        return {uid: u for uid, u in self.units.items()
                if u["owner"] == self.player_no}

    def my_cities(self):
        return {cid: c for cid, c in self.cities.items()
                if c["owner"] == self.player_no}

    def foreign_units(self):
        out = {}
        for uid, u in self.short_units.items():
            if u["owner"] != self.player_no:
                out[uid] = u
        for uid, u in self.units.items():
            if u["owner"] != self.player_no:
                out[uid] = u
        return out

    def foreign_cities(self):
        out = {}
        for cid, c in self.short_cities.items():
            if c["owner"] != self.player_no:
                out[cid] = c
        for cid, c in self.cities.items():
            if c["owner"] != self.player_no:
                out[cid] = c
        return out

    def is_ai(self, player):
        """Whether a PLAYER_INFO belongs to a computer player."""
        return bool(player.get("flags", 0) & PLRF_AI)

    def chat_since(self, index=0):
        """Messages a person typed, from `index` onward."""
        return [m for m in self.messages[index:] if m.is_chat]

    def events_since(self, index=0):
        """Game notifications (city lost, tech learned, ...) from `index` on."""
        return [m for m in self.messages[index:] if not m.is_chat]

    def tile(self, index):
        return self.tiles.get(index)

    def is_known(self, index):
        t = self.tiles.get(index)
        return t is not None and t["known"] != TILE_UNKNOWN

    def terrain_at(self, index):
        t = self.tiles.get(index)
        if t is None:
            return None
        return self.ruleset.terrains.get(t["terrain"])

    def units_at(self, index):
        out = [u for u in self.units.values() if u["tile"] == index]
        out += [u for u in self.short_units.values()
                if u["tile"] == index and u["id"] not in self.units]
        return out

    def city_at(self, index):
        for c in self.cities.values():
            if c["tile"] == index:
                return c
        for c in self.short_cities.values():
            if c["tile"] == index:
                return c
        return None

    # -- packet dispatch -----------------------------------------------
    def handle(self, name, v):
        fn = _HANDLERS.get(name)
        if fn:
            fn(self, v)


# -- individual handlers ------------------------------------------------

def _h_map_info(s, v):
    # 3.2 split wrapping out of topology_id into its own wrap_id.
    s.topo = fcmap.Topology(v["xsize"], v["ysize"], v["topology_id"],
                            v.get("wrap_id", 0))


def _h_tile_info(s, v):
    s.tiles[v["tile"]] = v


def _h_unit_info(s, v):
    s.units[v["id"]] = v
    s.short_units.pop(v["id"], None)


def _h_unit_short_info(s, v):
    # Never let a short info overwrite the detailed info for our own unit.
    if v["id"] not in s.units:
        s.short_units[v["id"]] = v


def _h_unit_remove(s, v):
    s.units.pop(v["unit_id"], None)
    s.short_units.pop(v["unit_id"], None)


def _h_city_info(s, v):
    s.cities[v["id"]] = v
    s.short_cities.pop(v["id"], None)


def _h_city_short_info(s, v):
    if v["id"] not in s.cities:
        s.short_cities[v["id"]] = v


def _h_city_remove(s, v):
    s.cities.pop(v["city_id"], None)
    s.short_cities.pop(v["city_id"], None)


def _h_player_info(s, v):
    s.players[v["playerno"]] = v


def _h_player_remove(s, v):
    s.players.pop(v["playerno"], None)


def _h_research_info(s, v):
    s.research[v["id"]] = v


def _h_conn_info(s, v):
    s.conns[v["id"]] = v
    if s.conn_id is not None and v["id"] == s.conn_id:
        pn = v["player_num"]
        s.player_no = None if pn is None or pn >= NO_PLAYER else pn


def _h_start_phase(s, v):
    s.phase = v["phase"]


def _h_new_year(s, v):
    s.year = v.get("year", s.year)
    s.turn = v["turn"]


def _h_begin_turn(s, v):
    pass


def _h_game_info(s, v):
    s.turn = v.get("turn", s.turn)
    s.year = v.get("year", s.year)
    s.game_started = v.get("is_new_game") is False or s.game_started


def _h_chat(s, v):
    s.messages.append(chatmod.from_packet(v, s.conns, s.conn_id))
    if len(s.messages) > 1000:
        del s.messages[:500]


def _make_ruleset_handler(table, key="id"):
    def handler(s, v):
        getattr(s.ruleset, table)[v[key]] = v
    return handler


_HANDLERS = {
    "PACKET_MAP_INFO": _h_map_info,
    "PACKET_TILE_INFO": _h_tile_info,
    "PACKET_UNIT_INFO": _h_unit_info,
    "PACKET_UNIT_SHORT_INFO": _h_unit_short_info,
    "PACKET_UNIT_REMOVE": _h_unit_remove,
    "PACKET_CITY_INFO": _h_city_info,
    "PACKET_CITY_SHORT_INFO": _h_city_short_info,
    "PACKET_CITY_REMOVE": _h_city_remove,
    "PACKET_PLAYER_INFO": _h_player_info,
    "PACKET_PLAYER_REMOVE": _h_player_remove,
    "PACKET_RESEARCH_INFO": _h_research_info,
    "PACKET_CONN_INFO": _h_conn_info,
    "PACKET_START_PHASE": _h_start_phase,
    "PACKET_NEW_YEAR": _h_new_year,
    "PACKET_BEGIN_TURN": _h_begin_turn,
    "PACKET_GAME_INFO": _h_game_info,
    "PACKET_CHAT_MSG": _h_chat,
    "PACKET_RULESET_UNIT": _make_ruleset_handler("units"),
    "PACKET_RULESET_TECH": _make_ruleset_handler("techs"),
    "PACKET_RULESET_BUILDING": _make_ruleset_handler("buildings"),
    "PACKET_RULESET_TERRAIN": _make_ruleset_handler("terrains"),
    "PACKET_RULESET_GOVERNMENT": _make_ruleset_handler("governments"),
    "PACKET_RULESET_NATION": _make_ruleset_handler("nations"),
    "PACKET_RULESET_EXTRA": _make_ruleset_handler("extras"),
    "PACKET_RULESET_RESOURCE": _make_ruleset_handler("resources"),
}


def _h_ruleset_control(s, v):
    s.ruleset.control = v


def _h_ruleset_game(s, v):
    s.ruleset.game = v


_HANDLERS["PACKET_RULESET_CONTROL"] = _h_ruleset_control
_HANDLERS["PACKET_RULESET_GAME"] = _h_ruleset_game
