"""Standing orders, and the exceptions that stop them.

The contract these tests pin down is the conservative one: autoplay carries
out what the policy says and stops for everything else. A test that lets it
decide something the policy did not cover is testing the wrong thing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fcbot import policy as policy_mod, state
from fcbot.interactive import InteractiveAgent, _split_policy
from test_interactive import FakeClient, build_game


FULL = {
    "research": ["Currency", "Pottery"],
    "cities": {"default": {"build": [["unit", "Warriors"]]}},
    "units": {"Settlers": "hold", "Warriors": "fortify", "*": "sentry"},
    "wake_on": {"every_n_turns": 0, "enemy_within": 0},
}


def policy_for(**over):
    data = dict(FULL)
    data.update(over)
    return policy_mod.Policy(data)


class InterruptTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.memory = policy_mod.remember(self.game)

    def kinds(self, policy, memory=None):
        memory = self.memory if memory is None else memory
        return sorted(i.kind for i in
                      policy_mod.check_interrupts(self.game, policy, memory))

    def test_a_covered_turn_raises_nothing(self):
        self.assertEqual(self.kinds(policy_for()), [])

    def test_a_unit_with_no_doctrine_stops_autoplay(self):
        """The whole point of conservative: it will not invent a use for
        something the policy never mentioned."""
        self.game.units[13] = dict(self.game.units[11], id=13, type=6)
        policy = policy_for(units={"Settlers": "hold"})   # nothing for Warriors
        self.assertIn("unit_without_orders", self.kinds(policy))

    def test_a_city_with_no_build_rule_stops_autoplay(self):
        policy = policy_for(cities={})
        self.game.cities[21]["worklist"] = []
        self.assertIn("worklist_empty", self.kinds(policy))

    def test_an_exhausted_research_plan_stops_autoplay(self):
        self.game.research[0]["inventions"] = "22222"
        self.assertIn("research_idle", self.kinds(policy_for()))

    def test_meeting_someone_new_stops_autoplay(self):
        """Contact is the moment diplomacy is possible and cheap; it is
        exactly what a policy should not decide unattended."""
        self.assertEqual(self.kinds(policy_for(), memory={}),
                         sorted(set(self.kinds(policy_for(), memory={}))))
        self.assertIn("first_contact", self.kinds(policy_for(), memory={}))

    def test_someone_already_met_does_not_stop_it_again(self):
        self.assertNotIn("first_contact", self.kinds(policy_for()))

    def test_a_treaty_waiting_on_us_stops_autoplay(self):
        self.game.handle("PACKET_DIPLOMACY_INIT_MEETING",
                         {"counterpart": 1, "initiated_from": 1})
        self.game.handle("PACKET_DIPLOMACY_ACCEPT_TREATY",
                         {"counterpart": 1, "I_accepted": False,
                          "other_accepted": True})
        self.assertIn("treaty_offered", self.kinds(policy_for()))

    def test_losing_a_city_stops_autoplay(self):
        memory = policy_mod.remember(self.game)
        del self.game.cities[21]
        self.assertIn("city_attacked_or_lost", self.kinds(policy_for(),
                                                          memory))

    def test_starvation_and_disorder_stop_autoplay(self):
        self.game.cities[21]["surplus"] = [-1, 5, 4, 0, 0, 0]
        self.assertIn("disorder_or_famine", self.kinds(policy_for()))
        self.game.cities[21]["surplus"] = [2, 5, 4, 0, 0, 0]
        self.game.cities[21]["anarchy"] = 1
        self.assertIn("disorder_or_famine", self.kinds(policy_for()))

    def test_a_hostile_unit_near_a_city_stops_autoplay(self):
        policy = policy_for(wake_on={"enemy_within": 6, "every_n_turns": 0})
        self.assertIn("enemy_within", self.kinds(policy))

    def test_a_distant_hostile_unit_does_not(self):
        policy = policy_for(wake_on={"enemy_within": 1, "every_n_turns": 0})
        self.assertNotIn("enemy_within", self.kinds(policy))

    def test_a_checkpoint_stops_autoplay_even_when_all_is_well(self):
        policy = policy_for(wake_on={"every_n_turns": 5, "enemy_within": 0})
        memory = dict(self.memory, last_decision_turn=0)
        self.assertIn("every_n_turns", self.kinds(policy, memory))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()

    def plan(self, policy):
        return policy_mod.plan_turn(self.game, policy)[0]

    def test_research_follows_the_plan_in_order(self):
        self.game.research[0]["researching"] = 4          # off-plan
        orders = self.plan(policy_for(research=["Currency"]))
        self.assertIn({"research": "Currency"}, orders)

    def test_a_tech_already_known_is_skipped(self):
        self.game.research[0]["inventions"] = "2211"
        orders = self.plan(policy_for(research=["Currency", "Pottery"]))
        self.assertNotIn({"research": "Currency"}, orders)

    def test_an_unreachable_tech_becomes_a_goal_not_a_dead_end(self):
        """Setting research to something whose prerequisites are missing is
        silently dropped by the server; a goal is the right request."""
        self.game.research[0]["inventions"] = "2000"
        self.game.research[0]["tech_goal"] = 0
        orders = self.plan(policy_for(research=["Pottery"]))
        self.assertIn({"research_goal": "Pottery"}, orders)

    def test_cities_build_what_the_rule_says(self):
        self.game.cities[21]["worklist"] = []
        orders = self.plan(policy_for())
        self.assertIn({"city": 21, "build": ["unit", "Warriors"]}, orders)

    def test_a_city_already_building_it_is_left_alone(self):
        self.game.cities[21]["worklist"] = []
        self.game.cities[21]["production_kind"] = state.VUT_UTYPE
        self.game.cities[21]["production_value"] = 6      # Warriors
        self.assertEqual(
            [o for o in self.plan(policy_for()) if "build" in o], [])

    def test_units_get_their_doctrine(self):
        orders = self.plan(policy_for(units={"Settlers": "fortify"}))
        self.assertIn({"unit": 11, "activity": "fortify"}, orders)

    def test_a_unit_already_doing_it_is_not_re_ordered(self):
        self.game.units[11]["activity"] = state.ACTIVITY_FORTIFIED
        orders = self.plan(policy_for(units={"Settlers": "fortify"}))
        self.assertEqual(orders, [o for o in orders if "unit" not in o])

    def test_hold_means_do_nothing(self):
        orders = self.plan(policy_for(units={"Settlers": "hold"}))
        self.assertEqual([o for o in orders if o.get("unit") == 11], [])

    def test_a_tile_bias_makes_one_swap_at_a_time(self):
        """One swap per turn: the server rearranges citizens itself, and a
        pile of reassignments in one turn is hard to read and easy to get
        wrong."""
        self.game.tiles[39]["resource"] = 40         # wheat, 4 food
        policy = policy_for(cities={"default": {"build": [["unit", "Warriors"]],
                                                "tiles": "food"}})
        orders = self.plan(policy)
        swaps = [o for o in orders if "work_tile" in o or "stop_working" in o]
        self.assertEqual(len(swaps), 2)

    def test_no_bias_means_no_tile_meddling(self):
        self.game.tiles[39]["resource"] = 40
        orders = self.plan(policy_for())
        self.assertEqual([o for o in orders
                          if "work_tile" in o or "stop_working" in o], [])


class AutoplayTest(unittest.TestCase):
    def setUp(self):
        self.game = build_game()
        self.client = FakeClient(self.game)
        self.agent = InteractiveAgent(self.client, _tmpdir(),
                                      poll_seconds=0.01)

    def test_without_a_policy_every_turn_needs_a_decision(self):
        played, interrupts = self.agent.autoplay_turn()
        self.assertFalse(played)
        self.assertEqual(interrupts[0].kind, "no_policy")

    def test_a_covered_turn_is_played_without_waking_anyone(self):
        self.agent.set_policy(FULL)
        self.agent.memory = policy_mod.remember(self.game)
        self.game.cities[21]["worklist"] = []
        played, interrupts = self.agent.autoplay_turn()
        self.assertTrue(played, [i.detail for i in interrupts])
        self.assertEqual(interrupts, [])
        self.assertTrue(self.client.calls)

    def test_an_interrupt_sends_nothing_at_all(self):
        """Stopping has to mean stopping: a half-played turn would be worse
        than either playing it or not."""
        self.agent.set_policy(dict(FULL, units={}))
        played, interrupts = self.agent.autoplay_turn()
        self.assertFalse(played)
        self.assertEqual(self.client.calls, [])

    def test_standing_orders_arrive_by_the_same_channel_as_orders(self):
        rest, found = _split_policy([{"unit": 11, "activity": "fortify"},
                                     {"policy": FULL}])
        self.assertEqual(rest, [{"unit": 11, "activity": "fortify"}])
        self.assertEqual(found["research"], ["Currency", "Pottery"])

    def test_an_ordinary_orders_list_carries_no_policy(self):
        rest, found = _split_policy([{"chat": "hello"}])
        self.assertIsNone(found)
        self.assertEqual(len(rest), 1)


def _tmpdir():
    import tempfile
    return tempfile.mkdtemp()


if __name__ == "__main__":
    unittest.main()
