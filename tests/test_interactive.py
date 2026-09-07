"""Observation and orders: the two halves of interactive mode."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcbot import fcmap, observe, orders, server, state
from fcbot.interactive import InteractiveAgent


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def send(self, name, **values):
        self.sent.append((name, values))
        return True


class FakeClient(object):
    """Enough of Client to record what an order would put on the wire."""

    def __init__(self, game):
        self.game = game
        self.conn = FakeConn()
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name,) + args)
        return True

    def goto(self, uid, dest, then=None):
        self.calls.append(("goto", uid, dest, then))
        return dest != 999            # 999 stands for "no route"

    def do_activity(self, uid, activity, target=-1):
        return self._record("do_activity", uid, activity)

    def explore(self, uid):
        return self._record("explore", uid)

    def auto_settler(self, uid):
        return self._record("auto_settler", uid)

    def build_city(self, uid, name):
        return self._record("build_city", uid, name)

    def do_action(self, uid, action, target, sub_target=-1, name=""):
        return self._record("do_action", uid, action, target)

    def change_production(self, cid, kind, value):
        return self._record("change_production", cid, kind, value)

    def set_worklist(self, cid, entries):
        return self._record("set_worklist", cid, entries)

    def buy_production(self, cid):
        return self._record("buy_production", cid)

    def set_research(self, tid):
        return self._record("set_research", tid)

    def set_research_goal(self, tid):
        return self._record("set_research_goal", tid)

    def set_rates(self, tax, lux, sci):
        return self._record("set_rates", tax, lux, sci)

    def change_government(self, gid):
        return self._record("change_government", gid)

    def chat(self, text):
        return self._record("chat", text)

    def make_worker(self, cid, tile):
        return self._record("make_worker", cid, tile)

    def make_specialist(self, cid, tile):
        return self._record("make_specialist", cid, tile)

    def change_specialist(self, cid, from_id, to_id):
        return self._record("change_specialist", cid, from_id, to_id)

    def init_meeting(self, other):
        return self._record("init_meeting", other)

    def cancel_meeting(self, other):
        return self._record("cancel_meeting", other)

    def create_clause(self, other, giver, ctype, value=0):
        return self._record("create_clause", other, giver, ctype, value)

    def remove_clause(self, other, giver, ctype, value=0):
        return self._record("remove_clause", other, giver, ctype, value)

    def accept_treaty(self, other):
        return self._record("accept_treaty", other)

    def cancel_pact(self, other, clause=None):
        return self._record("cancel_pact", other, clause)

    def pump(self, timeout=0):
        return []


def build_game():
    """A small square-topology game with one city, two units and a rival."""
    g = state.GameState()
    g.topo = fcmap.Topology(12, 12, 0, 0)
    g.player_no = 0
    g.turn = 7
    g.year = -3000

    g.ruleset.terrains = {
        0: {"id": 0, "rule_name": "Grassland", "name": "Grassland",
            "movement_cost": 1, "native_to": 0xFF,
            "output": [2, 0, 0, 0, 0, 0]},
        1: {"id": 1, "rule_name": "Ocean", "name": "Ocean",
            "movement_cost": 1, "native_to": 0,
            "output": [1, 0, 2, 0, 0, 0]},
    }
    g.ruleset.units = {
        5: {"id": 5, "rule_name": "Settlers", "name": "Settlers",
            "unit_class_id": 0, "build_cost": 30, "pop_cost": 2},
        6: {"id": 6, "rule_name": "Warriors", "name": "Warriors",
            "unit_class_id": 0, "build_cost": 10, "pop_cost": 0},
    }
    g.ruleset.buildings = {
        2: {"id": 2, "rule_name": "Temple", "name": "Temple",
            "build_cost": 40},
    }
    g.ruleset.techs = {
        3: {"id": 3, "rule_name": "Currency", "name": "Currency"},
        4: {"id": 4, "rule_name": "Pottery", "name": "Pottery"},
    }
    g.ruleset.governments = {1: {"id": 1, "rule_name": "Despotism",
                                 "name": "Despotism"},
                             2: {"id": 2, "rule_name": "Monarchy",
                                 "name": "Monarchy"}}
    g.ruleset.nations = {9: {"id": 9, "rule_name": "Roman",
                             "name": "Roman", "adjective": "Roman"}}
    g.ruleset.extras = {40: {"id": 40, "rule_name": "Wheat"}}
    g.ruleset.resources = {40: {"id": 40, "output": [2, 0, 0, 0, 0, 0]}}
    g.ruleset.specialists = {
        0: {"id": 0, "rule_name": "elvis", "plural_name": "Entertainers"},
        1: {"id": 1, "rule_name": "scientist", "plural_name": "Scientists"},
    }
    # foodbox/shieldbox at 100 leave the ruleset costs alone; the granary
    # table is the classic ruleset's.
    g.game_info = {"foodbox": 100, "shieldbox": 100,
                   "granary_food_ini": [20, 20, 20, 20, 20, 30, 30, 40],
                   "granary_num_inis": 8, "granary_food_inc": 10}
    g.ruleset.terrain_control = {"move_fragments": 3}

    for index in range(144):
        g.tiles[index] = {"tile": index, "known": state.TILE_KNOWN_SEEN,
                          "terrain": 0, "resource": state.NO_RESOURCE,
                          "extras": 0, "owner": 0, "worked": 0}
    # Roma (tile 40) works two tiles, and there is wheat next door.
    g.tiles[41]["worked"] = 21
    g.tiles[52]["worked"] = 21
    g.tiles[39]["resource"] = 40

    g.players = {
        0: {"playerno": 0, "name": "Claudius", "nation": 9, "government": 1,
            "gold": 50, "tax": 40, "luxury": 0, "science": 60, "flags": 0,
            "is_alive": True, "real_embassy": 0, "score": 3},
        1: {"playerno": 1, "name": "Pakal", "nation": 9, "government": 1,
            "flags": state.PLRF_AI, "is_alive": True, "real_embassy": 0},
    }
    g.diplstates = {(0, 1): {"plr1": 0, "plr2": 1, "type": state.DS_WAR,
                             "turns_left": 0, "contact_turns_left": 14}}
    g.research = {0: {"id": 0, "researching": 3, "researching_cost": 30,
                      "bulbs_researched": 12, "tech_goal": 4,
                      "total_bulbs_prod": 4,
                      "inventions": "2" + "1" + "1" + "1" + "0"}}
    g.units = {
        11: {"id": 11, "owner": 0, "tile": 40, "type": 5, "movesleft": 3,
             "hp": 20, "veteran": 0, "activity": state.ACTIVITY_IDLE,
             "ssa_controller": state.SSA_NONE, "homecity": 0,
             "has_orders": False, "done_moving": False},
        12: {"id": 12, "owner": 1, "tile": 60, "type": 6, "movesleft": 3,
             "hp": 20, "veteran": 0, "activity": state.ACTIVITY_IDLE,
             "ssa_controller": state.SSA_NONE, "homecity": 0,
             "has_orders": False, "done_moving": False},
    }
    g.cities = {
        21: {"id": 21, "owner": 0, "tile": 40, "name": "Roma", "size": 3,
             "surplus": [2, 5, 4, 0, 0, 0], "food_stock": 6,
             "shield_stock": 10, "production_kind": state.VUT_IMPROVEMENT,
             "production_value": 2, "buy_cost": 90, "worklist": [(6, 6)],
             "improvements": 0, "anarchy": 0, "rapture": 0,
             "city_radius_sq": 5, "specialists": [1, 0]},
    }
    return g


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.obs = observe.observation(self.game)

    def test_ids_are_resolved_to_names(self):
        self.assertEqual(self.obs["meta"]["government"], "Despotism")
        self.assertEqual(self.obs["research"]["researching"], "Currency")
        self.assertEqual(self.obs["research"]["goal"], "Pottery")
        self.assertEqual(self.obs["units"][0]["type"], "Settlers")
        self.assertEqual(self.obs["cities"][0]["building"],
                         "improvement:Temple")
        self.assertEqual(self.obs["cities"][0]["worklist"], ["unit:Warriors"])

    def test_moves_are_in_whole_moves_not_fragments(self):
        # movesleft 3 with 3 fragments per move is one move, not three.
        self.assertEqual(self.obs["units"][0]["moves_left"], 1.0)

    def test_turns_to_completion_rounds_up(self):
        # Temple costs 40, we have 10 banked and make 5 a turn -> 6 turns.
        self.assertEqual(self.obs["cities"][0]["turns_to_completion"], 6)

    def test_only_our_own_units_are_ours(self):
        self.assertEqual([u["id"] for u in self.obs["units"]], [11])
        self.assertEqual([u["id"] for u in self.obs["foreign"]["units"]], [12])

    def test_foreign_units_are_sorted_by_threat_distance(self):
        u = self.obs["foreign"]["units"][0]
        self.assertEqual(u["owner"], "Pakal")
        self.assertIsNotNone(u["distance_from_my_nearest_city"])

    def test_diplomacy_names_the_state(self):
        self.assertEqual(self.obs["diplomacy"][0]["state"], "war")
        self.assertTrue(self.obs["diplomacy"][0]["is_ai"])

    def test_map_markers_never_collide_with_terrain_letters(self):
        letters = set(observe.terrain_letters(self.game.ruleset).values())
        self.assertTrue(all(c.islower() for c in letters))
        for marker in "CXUE*~":
            self.assertNotIn(marker, letters)

    def test_map_shows_our_city_and_the_rival(self):
        joined = "".join(self.obs["map"]["overview"])
        self.assertIn("C", joined)      # our city
        self.assertIn("E", joined)      # the rival's unit

    def test_map_rows_line_up_with_the_ruler(self):
        rows = self.obs["map"]["overview"]
        widths = {len(r) for r in rows[1:]}
        self.assertEqual(len(widths), 1, "map rows are ragged")

    def test_observation_is_json_serialisable(self):
        json.dumps(self.obs)

    def test_text_rendering_mentions_the_essentials(self):
        text = observe.to_text(self.obs)
        for expected in ("Turn 7", "Roma", "Settlers", "Despotism", "war"):
            self.assertIn(expected, text)


class OrdersTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)

    def run_orders(self, orders_list):
        return orders.apply_orders(self.client, orders_list)

    def test_names_resolve_to_ids(self):
        self.run_orders([{"city": 21, "build": ["unit", "Warriors"]},
                         {"research_goal": "Currency"},
                         {"government": "Monarchy"}])
        self.assertIn(("change_production", 21, state.VUT_UTYPE, 6),
                      self.client.calls)
        self.assertIn(("set_research_goal", 3), self.client.calls)
        self.assertIn(("change_government", 2), self.client.calls)

    def test_tiles_accept_both_index_and_coordinates(self):
        self.run_orders([{"unit": 11, "goto": 50},
                         {"unit": 11, "goto": [2, 3]}])
        dests = [c[2] for c in self.client.calls if c[0] == "goto"]
        self.assertEqual(dests, [50, self.game.topo.map_to_index(2, 3)])

    def test_found_city_after_goto_becomes_an_action_order(self):
        self.run_orders([{"unit": 11, "goto": 50, "then": "found_city"}])
        _, _, dest, then = self.client.calls[0]
        self.assertEqual(then["order"], state.ORDER_PERFORM_ACTION)
        self.assertEqual(then["action"], state.ACTION_FOUND_CITY)
        self.assertEqual(then["target"], dest)

    def test_explore_is_a_server_agent_not_an_activity(self):
        self.run_orders([{"unit": 11, "activity": "explore"}])
        self.assertEqual(self.client.calls, [("explore", 11)])

    def test_rates_must_add_up(self):
        result = self.run_orders(
            [{"rates": {"tax": 30, "luxury": 0, "science": 60}}])
        self.assertIn("FAILED", result[0])
        self.assertIn("100", result[0])
        self.assertEqual(self.client.calls, [])

    def test_a_bad_order_does_not_stop_the_others(self):
        results = self.run_orders([
            {"unit": 4242, "activity": "fortify"},
            {"unit": 11, "activity": "nonsense"},
            {"city": 999, "build": ["unit", "Warriors"]},
            {"city": 21, "build": ["unit", "Nonexistent"]},
            {"unit": 11, "goto": 999},
            {"chat": "still here"},
        ])
        self.assertEqual(sum(r.startswith("FAILED") for r in results), 5)
        self.assertEqual(self.client.calls[-1], ("chat", "still here"))

    def test_orders_must_be_a_list(self):
        with self.assertRaises(orders.OrderError):
            orders.apply_orders(self.client, {"unit": 11})


class DiplomacyStateTest(unittest.TestCase):
    """The mirror of a meeting, driven the way the server drives it."""

    def setUp(self):
        self.game = build_game()

    def feed(self, name, **values):
        self.game.handle("PACKET_DIPLOMACY_" + name, values)

    def open_meeting(self, initiated_from=1):
        self.feed("INIT_MEETING", counterpart=1,
                  initiated_from=initiated_from)
        return self.game.treaties[1]

    def test_a_meeting_is_tracked_from_the_init_packet(self):
        treaty = self.open_meeting()
        self.assertTrue(treaty.they_started_it)
        self.assertEqual(treaty.clauses, [])

    def test_clauses_arrive_and_leave(self):
        self.open_meeting()
        self.feed("CREATE_CLAUSE", counterpart=1, giver=1,
                  type=state.CLAUSE_CEASEFIRE, value=0)
        self.assertEqual(self.game.treaties[1].clauses,
                         [{"giver": 1, "type": state.CLAUSE_CEASEFIRE,
                           "value": 0}])
        self.feed("REMOVE_CLAUSE", counterpart=1, giver=1,
                  type=state.CLAUSE_CEASEFIRE, value=0)
        self.assertEqual(self.game.treaties[1].clauses, [])

    def test_a_new_clause_clears_both_acceptances(self):
        """The trap the whole schema is shaped around: accepting first and
        then adding a clause leaves nobody having accepted anything."""
        treaty = self.open_meeting()
        self.feed("ACCEPT_TREATY", counterpart=1, I_accepted=True,
                  other_accepted=True)
        self.assertTrue(treaty.i_accepted and treaty.other_accepted)
        self.feed("CREATE_CLAUSE", counterpart=1, giver=0,
                  type=state.CLAUSE_GOLD, value=50)
        self.assertFalse(treaty.i_accepted)
        self.assertFalse(treaty.other_accepted)

    def test_a_second_pact_clause_replaces_the_first(self):
        treaty = self.open_meeting()
        self.feed("CREATE_CLAUSE", counterpart=1, giver=0,
                  type=state.CLAUSE_CEASEFIRE, value=0)
        self.feed("CREATE_CLAUSE", counterpart=1, giver=0,
                  type=state.CLAUSE_PEACE, value=0)
        self.assertEqual([c["type"] for c in treaty.clauses],
                         [state.CLAUSE_PEACE])

    def test_a_repeated_gold_clause_updates_its_amount(self):
        treaty = self.open_meeting()
        self.feed("CREATE_CLAUSE", counterpart=1, giver=0,
                  type=state.CLAUSE_GOLD, value=50)
        self.feed("CREATE_CLAUSE", counterpart=1, giver=0,
                  type=state.CLAUSE_GOLD, value=80)
        self.assertEqual([c["value"] for c in treaty.clauses], [80])

    def test_cancelling_the_meeting_forgets_it(self):
        self.open_meeting()
        self.feed("CANCEL_MEETING", counterpart=1, initiated_from=1)
        self.assertEqual(self.game.treaties, {})

    def test_talking_needs_contact_or_an_embassy(self):
        self.assertTrue(self.game.can_meet(1))
        self.game.diplstates[(0, 1)]["contact_turns_left"] = 0
        self.assertFalse(self.game.can_meet(1))
        self.game.players[0]["real_embassy"] = 1 << 1
        self.assertTrue(self.game.can_meet(1))


class DiplomacyOrderTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)

    def run_orders(self, orders_list):
        return orders.apply_orders(self.client, orders_list)

    def test_an_offer_opens_the_meeting_and_accepts_last(self):
        results = self.run_orders(
            [{"diplomacy": "offer", "with": "Pakal",
              "clauses": ["ceasefire"]}])
        self.assertNotIn("FAILED", results[0])
        self.assertEqual(self.client.calls, [
            ("init_meeting", 1),
            ("create_clause", 1, 0, state.CLAUSE_CEASEFIRE, 0),
            ("accept_treaty", 1),
        ])

    def test_a_player_can_be_named_by_nation_or_number(self):
        for who in ("Pakal", "Roman", 1):
            self.client.calls = []
            self.run_orders([{"diplomacy": "meet", "with": who}])
            self.assertEqual(self.client.calls, [("init_meeting", 1)])

    def test_clauses_carry_values_and_a_giver(self):
        self.run_orders([{"diplomacy": "offer", "with": "Pakal", "clauses": [
            "peace",
            {"type": "gold", "value": 30},
            {"type": "advance", "value": "Currency", "from": "them"},
            {"embassy": None, "from": "them"},
        ], "accept": False}])
        self.assertEqual(self.client.calls[1:], [
            ("create_clause", 1, 0, state.CLAUSE_PEACE, 0),
            ("create_clause", 1, 0, state.CLAUSE_GOLD, 30),
            ("create_clause", 1, 1, state.CLAUSE_ADVANCE, 3),
            ("create_clause", 1, 1, state.CLAUSE_EMBASSY, 0),
        ])

    def test_a_pact_we_cannot_reach_is_refused_before_it_is_sent(self):
        """Ceasefire only comes from war, so at peace it is a mistake worth
        catching here rather than losing a turn to."""
        self.game.diplstates[(0, 1)]["type"] = state.DS_PEACE
        results = self.run_orders([{"diplomacy": "offer", "with": "Pakal",
                                    "clauses": ["ceasefire"]}])
        self.assertTrue(results[0].startswith("FAILED"))
        self.assertIn("only reachable from war", results[0])
        self.assertEqual(self.client.calls, [])

    def test_gold_we_do_not_have_is_refused(self):
        results = self.run_orders([{"diplomacy": "offer", "with": "Pakal",
                                    "clauses": [{"gold": 5000}]}])
        self.assertTrue(results[0].startswith("FAILED"))
        self.assertIn("we have 50", results[0])

    def test_accepting_needs_a_meeting(self):
        results = self.run_orders([{"diplomacy": "accept", "with": "Pakal"}])
        self.assertTrue(results[0].startswith("FAILED"))

    def test_accepting_an_offer_they_made(self):
        self.game.handle("PACKET_DIPLOMACY_INIT_MEETING",
                         {"counterpart": 1, "initiated_from": 1})
        self.game.handle("PACKET_DIPLOMACY_CREATE_CLAUSE",
                         {"counterpart": 1, "giver": 1,
                          "type": state.CLAUSE_CEASEFIRE, "value": 0})
        self.game.handle("PACKET_DIPLOMACY_ACCEPT_TREATY",
                         {"counterpart": 1, "I_accepted": False,
                          "other_accepted": True})
        results = self.run_orders([{"diplomacy": "accept", "with": "Pakal"}])
        self.assertEqual(self.client.calls, [("accept_treaty", 1)])
        self.assertIn("accepted", results[0])

    def test_breaking_a_pact_goes_one_step_down(self):
        self.run_orders([{"diplomacy": "break", "with": "Pakal"}])
        self.assertEqual(self.client.calls, [("cancel_pact", 1, None)])

    def test_an_unknown_player_is_reported_not_guessed(self):
        results = self.run_orders([{"diplomacy": "meet", "with": "Nobody"}])
        self.assertTrue(results[0].startswith("FAILED"))
        self.assertEqual(self.client.calls, [])

    def test_lapsed_contact_is_refused_with_a_reason(self):
        self.game.diplstates[(0, 1)]["contact_turns_left"] = 0
        results = self.run_orders([{"diplomacy": "offer", "with": "Pakal",
                                    "clauses": ["ceasefire"]}])
        self.assertIn("no embassy and no recent contact", results[0])


class DiplomacyObservationTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.game.handle("PACKET_DIPLOMACY_INIT_MEETING",
                         {"counterpart": 1, "initiated_from": 1})
        self.game.handle("PACKET_DIPLOMACY_CREATE_CLAUSE",
                         {"counterpart": 1, "giver": 1,
                          "type": state.CLAUSE_CEASEFIRE, "value": 0})
        self.game.handle("PACKET_DIPLOMACY_ACCEPT_TREATY",
                         {"counterpart": 1, "I_accepted": False,
                          "other_accepted": True})
        self.obs = observe.observation(self.game)

    def test_the_pending_offer_is_visible(self):
        meeting = self.obs["meetings"][0]
        self.assertEqual(meeting["with"], "Pakal")
        self.assertTrue(meeting["they_opened_it"])
        self.assertTrue(meeting["waiting_on_us"])
        self.assertEqual(meeting["clauses"][0]["clause"], "ceasefire")
        self.assertFalse(meeting["clauses"][0]["given_by_us"])

    def test_the_text_says_who_is_waiting(self):
        text = observe.to_text(self.obs)
        self.assertIn("MEETING WITH Pakal", text)
        self.assertIn("THEY ARE WAITING ON US", text)

    def test_whether_we_can_talk_at_all_is_stated(self):
        entry = self.obs["diplomacy"][0]
        self.assertTrue(entry["can_negotiate_now"])
        self.assertTrue(entry["in_meeting"])
        self.assertEqual(entry["contact_turns_left"], 14)


class CityDetailTest(unittest.TestCase):
    """The things the first live game could not see, and paid for."""

    def setUp(self):
        self.game = build_game()
        self.obs = observe.observation(self.game)
        self.city = self.obs["cities"][0]

    def test_food_box_and_turns_to_grow(self):
        # Size 3 in this granary table needs 20 food; 6 banked, +2 a turn.
        self.assertEqual(self.city["food"]["box"], 20)
        self.assertEqual(self.city["food"]["turns_to_grow"], 7)

    def test_a_starving_city_says_how_long_it_has(self):
        self.game.cities[21]["surplus"] = [-2, 5, 4, 0, 0, 0]
        city = observe.observation(self.game)["cities"][0]
        self.assertIn("shrinking", city["food"]["turns_to_grow"])
        self.assertTrue(any("starving" in w for w in city["warnings"]))

    def test_population_cost_is_visible_before_the_build_fails(self):
        self.game.cities[21]["production_kind"] = state.VUT_UTYPE
        self.game.cities[21]["production_value"] = 5      # Settlers, 2 pop
        self.game.cities[21]["size"] = 2
        city = observe.observation(self.game)["cities"][0]
        self.assertEqual(city["population_cost"], 2)
        self.assertTrue(any("cannot be completed until size 3" in w
                            for w in city["warnings"]))

    def test_banked_shields_above_the_cost_are_flagged(self):
        self.game.cities[21]["shield_stock"] = 60         # Temple costs 40
        city = observe.observation(self.game)["cities"][0]
        self.assertTrue(any("wasted" in w for w in city["warnings"]))

    def test_build_cost_follows_the_shieldbox_setting(self):
        self.game.game_info["shieldbox"] = 200
        city = observe.observation(self.game)["cities"][0]
        self.assertEqual(city["build_cost"], 80)

    def test_worked_and_free_tiles_are_listed_with_their_output(self):
        self.assertEqual(len(self.city["worked_tiles"]), 2)
        self.assertEqual(sorted(t["at"] for t in self.city["worked_tiles"]),
                         sorted(list(self.game.topo.index_to_map(i))
                                for i in (41, 52)))
        best = self.city["free_tiles"][0]
        # The wheat tile is 2 food of grassland plus 2 from the resource.
        self.assertEqual(best["resource"], "Wheat")
        self.assertEqual(best["output"]["food"], 4)

    def test_free_tiles_exclude_what_another_city_works(self):
        self.game.tiles[42]["worked"] = 99
        city = observe.observation(self.game)["cities"][0]
        self.assertNotIn(list(self.game.topo.index_to_map(42)),
                         [t["at"] for t in city["free_tiles"]])

    def test_specialists_are_named(self):
        self.assertEqual(self.city["specialists"], {"Entertainers": 1})


class CityTileOrderTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)

    def run_orders(self, o):
        return orders.apply_orders(self.client, o)

    def test_a_tile_can_be_put_to_work(self):
        at = list(self.game.topo.index_to_map(39))
        self.run_orders([{"city": 21, "work_tile": at}])
        self.assertEqual(self.client.calls, [("make_worker", 21, 39)])

    def test_a_worked_tile_can_be_freed(self):
        at = list(self.game.topo.index_to_map(41))
        self.run_orders([{"city": 21, "stop_working": at}])
        self.assertEqual(self.client.calls, [("make_specialist", 21, 41)])

    def test_tiles_outside_the_radius_are_refused(self):
        results = self.run_orders([{"city": 21, "work_tile": [11, 11]}])
        self.assertTrue(results[0].startswith("FAILED"))
        self.assertIn("outside", results[0])
        self.assertEqual(self.client.calls, [])

    def test_the_centre_tile_cannot_be_reassigned(self):
        at = list(self.game.topo.index_to_map(40))
        results = self.run_orders([{"city": 21, "stop_working": at}])
        self.assertIn("city centre", results[0])

    def test_a_specialist_can_be_retrained(self):
        self.run_orders([{"city": 21,
                          "specialist": {"from": "Elvis", "to": "Scientist"}}])
        self.assertEqual(self.client.calls, [("change_specialist", 21, 0, 1)])

    def test_a_specialist_is_named_case_insensitively(self):
        self.run_orders([{"city": 21,
                          "specialist": {"from": "ELVIS", "to": "scientist"}}])
        self.assertEqual(self.client.calls, [("change_specialist", 21, 0, 1)])

    def test_retraining_a_specialist_we_do_not_have_is_refused(self):
        results = self.run_orders(
            [{"city": 21, "specialist": {"from": "Scientist", "to": "Elvis"}}])
        self.assertTrue(results[0].startswith("FAILED"))

    def test_building_something_the_city_is_too_small_for_is_noted(self):
        self.game.cities[21]["size"] = 2
        results = self.run_orders([{"city": 21, "build": ["unit", "Settlers"]}])
        self.assertIn("NOTE", results[0])
        self.assertIn("cannot finish until size 3", results[0])
        # ...but it is still sent: you queue it while the city grows.
        self.assertEqual(self.client.calls,
                         [("change_production", 21, state.VUT_UTYPE, 5)])


class ResearchValidationTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)

    def run_orders(self, o):
        return orders.apply_orders(self.client, o)

    def test_researching_something_we_know_is_refused(self):
        """The server drops this silently, and a turn of research goes."""
        known = self.game.ruleset.techs[3]
        self.game.research[0]["inventions"] = "2222"
        results = self.run_orders([{"research": known["name"]}])
        self.assertTrue(results[0].startswith("FAILED"))
        self.assertIn("already know", results[0])
        self.assertEqual(self.client.calls, [])

    def test_researching_past_our_prerequisites_is_refused(self):
        self.game.research[0]["inventions"] = "2000"
        results = self.run_orders([{"research": "Pottery"}])
        self.assertIn("research_goal", results[0])
        self.assertEqual(self.client.calls, [])

    def test_a_goal_may_be_out_of_reach_for_now(self):
        self.game.research[0]["inventions"] = "2000"
        results = self.run_orders([{"research_goal": "Pottery"}])
        self.assertFalse(results[0].startswith("FAILED"))
        self.assertEqual(self.client.calls, [("set_research_goal", 4)])


class MapWindowTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()

    def test_a_far_off_explorer_does_not_stretch_the_overview(self):
        """One wanderer used to turn the empire view into 43x43 of '?'."""
        before = observe.observation(self.game)["map"]["overview"]
        self.game.units[13] = dict(self.game.units[11], id=13, tile=143)
        after = observe.observation(self.game)["map"]
        self.assertEqual(len(after["overview"]), len(before))
        self.assertTrue(after["around_units_further_afield"])

    def test_the_wanderer_gets_its_own_close_up(self):
        self.game.units[13] = dict(self.game.units[11], id=13, tile=143)
        views = observe.observation(self.game)["map"]["around_units_further_afield"]
        label = list(views)[0]
        self.assertIn("Settlers #13", label)

    def test_all_unknown_edges_are_trimmed_away(self):
        for index, tile in self.game.tiles.items():
            if index != 40:
                tile["known"] = state.TILE_UNKNOWN
        overview = observe.observation(self.game)["map"]["overview"]
        # A header and one row holding the single known tile.
        self.assertEqual(len(overview), 2)
        self.assertEqual(overview[1].split()[1], "gC")


class TopologyChoiceTest(unittest.TestCase):
    """`--topology` has to name values the 3.2 server's `set topology`
    accepts; the empty one is what makes a map plain squares."""

    def test_the_four_shapes_map_to_the_server_setting(self):
        self.assertEqual(server.TOPOLOGIES["square"], "")
        self.assertEqual(server.TOPOLOGIES["iso"], "ISO")
        self.assertEqual(server.TOPOLOGIES["hex"], "Hex")
        self.assertEqual(server.TOPOLOGIES["iso-hex"], "ISO|Hex")

    def test_an_unknown_topology_is_refused_before_the_server_sees_it(self):
        srv = server.Server.__new__(server.Server)
        srv.command = lambda cmd: True
        srv.ruleset = "classic"
        with self.assertRaises(ValueError):
            srv.configure(topology="octagon")

    def test_the_empty_value_is_quoted(self):
        sent = []
        srv = server.Server.__new__(server.Server)
        srv.command = lambda cmd: sent.append(cmd)
        srv.ruleset = "classic"
        srv.configure(topology="square")
        self.assertIn('set topology ""', sent)


class InteractiveFileTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)
        self.dir = tempfile.mkdtemp()
        self.agent = InteractiveAgent(self.client, self.dir,
                                      poll_seconds=0.01)

    def test_observation_is_written_with_both_faces(self):
        path, obs = self.agent.write_observation(7)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(path.replace(".json", ".txt")))
        with open(path) as fp:
            self.assertEqual(json.load(fp)["meta"]["turn"], 7)
        self.assertIn("orders_go_in", obs)

    def test_orders_are_read_and_applied(self):
        self.agent.write_observation(7)
        with open(self.agent.path(7, "orders"), "w") as fp:
            json.dump([{"chat": "hello"}], fp)
        results = self.agent.play_turn(deadline=0)
        self.assertEqual(self.client.calls, [("chat", "hello")])
        with open(self.agent.path(7, "result")) as fp:
            self.assertEqual(json.load(fp)["results"], results)

    def test_no_orders_ends_the_phase_rather_than_hanging(self):
        results = self.agent.play_turn(deadline=0)
        self.assertEqual(results, [])

    def test_an_earlier_game_in_the_directory_is_moved_aside(self):
        """Turn numbers restart every game, so leftovers would be taken as
        this turn's orders the instant the observation was written -- which
        is exactly how one game replayed two-day-old orders."""
        for name in ("0001.obs.json", "0001.orders.json", "0001.result.json",
                     "0002.obs.txt"):
            with open(os.path.join(self.dir, name), "w") as fp:
                fp.write("[]")
        agent = InteractiveAgent(self.client, self.dir, poll_seconds=0.01)
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, "0001.orders.json")))
        archives = [n for n in os.listdir(self.dir)
                    if n.startswith("previous-")]
        self.assertEqual(len(archives), 1)
        # kept, not deleted: they are the record of a game that was played
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir,
                                                        archives[0]))),
                         ["0001.obs.json", "0001.orders.json",
                          "0001.result.json", "0002.obs.txt"])
        self.assertIsNone(agent.archive_previous_game())

    def test_a_stale_orders_file_is_not_taken_as_this_turn_s(self):
        with open(os.path.join(self.dir, "0007.orders.json"), "w") as fp:
            json.dump([{"chat": "from a game two days ago"}], fp)
        agent = InteractiveAgent(self.client, self.dir, poll_seconds=0.01)
        self.assertEqual(agent.play_turn(deadline=0), [])
        self.assertEqual(self.client.calls, [])

    def test_the_seat_can_be_retuned_while_the_game_runs(self):
        """Restarting the host would drop us out of the game, so observe.py
        and orders.py are re-imported each turn instead."""
        self.assertTrue(self.agent.reload_each_turn)
        self.agent.reload_modules()
        _path, obs = self.agent.write_observation(9)
        self.assertEqual(obs["meta"]["turn"], 7)

    def test_messages_are_not_replayed_next_turn(self):
        self.agent.write_observation(7)
        first = self.agent.last_message_mark
        _, obs = self.agent.write_observation(8)
        self.assertEqual(obs["message_mark"], first)
        self.assertEqual(obs["chat_since_last_turn"], [])


if __name__ == "__main__":
    unittest.main()
