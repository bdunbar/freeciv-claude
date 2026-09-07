"""`fcgame.py play`: one call per turn instead of poll, write, poll again."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fcgame


class Args(object):
    def __init__(self, **kw):
        self.turns_dir = kw.get("turns_dir")
        self.orders = kw.get("orders")
        self.timeout = kw.get("timeout", 5)


def write_obs(turns_dir, turn, **extra):
    """A minimal observation, enough for observe.to_text()."""
    obs = {
        "meta": {"turn": turn, "year": "3000 BC", "gold": 50,
                 "rates": {"tax": 40, "luxury": 0, "science": 60},
                 "government": "Despotism", "in_revolution": False,
                 "revolution_finishes_turn": None, "nation": "Roman",
                 "leader": "Claudius", "score": 3},
        "research": None, "units": [], "cities": [],
        "map": {"legend": {"cells": "", "terrain": "", "directions": ""},
                "centre_tile": 0, "overview": [], "around_each_city": {},
                "around_units_further_afield": {}},
        "foreign": {"units": [], "cities": []}, "diplomacy": [],
        "meetings": [], "diplomatic_news": [],
        "chat_since_last_turn": [], "events_since_last_turn": {},
        "message_mark": 0,
    }
    obs.update(extra)
    with open(os.path.join(turns_dir, "%04d.obs.json" % turn), "w") as fp:
        json.dump(obs, fp)
    return obs


class PendingTurnTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_nothing_pending_before_the_game_starts(self):
        self.assertIsNone(fcgame._pending_turn(self.dir))

    def test_the_newest_unanswered_observation_is_the_pending_turn(self):
        write_obs(self.dir, 20)
        write_obs(self.dir, 21)
        self.assertEqual(fcgame._pending_turn(self.dir), 21)

    def test_an_answered_turn_is_not_pending(self):
        """Once orders are in, the host is thinking and it is not our move."""
        write_obs(self.dir, 21)
        with open(os.path.join(self.dir, "0021.orders.json"), "w") as fp:
            fp.write("[]")
        self.assertIsNone(fcgame._pending_turn(self.dir))

    def test_a_missing_directory_is_not_an_error(self):
        self.assertIsNone(fcgame._pending_turn(
            os.path.join(self.dir, "nope")))


class PlayTest(unittest.TestCase):
    """Drive cmd_play against a stand-in host that answers one turn."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.stop = threading.Event()

    def tearDown(self):
        self.stop.set()

    def fake_host(self, turn, results=("unit 11: sent fortify",), delay=0.05):
        """Do what InteractiveAgent does: wait for orders, write the result
        and the next observation."""
        def run():
            path = os.path.join(self.dir, "%04d.orders.json" % turn)
            while not self.stop.is_set():
                if os.path.exists(path):
                    time.sleep(delay)
                    with open(os.path.join(self.dir,
                                           "%04d.result.json" % turn), "w") as fp:
                        json.dump({"turn": turn, "orders": [],
                                   "results": list(results)}, fp)
                    write_obs(self.dir, turn + 1)
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_one_call_submits_orders_and_returns_the_next_turn(self):
        write_obs(self.dir, 21)
        self.fake_host(21)
        code = fcgame.cmd_play(Args(turns_dir=self.dir,
                                    orders='[{"unit": 11, "activity": "fortify"}]'))
        self.assertEqual(code, 0)
        with open(os.path.join(self.dir, "0021.orders.json")) as fp:
            self.assertEqual(json.load(fp),
                             [{"unit": 11, "activity": "fortify"}])

    def test_it_waits_for_a_turn_that_is_not_ready_yet(self):
        """The host may still be running the AI players' phase."""
        def later():
            time.sleep(0.3)
            write_obs(self.dir, 7)
            self.fake_host(7)
        threading.Thread(target=later, daemon=True).start()
        self.assertEqual(fcgame.cmd_play(Args(turns_dir=self.dir,
                                              orders="[]")), 0)
        self.assertTrue(os.path.exists(
            os.path.join(self.dir, "0007.orders.json")))

    def test_no_orders_is_a_legitimate_turn(self):
        write_obs(self.dir, 3)
        self.fake_host(3)
        self.assertEqual(fcgame.cmd_play(Args(turns_dir=self.dir,
                                              orders=None, timeout=5)), 0)
        with open(os.path.join(self.dir, "0003.orders.json")) as fp:
            self.assertEqual(json.load(fp), [])

    def test_orders_that_are_not_a_list_are_refused(self):
        write_obs(self.dir, 3)
        self.assertEqual(fcgame.cmd_play(Args(turns_dir=self.dir,
                                              orders='{"unit": 11}')), 2)
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, "0003.orders.json")))

    def test_giving_up_says_so_rather_than_hanging(self):
        self.assertEqual(fcgame.cmd_play(Args(turns_dir=self.dir,
                                              orders="[]", timeout=1)), 1)

    def test_a_host_that_never_answers_is_reported(self):
        write_obs(self.dir, 9)
        code = fcgame.cmd_play(Args(turns_dir=self.dir, orders="[]",
                                    timeout=1))
        self.assertEqual(code, 1)     # orders went in, no next observation


if __name__ == "__main__":
    unittest.main()
