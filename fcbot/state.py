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

# enum diplstate_type (common/player.h)
DIPLSTATE_NAMES = {
    0: "armistice", 1: "war", 2: "ceasefire", 3: "peace",
    4: "alliance", 5: "no contact", 6: "team",
}

DS_ARMISTICE, DS_WAR, DS_CEASEFIRE, DS_PEACE = 0, 1, 2, 3
DS_ALLIANCE, DS_NO_CONTACT, DS_TEAM = 4, 5, 6

# enum clause_type (common/diptreaty.h) -- what one side puts on the table.
CLAUSE_ADVANCE = 0
CLAUSE_GOLD = 1
CLAUSE_MAP = 2
CLAUSE_SEAMAP = 3
CLAUSE_CITY = 4
CLAUSE_CEASEFIRE = 5
CLAUSE_PEACE = 6
CLAUSE_ALLIANCE = 7
CLAUSE_VISION = 8
CLAUSE_EMBASSY = 9
CLAUSE_SHARED_TILES = 10

CLAUSE_NAMES = {
    CLAUSE_ADVANCE: "advance",
    CLAUSE_GOLD: "gold",
    CLAUSE_MAP: "map",
    CLAUSE_SEAMAP: "seamap",
    CLAUSE_CITY: "city",
    CLAUSE_CEASEFIRE: "ceasefire",
    CLAUSE_PEACE: "peace",
    CLAUSE_ALLIANCE: "alliance",
    CLAUSE_VISION: "vision",
    CLAUSE_EMBASSY: "embassy",
    CLAUSE_SHARED_TILES: "shared_tiles",
}

#: The three clauses that set the diplomatic state itself. A treaty holds at
#: most one of them: adding a second replaces the first (common/diptreaty.c).
PACT_CLAUSES = frozenset([CLAUSE_CEASEFIRE, CLAUSE_PEACE, CLAUSE_ALLIANCE])

#: TILE_INFO's `resource` carries this when the tile has none.
NO_RESOURCE = 250        # MAX_EXTRA_TYPES


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
        self.specialists = {}
        self.control = {}
        self.terrain_control = {}
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


class Treaty(object):
    """One diplomatic meeting in progress, mirrored from the server.

    The server owns the real treaty; every INIT/CREATE/REMOVE/ACCEPT packet
    is a report of what it now holds, so this only has to apply the same
    edits the client library does (client/clitreaty.c -> common/diptreaty.c).
    """

    def __init__(self, counterpart, initiated_from=None):
        self.counterpart = counterpart
        self.initiated_from = initiated_from
        self.clauses = []            # [{"giver": plr, "type": int, "value": int}]
        self.i_accepted = False
        self.other_accepted = False

    @property
    def they_started_it(self):
        return self.initiated_from == self.counterpart

    def add_clause(self, giver, type_, value):
        """Mirror common/diptreaty.c add_clause(); any change clears both
        acceptances, which is why accepting has to come after the offer."""
        for clause in self.clauses:
            if (clause["type"] == type_ and clause["giver"] == giver
                    and clause["value"] == value):
                return False         # already on the table
            if type_ in PACT_CLAUSES and clause["type"] in PACT_CLAUSES:
                clause["type"] = type_
                self._unaccept()
                return True
            if (type_ == CLAUSE_GOLD and clause["type"] == CLAUSE_GOLD
                    and clause["giver"] == giver):
                clause["value"] = value
                self._unaccept()
                return True
        self.clauses.append({"giver": giver, "type": type_, "value": value})
        self._unaccept()
        return True

    def remove_clause(self, giver, type_, value):
        for i, clause in enumerate(self.clauses):
            if (clause["type"] == type_ and clause["giver"] == giver
                    and clause["value"] == value):
                del self.clauses[i]
                self._unaccept()
                return True
        return False

    def _unaccept(self):
        self.i_accepted = False
        self.other_accepted = False

    def __repr__(self):
        return "<Treaty with %s, %d clauses, accepted %s/%s>" % (
            self.counterpart, len(self.clauses),
            self.i_accepted, self.other_accepted)


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
        self.diplstates = {}     # (plr1, plr2) -> PLAYER_DIPLSTATE
        self.treaties = {}       # counterpart player number -> Treaty
        self.player_no = None
        self.conn_id = None
        self.turn = 0
        self.year = 0
        self.phase = None
        self.game_started = False
        self.game_info = {}      # the last GAME_INFO: food/shield box, rules
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

    # -- the two boxes a city fills ------------------------------------

    def granary_size(self, city_size):
        """Food needed to grow one more citizen (common/city.c
        city_granary_size). Sizes below `granary_num_inis` are listed
        outright; past that each citizen adds a fixed amount."""
        info = self.game_info
        inis = info.get("granary_food_ini") or []
        num_inis = info.get("granary_num_inis", len(inis))
        if city_size <= 0 or not inis or num_inis <= 0:
            return None
        if city_size > num_inis:
            base = inis[num_inis - 1]
            base += info.get("granary_food_inc", 0) * (city_size - num_inis)
        else:
            base = inis[city_size - 1]
        return max(base * info.get("foodbox", 100) // 100, 1)

    def build_shield_cost(self, kind, value):
        """Shields to finish something, with the game's shieldbox applied.

        Ruleset costs are the unscaled ones; `shieldbox` is a game setting
        that scales every build (common/improvement.c, common/unittype.c).
        Per-city effects can still shift it, so this is the base figure.
        """
        if kind == VUT_UTYPE:
            base = self.ruleset.units.get(value, {}).get("build_cost")
        elif kind == VUT_IMPROVEMENT:
            base = self.ruleset.buildings.get(value, {}).get("build_cost")
        else:
            base = None
        if base is None:
            return None
        return max(base * self.game_info.get("shieldbox", 100) // 100, 1)

    def pop_cost(self, unit_type_id):
        """Citizens a unit costs the city that builds it -- 2 for Settlers
        in the classic ruleset, which is why a size-2 city cannot build one."""
        return self.ruleset.units.get(unit_type_id, {}).get("pop_cost", 0)

    def worked_tiles(self, city_id):
        """Tile indices this city has citizens working, from TILE_INFO."""
        return sorted(i for i, t in self.tiles.items()
                      if t.get("worked") == city_id)

    def tile_output(self, index):
        """Base food/shield/trade of a tile: terrain plus any resource.

        This is the ruleset's flat figure. What a city actually collects
        also depends on extras, government penalties and effects, so treat
        it as the ordering, not the number.
        """
        tile = self.tiles.get(index)
        if tile is None or tile.get("known") == TILE_UNKNOWN:
            return None
        terrain = self.ruleset.terrains.get(tile.get("terrain"))
        if terrain is None:
            return None
        out = list((terrain.get("output") or [0, 0, 0])[:3])
        resource = self.ruleset.resources.get(tile.get("resource"))
        if resource:
            for i, extra in enumerate((resource.get("output") or [])[:3]):
                out[i] += extra
        return {"food": out[0], "shield": out[1], "trade": out[2]}

    @property
    def move_fragments(self):
        """Move points per tile step; movesleft is counted in these."""
        return self.ruleset.terrain_control.get("move_fragments", 1) or 1

    def diplstate(self, other, me=None):
        """Our diplomatic state toward another player, as a dict or None."""
        me = self.player_no if me is None else me
        return (self.diplstates.get((me, other))
                or self.diplstates.get((other, me)))

    def can_meet(self, other):
        """Whether a meeting with `other` is possible at all: an embassy
        either way, or contact still in date (common/diptreaty.c
        could_meet_with_player)."""
        if other == self.player_no:
            return False
        p = self.players.get(other)
        if not p or not p.get("is_alive", True):
            return False
        if self.has_embassy_with(other) or self.gives_embassy_to(other):
            return True
        for pair in ((self.player_no, other), (other, self.player_no)):
            ds = self.diplstates.get(pair)
            if ds and ds.get("contact_turns_left", 0) > 0:
                return True
        return False

    def gives_embassy_to(self, other):
        """Whether `other` has an embassy with us."""
        p = self.players.get(other)
        if not p:
            return False
        return bool(p.get("real_embassy", 0) >> self.player_no & 1)

    def has_embassy_with(self, other):
        me = self.me
        if not me:
            return False
        return bool(me.get("real_embassy", 0) >> other & 1)

    def is_ai(self, player):
        """Whether a PLAYER_INFO belongs to a computer player."""
        return bool(player.get("flags", 0) & PLRF_AI)

    def chat_since(self, index=0):
        """Messages *another* person typed, from `index` onward.

        Our own chat comes back down the wire like everyone else's; echoing
        it back as something said to us is only confusing.
        """
        return [m for m in self.messages[index:]
                if m.is_chat and m.conn_id != self.conn_id]

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
    s.treaties.pop(v["playerno"], None)


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
    s.game_info = v
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
    "PACKET_RULESET_SPECIALIST": _make_ruleset_handler("specialists"),
}


def _h_ruleset_control(s, v):
    s.ruleset.control = v


def _h_ruleset_game(s, v):
    s.ruleset.game = v


def _h_terrain_control(s, v):
    s.ruleset.terrain_control = v


def _h_diplstate(s, v):
    s.diplstates[(v["plr1"], v["plr2"])] = v


# -- diplomatic meetings ------------------------------------------------
#
# The server reports every meeting to both sides, so all five packets are
# just edits to apply to our mirror of the treaty.

def _h_init_meeting(s, v):
    other = v["counterpart"]
    s.treaties[other] = Treaty(other, v.get("initiated_from"))


def _h_cancel_meeting(s, v):
    # Also how a *concluded* treaty ends: once both sides accept, the server
    # executes it and cancels the meeting (server/diplhand.c).
    s.treaties.pop(v["counterpart"], None)


def _h_create_clause(s, v):
    treaty = s.treaties.get(v["counterpart"])
    if treaty is not None:
        treaty.add_clause(v["giver"], v["type"], v["value"])


def _h_remove_clause(s, v):
    treaty = s.treaties.get(v["counterpart"])
    if treaty is not None:
        treaty.remove_clause(v["giver"], v["type"], v["value"])


def _h_accept_treaty(s, v):
    treaty = s.treaties.get(v["counterpart"])
    if treaty is not None:
        treaty.i_accepted = bool(v["I_accepted"])
        treaty.other_accepted = bool(v["other_accepted"])


_HANDLERS["PACKET_RULESET_CONTROL"] = _h_ruleset_control
_HANDLERS["PACKET_RULESET_GAME"] = _h_ruleset_game
_HANDLERS["PACKET_RULESET_TERRAIN_CONTROL"] = _h_terrain_control
_HANDLERS["PACKET_PLAYER_DIPLSTATE"] = _h_diplstate
_HANDLERS["PACKET_DIPLOMACY_INIT_MEETING"] = _h_init_meeting
_HANDLERS["PACKET_DIPLOMACY_CANCEL_MEETING"] = _h_cancel_meeting
_HANDLERS["PACKET_DIPLOMACY_CREATE_CLAUSE"] = _h_create_clause
_HANDLERS["PACKET_DIPLOMACY_REMOVE_CLAUSE"] = _h_remove_clause
_HANDLERS["PACKET_DIPLOMACY_ACCEPT_TREATY"] = _h_accept_treaty
