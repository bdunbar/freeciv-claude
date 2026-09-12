"""Standing orders, and the exceptions that need a decision.

The model is an intermittent player: between invocations it is not slow,
it is absent. Waking it once per turn spends nearly every wakeup confirming
that nothing happened. So instead it writes a *policy* -- what to build,
what to research, what units of each kind are for, what treaties to sign --
and this plays forward under that policy until something happens that the
policy does not cover. Then it stops and says why.

The division is deliberate and has to stay visible: the strategy here is
the model's, and the execution is a rules engine. Nothing in this file
decides anything the policy did not already say; where it would have to, it
raises an interrupt instead. That is what "conservative" means, and it is
the setting this was built for.

Orders come out in exactly the vocabulary `orders.py` already speaks, so
what autoplay did is readable in the same terms as what the model writes by
hand.
"""

from . import state

#: Interrupt kinds, and whether they are on by default.
DEFAULT_WAKE_ON = {
    "first_contact": True,
    "treaty_offered": True,
    "city_attacked_or_lost": True,
    "worklist_empty": True,
    "research_idle": True,
    "disorder_or_famine": True,
    "unit_without_orders": True,
    "enemy_within": 4,
    "every_n_turns": 10,
}


class Interrupt(object):
    """A reason autoplay stopped and wants a decision."""

    __slots__ = ("kind", "detail")

    def __init__(self, kind, detail):
        self.kind = kind
        self.detail = detail

    def as_dict(self):
        return {"kind": self.kind, "detail": self.detail}

    def __repr__(self):
        return "<Interrupt %s: %s>" % (self.kind, self.detail)


class Policy(object):
    """Standing orders. Anything absent here is an interrupt, not a guess."""

    def __init__(self, data=None):
        data = data or {}
        self.research = list(data.get("research") or [])
        self.government = data.get("government")
        self.rates = data.get("rates")
        self.cities = data.get("cities") or {}
        self.units = data.get("units") or {}
        self.diplomacy = data.get("diplomacy") or {}
        self.notes = data.get("notes", "")
        wake = dict(DEFAULT_WAKE_ON)
        wake.update(data.get("wake_on") or {})
        self.wake_on = wake

    def city_rule(self, name):
        return self.cities.get(name) or self.cities.get("default") or {}

    def unit_rule(self, type_name):
        return self.units.get(type_name) or self.units.get("*")

    def as_dict(self):
        return {"research": self.research, "government": self.government,
                "rates": self.rates, "cities": self.cities,
                "units": self.units, "diplomacy": self.diplomacy,
                "wake_on": self.wake_on, "notes": self.notes}


# -- interrupts ---------------------------------------------------------

def check_interrupts(game, policy, memory):
    """Everything about this turn that the policy does not cover.

    `memory` carries what autoplay knew last turn, so a *change* can be
    noticed -- a player met, a city lost -- rather than only a state.
    """
    out = []
    wake = policy.wake_on

    if wake.get("first_contact"):
        for pn, player in sorted(game.players.items()):
            if pn == game.player_no or not player.get("is_alive", True):
                continue
            if pn in memory.get("known_players", ()):
                continue
            if game.can_meet(pn):
                out.append(Interrupt(
                    "first_contact",
                    "%s of the %s can be negotiated with" %
                    (player.get("name"),
                     _nation(game, player))))

    if wake.get("treaty_offered"):
        for other, treaty in sorted(game.treaties.items()):
            if treaty.other_accepted and not treaty.i_accepted:
                out.append(Interrupt(
                    "treaty_offered",
                    "%s is waiting on us over %d clause(s)" %
                    (_player_name(game, other), len(treaty.clauses))))

    if wake.get("city_attacked_or_lost"):
        had = set(memory.get("city_ids", ()))
        now = set(game.my_cities())
        for lost in sorted(had - now):
            out.append(Interrupt("city_attacked_or_lost",
                                 "we no longer hold city %d" % lost))

    if wake.get("research_idle"):
        research = game.my_research or {}
        if research and not _next_tech(game, policy):
            out.append(Interrupt(
                "research_idle",
                "the research plan is finished or empty; nothing queued"))

    for cid, city in sorted(game.my_cities().items()):
        name = city.get("name")
        if wake.get("disorder_or_famine"):
            if city.get("anarchy", 0):
                out.append(Interrupt("disorder_or_famine",
                                     "%s is in disorder" % name))
            surplus = (city.get("surplus") or [0])[0]
            if surplus < 0:
                out.append(Interrupt("disorder_or_famine",
                                     "%s is starving (%+d food)"
                                     % (name, surplus)))
        if wake.get("worklist_empty"):
            rule = policy.city_rule(name)
            if not rule.get("build") and not city.get("worklist"):
                out.append(Interrupt(
                    "worklist_empty",
                    "%s has nothing queued and no build rule" % name))

    if wake.get("unit_without_orders"):
        for uid, unit in sorted(game.my_units().items()):
            type_name = _unit_type(game, unit)
            if policy.unit_rule(type_name) is None:
                out.append(Interrupt(
                    "unit_without_orders",
                    "%s #%d has no doctrine in the policy"
                    % (type_name, uid)))

    within = wake.get("enemy_within")
    if within:
        for unit in game.foreign_units().values():
            if not _is_hostile(game, unit["owner"]):
                continue
            near = _distance_to_our_cities(game, unit["tile"])
            if near is not None and near <= within:
                out.append(Interrupt(
                    "enemy_within",
                    "%s unit %d tiles from one of our cities"
                    % (_player_name(game, unit["owner"]), near)))
                break

    every = wake.get("every_n_turns")
    if every:
        last = memory.get("last_decision_turn", 0)
        if game.turn - last >= every:
            out.append(Interrupt("every_n_turns",
                                 "%d turns since the last decision"
                                 % (game.turn - last)))
    return out


def remember(game):
    """What the next turn needs in order to notice a change."""
    return {
        "known_players": sorted(pn for pn in game.players
                                if pn != game.player_no
                                and game.can_meet(pn)),
        "city_ids": sorted(game.my_cities()),
        "turn": game.turn,
    }


# -- carrying out the policy --------------------------------------------

def plan_turn(game, policy):
    """The orders the policy calls for this turn, in orders.py's vocabulary."""
    orders, notes = [], []
    _plan_research(game, policy, orders, notes)
    _plan_government(game, policy, orders, notes)
    _plan_cities(game, policy, orders, notes)
    _plan_units(game, policy, orders, notes)
    return orders, notes


def _plan_research(game, policy, orders, notes):
    research = game.my_research
    if not research:
        return
    wanted = _next_tech(game, policy)
    if wanted is None:
        return
    tech_id, tech = wanted
    if research.get("researching") == tech_id:
        return
    # Only set what is reachable now; the rest is what the goal is for.
    if _invention(game, tech_id) == "1":
        orders.append({"research": tech.get("rule_name") or tech.get("name")})
        notes.append("research -> %s" % (tech.get("name")))
    elif research.get("tech_goal") != tech_id:
        orders.append({"research_goal": tech.get("rule_name")
                       or tech.get("name")})
        notes.append("research goal -> %s" % (tech.get("name")))


def _plan_government(game, policy, orders, notes):
    if not policy.government:
        return
    me = game.me or {}
    if me.get("revolution_finishes", -1) > game.turn:
        return
    target = _government_by_name(game, policy.government)
    if target is None or me.get("government") == target[0]:
        return
    if me.get("target_government") == target[0]:
        return
    # Only revolt once the government is actually available to us.
    if not _government_available(game, target[0]):
        return
    orders.append({"government": policy.government})
    notes.append("revolution -> %s" % policy.government)


def _plan_cities(game, policy, orders, notes):
    for cid, city in sorted(game.my_cities().items()):
        rule = policy.city_rule(city.get("name"))
        build = rule.get("build") or []
        if build and not city.get("worklist"):
            current = (city.get("production_kind"), city.get("production_value"))
            wanted = _production_ids(game, build[0])
            if wanted and current != wanted:
                orders.append({"city": cid, "build": list(build[0])})
                notes.append("%s builds %s" % (city.get("name"), build[0][1]))
            if len(build) > 1:
                orders.append({"city": cid,
                               "worklist": [list(b) for b in build[1:]]})
        swap = _tile_swap(game, city, rule.get("tiles"))
        if swap:
            drop, take = swap
            orders.append({"city": cid, "stop_working": list(drop)})
            orders.append({"city": cid, "work_tile": list(take)})
            notes.append("%s works %s instead of %s (%s bias)"
                         % (city.get("name"), take, drop, rule.get("tiles")))


def _tile_swap(game, city, bias):
    """One swap toward the bias, or None. One at a time on purpose: the
    server rearranges citizens itself, and a pile of reassignments in one
    turn is hard to read and easy to get wrong."""
    if bias not in ("food", "shield", "shields", "trade"):
        return None
    key = {"shields": "shield"}.get(bias, bias)
    worked, free = [], []
    radius_sq = city.get("city_radius_sq", 5)
    for index, tile in game.tiles.items():
        if index == city["tile"]:
            continue
        if game.topo.sq_distance(city["tile"], index) > radius_sq:
            continue
        output = game.tile_output(index)
        if output is None:
            continue
        if tile.get("worked") == city["id"]:
            worked.append((output[key], index))
        elif not tile.get("worked"):
            free.append((output[key], index))
    if not worked or not free:
        return None
    worst = min(worked)
    best = max(free)
    if best[0] <= worst[0]:
        return None
    return (list(game.topo.index_to_map(worst[1])),
            list(game.topo.index_to_map(best[1])))


def _plan_units(game, policy, orders, notes):
    for uid, unit in sorted(game.my_units().items()):
        rule = policy.unit_rule(_unit_type(game, unit))
        if rule is None:
            continue                        # already an interrupt
        if unit.get("has_orders") or unit.get("ssa_controller"):
            continue                        # already busy
        if rule in ("explore", "auto_worker", "fortify", "sentry"):
            if _activity_name(unit) == rule:
                continue
            orders.append({"unit": uid, "activity": rule})
            notes.append("%s #%d: %s" % (_unit_type(game, unit), uid, rule))
        elif rule == "hold":
            continue


# -- small helpers ------------------------------------------------------

def _next_tech(game, policy):
    for name in policy.research:
        found = _tech_by_name(game, name)
        if found is None:
            continue
        tech_id, tech = found
        if _invention(game, tech_id) != "2":
            return tech_id, tech
    return None


def _invention(game, tech_id):
    inventions = (game.my_research or {}).get("inventions") or ""
    return inventions[tech_id] if 0 <= tech_id < len(inventions) else "0"


def _tech_by_name(game, name):
    wanted = str(name).lower()
    for tid, tech in game.ruleset.techs.items():
        if wanted in (str(tech.get("rule_name", "")).lower(),
                      str(tech.get("name", "")).lower()):
            return tid, tech
    return None


def _government_by_name(game, name):
    wanted = str(name).lower()
    for gid, gov in game.ruleset.governments.items():
        if wanted in (str(gov.get("rule_name", "")).lower(),
                      str(gov.get("name", "")).lower()):
            return gid, gov
    return None


def _government_available(game, gov_id):
    """Governments are gated on a tech; we only read that requirement."""
    gov = game.ruleset.governments.get(gov_id) or {}
    for req in (gov.get("reqs") or [])[:gov.get("reqs_count", 0)]:
        if req.get("type") != 1:            # VUT_ADVANCE
            return False
        if _invention(game, req.get("value", -1)) != "2":
            return False
    return True


def _production_ids(game, spec):
    kind, name = spec
    table = (game.ruleset.units if str(kind).lower().startswith("unit")
             else game.ruleset.buildings)
    wanted = str(name).lower()
    for key, item in table.items():
        if wanted in (str(item.get("rule_name", "")).lower(),
                      str(item.get("name", "")).lower()):
            return ((state.VUT_UTYPE
                     if str(kind).lower().startswith("unit")
                     else state.VUT_IMPROVEMENT), key)
    return None


def _unit_type(game, unit):
    utype = game.ruleset.units.get(unit["type"]) or {}
    return utype.get("rule_name") or utype.get("name") or "?"


def _activity_name(unit):
    if unit.get("ssa_controller") == state.SSA_AUTOEXPLORE:
        return "explore"
    if unit.get("ssa_controller") == state.SSA_AUTOSETTLER:
        return "auto_worker"
    if unit.get("activity") in (state.ACTIVITY_FORTIFIED,
                                state.ACTIVITY_FORTIFYING):
        return "fortify"
    if unit.get("activity") == state.ACTIVITY_SENTRY:
        return "sentry"
    return None


def _player_name(game, pn):
    player = game.players.get(pn)
    return player.get("name") if player else "player %s" % pn


def _nation(game, player):
    nation = game.ruleset.nations.get(player.get("nation")) or {}
    return nation.get("adjective") or nation.get("rule_name") or "?"


def _is_hostile(game, owner):
    ds = game.diplstate(owner)
    return not ds or ds.get("type") in (state.DS_WAR, state.DS_NO_CONTACT)


def _distance_to_our_cities(game, index):
    cities = game.my_cities().values()
    if not cities:
        return None
    return min(game.topo.real_distance(index, c["tile"]) for c in cities)
