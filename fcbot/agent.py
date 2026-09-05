"""Baseline playing agent.

It handles the per-turn micro (explore, settle, work, build, research) so a
game can run at a sane pace. Strategy knobs live in `Strategy` and are meant
to be adjusted between turns -- that is where higher-level direction enters.
"""

from . import fcpath, state

TECH_UNKNOWN, TECH_PREREQS_KNOWN, TECH_KNOWN = 0, 1, 2

REQ_ADVANCE = 1          # VUT_ADVANCE

# Rough build order for the classic ruleset, best first.
DEFAULT_BUILDINGS = [
    "Temple", "Granary", "Library", "Marketplace", "City Walls",
    "Harbour", "Aqueduct", "Colosseum", "Bank", "University",
]

DEFAULT_TECH_PATH = [
    "Bronze Working", "Alphabet", "Ceremonial Burial", "Pottery",
    "Writing", "Code of Laws", "Currency", "Monarchy", "Trade",
    "Literacy", "The Republic", "Philosophy", "Mathematics",
]


class Strategy(object):
    """Knobs a human (or Claude) can turn between turns."""

    def __init__(self):
        self.target_cities = 6
        self.min_city_distance = 3
        self.defenders_per_city = 1
        self.workers_per_city = 1
        self.tech_path = list(DEFAULT_TECH_PATH)
        self.building_priority = list(DEFAULT_BUILDINGS)
        self.science_rate = 60
        self.explore = True
        self.notes = []          # free-form, for reporting

    def describe(self):
        return ("cities<=%d science=%d%% next_tech=%s" %
                (self.target_cities, self.science_rate,
                 self.tech_path[0] if self.tech_path else "-"))


class Agent(object):
    def __init__(self, client, strategy=None, log=None):
        self.client = client
        self.game = client.game
        self.strategy = strategy or Strategy()
        self.log = log or (lambda *a: None)
        self.city_counter = 0
        self._settler_targets = {}

    # -- knowledge helpers ---------------------------------------------
    def tech_state(self, tech_id):
        r = self.game.my_research
        if not r:
            return TECH_UNKNOWN
        inv = r.get("inventions") or ""
        if tech_id < 0 or tech_id >= len(inv):
            return TECH_UNKNOWN
        try:
            return int(inv[tech_id]) - 0
        except ValueError:
            return TECH_UNKNOWN

    def knows_tech(self, tech_id):
        return self.tech_state(tech_id) == TECH_KNOWN

    def can_build_unit(self, utype):
        req = utype.get("tech_requirement", 0)
        # A_NONE (0) means no requirement in the classic ruleset.
        return req == 0 or self.knows_tech(req)

    def can_build_improvement(self, city, impr):
        built = city.get("improvements", 0)
        if built >> impr["id"] & 1:
            return False
        for req in impr.get("reqs", [])[:impr.get("reqs_count", 0)]:
            if req["type"] == REQ_ADVANCE and req["present"]:
                if not self.knows_tech(req["value"]):
                    return False
        return True

    def unit_role(self, unit):
        utype = self.game.ruleset.units.get(unit["type"], {})
        name = utype.get("rule_name", "")
        if name in ("Settlers", "Migrants"):
            return "settler"
        if name in ("Workers", "Engineers"):
            return "worker"
        if name == "Explorer":
            return "explorer"
        if utype.get("attack_strength", 0) > 0 or \
           utype.get("defense_strength", 0) > 0:
            return "military"
        return "other"

    # -- city siting -----------------------------------------------------
    def city_site_score(self, index, unit_class_id):
        game = self.game
        tile = game.tiles.get(index)
        if tile is None or tile["known"] == 0:
            return None
        terrain = game.ruleset.terrains.get(tile["terrain"])
        if terrain is None or not fcpath.is_native(terrain, unit_class_id):
            return None
        if game.city_at(index) is not None:
            return None
        # Keep clear of our own and everyone else's cities.
        mind = self.strategy.min_city_distance
        for city in list(game.cities.values()) + list(game.short_cities.values()):
            if game.topo.real_distance(index, city["tile"]) < mind:
                return None
        # Don't settle onto a tile another settler is already heading for.
        for target in self._settler_targets.values():
            if target is not None and \
               game.topo.real_distance(index, target) < mind:
                return None

        food, shield, trade = terrain["output"][0], terrain["output"][1], terrain["output"][2]
        score = food * 3 + shield * 2 + trade
        if tile.get("resource", 0):
            score += 3
        # Reward a productive neighbourhood, and coastal sites a little.
        coastal = False
        for _, n in game.topo.neighbours(index):
            nt = game.tiles.get(n)
            if nt is None or nt["known"] == 0:
                continue
            nter = game.ruleset.terrains.get(nt["terrain"])
            if nter is None:
                continue
            score += nter["output"][0] + nter["output"][1] * 0.5
            if not fcpath.is_native(nter, unit_class_id):
                coastal = True
        if coastal:
            score += 4
        return score

    def pick_city_site(self, unit):
        game = self.game
        uclass = game.ruleset.units[unit["type"]]["unit_class_id"]
        here = unit["tile"]
        candidates = []
        for index in game.tiles:
            dist = game.topo.real_distance(here, index)
            if dist > 12:
                continue
            score = self.city_site_score(index, uclass)
            if score is None:
                continue
            # Prefer good sites, but strongly prefer close ones.
            candidates.append((score - dist * 1.5, index))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        for _, index in candidates[:12]:
            if index == here:
                return index
            path = fcpath.find_path(game, here, index, uclass)
            if path is not None:
                return index
        return None

    # -- per-unit decisions ----------------------------------------------
    def handle_settler(self, unit):
        uid = unit["id"]
        client = self.client
        game = self.game
        if unit["has_orders"]:
            return "moving"
        target = self._settler_targets.get(uid)
        if target is None or target == unit["tile"]:
            target = self.pick_city_site(unit)
            self._settler_targets[uid] = target
        if target is None:
            client.do_activity(uid, state.ACTIVITY_SENTRY)
            return "no site"
        if target == unit["tile"]:
            self.city_counter += 1
            name = "Nova Roma %d" % self.city_counter
            client.build_city(uid, name)
            self._settler_targets.pop(uid, None)
            return "founding %s" % name
        ok = client.goto(uid, target,
                         then={"order": state.ORDER_BUILD_CITY})
        if not ok:
            self._settler_targets[uid] = None
            client.do_activity(uid, state.ACTIVITY_SENTRY)
            return "unreachable site"
        return "heading to %s" % target

    def handle_worker(self, unit):
        # The server's own auto-worker logic, same as the GTK client's button.
        self.client.conn.send("PACKET_UNIT_AUTOSETTLERS", unit_id=unit["id"])
        return "auto"

    def handle_explorer(self, unit):
        if unit["activity"] != state.ACTIVITY_EXPLORE:
            self.client.do_activity(unit["id"], state.ACTIVITY_EXPLORE)
        return "exploring"

    def handle_military(self, unit):
        game = self.game
        uid = unit["id"]
        # Defend the nearest city that has no defender yet, else fortify.
        my_cities = game.my_cities()
        garrison = {}
        for u in game.my_units().values():
            if self.unit_role(u) == "military":
                garrison.setdefault(u["tile"], 0)
                garrison[u["tile"]] += 1
        undefended = [c["tile"] for c in my_cities.values()
                      if garrison.get(c["tile"], 0) == 0]
        if unit["tile"] in [c["tile"] for c in my_cities.values()]:
            if unit["activity"] not in (state.ACTIVITY_FORTIFIED,
                                        state.ACTIVITY_FORTIFYING):
                self.client.do_activity(uid, state.ACTIVITY_FORTIFYING)
            return "fortified"
        if undefended and not unit["has_orders"]:
            uclass = game.ruleset.units[unit["type"]]["unit_class_id"]
            best = fcpath.nearest(game, unit["tile"], undefended, uclass)
            if best:
                self.client.goto(uid, best[0])
                return "to garrison %s" % best[0]
        if unit["activity"] == state.ACTIVITY_IDLE:
            self.client.do_activity(uid, state.ACTIVITY_FORTIFYING)
        return "holding"

    # -- city production ---------------------------------------------------
    def choose_production(self, city):
        game = self.game
        rules = game.ruleset
        my_cities = game.my_cities()
        units = list(game.my_units().values())

        def unit_named(name):
            u = rules.unit_by_name(name)
            return u if u and self.can_build_unit(u) else None

        defenders = sum(1 for u in units
                        if self.unit_role(u) == "military"
                        and u["tile"] == city["tile"])
        settlers = sum(1 for u in units if self.unit_role(u) == "settler")
        workers = sum(1 for u in units if self.unit_role(u) == "worker")

        # 1. A defender in every city.
        if defenders < self.strategy.defenders_per_city:
            for name in ("Musketeers", "Pikemen", "Phalanx", "Warriors"):
                u = unit_named(name)
                if u:
                    return state.VUT_UTYPE, u["id"], u["rule_name"]

        # 2. Expand while we are under the city target.
        if len(my_cities) + settlers < self.strategy.target_cities:
            u = unit_named("Settlers")
            if u and city["size"] > 1:
                return state.VUT_UTYPE, u["id"], u["rule_name"]

        # 3. Keep the land improved.
        if workers < len(my_cities) * self.strategy.workers_per_city:
            u = unit_named("Workers")
            if u:
                return state.VUT_UTYPE, u["id"], u["rule_name"]

        # 4. Buildings, in priority order.
        for name in self.strategy.building_priority:
            impr = rules.building_by_name(name)
            if impr and self.can_build_improvement(city, impr):
                return state.VUT_IMPROVEMENT, impr["id"], impr["rule_name"]

        # 5. Fall back to more defence.
        for name in ("Musketeers", "Pikemen", "Phalanx", "Warriors"):
            u = unit_named(name)
            if u:
                return state.VUT_UTYPE, u["id"], u["rule_name"]
        return None

    # -- research ----------------------------------------------------------
    def next_research_step(self, tech_id, seen=None):
        """First tech on the way to tech_id whose prerequisites we have."""
        if tech_id is None or self.knows_tech(tech_id):
            return None
        seen = seen or set()
        if tech_id in seen:
            return None
        seen.add(tech_id)
        if self.tech_state(tech_id) == TECH_PREREQS_KNOWN:
            return tech_id
        tech = self.game.ruleset.techs.get(tech_id)
        if tech is None:
            return None
        for req in list(tech.get("req", []))[:2] + [tech.get("root_req", 0)]:
            if not req:
                continue
            step = self.next_research_step(req, seen)
            if step is not None:
                return step
        return None

    def manage_research(self):
        game = self.game
        r = game.my_research
        if not r:
            return None
        # Drop techs we already have from the front of the plan.
        while self.strategy.tech_path:
            t = game.ruleset.tech_by_name(self.strategy.tech_path[0])
            if t is None or self.knows_tech(t["id"]):
                self.strategy.tech_path.pop(0)
            else:
                break
        if not self.strategy.tech_path:
            return None
        goal = game.ruleset.tech_by_name(self.strategy.tech_path[0])
        if goal is None:
            return None
        changed = None
        if r.get("tech_goal") != goal["id"]:
            self.client.set_research_goal(goal["id"])
            changed = "goal set to %s" % goal["name"]

        # Setting a goal does not redirect research already under way, so
        # steer it explicitly while no bulbs would be lost.
        step = self.next_research_step(goal["id"])
        if step is not None and r.get("researching") != step \
                and r.get("bulbs_researched", 0) == 0:
            self.client.set_research(step)
            name = self.game.ruleset.techs.get(step, {}).get("name", step)
            changed = "%s (towards %s)" % (name, goal["name"])
        return changed

    # -- the turn ----------------------------------------------------------
    def play_turn(self):
        client = self.client
        game = self.game
        report = {"turn": game.turn, "year": game.year,
                  "units": {}, "cities": {}, "research": None}

        goal = self.manage_research()
        if goal:
            report["research"] = "goal -> %s" % goal

        me = game.me
        if me and me.get("science") != self.strategy.science_rate:
            sci = self.strategy.science_rate
            client.set_rates(100 - sci, 0, sci)

        for uid, unit in sorted(game.my_units().items()):
            role = self.unit_role(unit)
            try:
                if role == "settler":
                    what = self.handle_settler(unit)
                elif role == "worker":
                    what = self.handle_worker(unit)
                elif role == "explorer":
                    what = (self.handle_explorer(unit) if self.strategy.explore
                            else self.handle_military(unit))
                elif role == "military":
                    what = self.handle_military(unit)
                else:
                    what = "idle"
            except Exception as exc:
                what = "error: %s" % exc
            report["units"][uid] = "%s: %s" % (role, what)

        client.pump(0.3)

        for cid, city in sorted(game.my_cities().items()):
            choice = self.choose_production(city)
            if choice is None:
                continue
            kind, value, label = choice
            if (city["production_kind"], city["production_value"]) != (kind, value):
                client.change_production(cid, kind, value)
                report["cities"][cid] = "%s -> %s" % (city["name"], label)
            else:
                report["cities"][cid] = "%s building %s" % (city["name"], label)

        client.pump(0.3)
        return report
