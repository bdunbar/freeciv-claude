"""The persistent runner: the thing that is awake when the model is not.

No model is called here. The agent is a one-line shell command, which is
the point of making the command configurable -- a fake agent that writes
bad JSON is a string, not a mock.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcbot import runner


def write_obs(turns_dir, turn):
    """What InteractiveAgent leaves for us: the JSON and its readable twin."""
    obs = {"meta": {"turn": turn}, "units": [], "cities": [],
           "orders_go_in": os.path.join(turns_dir, "%04d.orders.json" % turn),
           "how_to_answer": "a JSON list"}
    runner.write_json(os.path.join(turns_dir, "%04d.obs.json" % turn), obs)
    with open(os.path.join(turns_dir, "%04d.obs.txt" % turn), "w") as fp:
        fp.write("Turn %d\n" % turn)
    return obs


#: Fake agents, as shell one-liners. `"$FC_ORDERS"` is the path the runner
#: told it to write, which is also the interface a real agent gets.
GOOD = 'printf %s \'[{"unit": 11, "activity": "fortify"}]\' > "$FC_ORDERS"'
EMPTY_LIST = 'printf %s "[]" > "$FC_ORDERS"'
MALFORMED = 'printf %s "{not json" > "$FC_ORDERS"'
NOT_A_LIST = 'printf %s \'{"unit": 11}\' > "$FC_ORDERS"'
SILENT = 'true'
CRASH = 'echo "boom" >&2; exit 3'
WROTE_THEN_CRASHED = GOOD + '; exit 9'
HANGS = 'sleep 30'


def make_runner(turns_dir, command=GOOD, **kw):
    kw.setdefault("timeout", 10)
    kw.setdefault("poll", 0.02)
    kw.setdefault("retry_delay", 0)
    return runner.TurnRunner(
        turns_dir, runner.AgentCommand(command, shell=True),
        strategy=os.path.join(turns_dir, "strategy.md"),
        journal=os.path.join(turns_dir, "journal.md"), **kw)


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def orders_path(self, turn):
        return os.path.join(self.dir, "%04d.orders.json" % turn)

    def record(self, turn):
        return runner.read_json(os.path.join(self.dir, "%04d.agent.json" % turn))


class PendingTurnTest(RunnerTestCase):
    def test_the_newest_unanswered_observation_is_the_work(self):
        write_obs(self.dir, 12)
        write_obs(self.dir, 13)
        self.assertEqual(runner.pending_turn(self.dir), 13)

    def test_an_answered_turn_is_not_work(self):
        write_obs(self.dir, 13)
        runner.write_json(self.orders_path(13), [])
        self.assertIsNone(runner.pending_turn(self.dir))

    def test_an_older_unanswered_turn_is_not_work(self):
        """The game moved on; answering turn 12 now answers a dead board."""
        write_obs(self.dir, 12)
        write_obs(self.dir, 13)
        runner.write_json(self.orders_path(13), [])
        self.assertIsNone(runner.pending_turn(self.dir))

    def test_a_missing_directory_is_not_an_error(self):
        self.assertIsNone(runner.pending_turn(os.path.join(self.dir, "no")))


class HappyPathTest(RunnerTestCase):
    def test_a_turn_is_detected_played_and_submitted(self):
        write_obs(self.dir, 42)
        engine = make_runner(self.dir)
        self.assertEqual(engine.run_forever(once=True), 0)
        with open(self.orders_path(42)) as fp:
            self.assertEqual(json.load(fp),
                             [{"unit": 11, "activity": "fortify"}])
        record = self.record(42)
        self.assertEqual(record["status"], runner.OK)
        self.assertTrue(record["submitted"])
        self.assertEqual(record["orders_count"], 1)

    def test_the_agent_never_sees_the_path_the_game_reads(self):
        """It writes a proposal; the rename into place is the runner's."""
        write_obs(self.dir, 7)
        job = make_runner(self.dir).job_for(7)
        self.assertTrue(job.orders.endswith("0007.orders.proposed.json"))
        self.assertTrue(job.submitted.endswith("0007.orders.json"))
        self.assertNotEqual(job.orders, job.submitted)

    def test_an_empty_list_is_a_real_turn_and_is_submitted(self):
        write_obs(self.dir, 5)
        self.assertEqual(make_runner(self.dir, EMPTY_LIST)
                         .run_forever(once=True), 0)
        with open(self.orders_path(5)) as fp:
            self.assertEqual(json.load(fp), [])

    def test_it_goes_back_to_waiting_and_plays_the_next_turn(self):
        write_obs(self.dir, 1)
        engine = make_runner(self.dir)

        def next_turn():
            while not os.path.exists(self.orders_path(1)):
                time.sleep(0.01)
            write_obs(self.dir, 2)
        threading.Thread(target=next_turn, daemon=True).start()
        engine.run_one(1)
        engine.done.add(1)
        deadline = time.time() + 5
        while not os.path.exists(self.orders_path(2)) and time.time() < deadline:
            turn = runner.pending_turn(self.dir)
            if turn is not None and turn not in engine.done:
                engine.run_one(turn)
                engine.done.add(turn)
            time.sleep(0.02)
        self.assertTrue(os.path.exists(self.orders_path(2)))


class FailureTest(RunnerTestCase):
    """Every failure leaves the turn pending. Nothing is ever invented."""

    def assert_left_pending(self, turn, status):
        self.assertFalse(os.path.exists(self.orders_path(turn)),
                         "orders were submitted after a failed agent")
        self.assertEqual(runner.pending_turn(self.dir), turn)
        record = self.record(turn)
        self.assertEqual(record["status"], status)
        self.assertFalse(record["submitted"])
        return record

    def test_malformed_json_is_not_submitted(self):
        write_obs(self.dir, 3)
        self.assertEqual(make_runner(self.dir, MALFORMED)
                         .run_forever(once=True), 1)
        record = self.assert_left_pending(3, runner.INVALID_ORDERS)
        self.assertIn("not valid JSON", record["detail"])

    def test_orders_that_are_not_a_list_are_not_submitted(self):
        write_obs(self.dir, 3)
        make_runner(self.dir, NOT_A_LIST).run_one(3)
        record = self.assert_left_pending(3, runner.INVALID_ORDERS)
        self.assertIn("must be a JSON list", record["detail"])

    def test_no_orders_at_all_does_not_become_an_empty_turn(self):
        """The one thing the runner must never do is play the turn itself."""
        write_obs(self.dir, 8)
        make_runner(self.dir, SILENT).run_one(8)
        self.assert_left_pending(8, runner.NO_ORDERS)

    def test_a_nonzero_exit_is_recorded_with_its_output(self):
        write_obs(self.dir, 9)
        make_runner(self.dir, CRASH).run_one(9)
        record = self.assert_left_pending(9, runner.EXIT_NONZERO)
        self.assertEqual(record["attempts"][0]["exit_code"], 3)
        self.assertIn("boom", record["attempts"][0]["stderr_tail"])
        with open(os.path.join(self.dir, "0009.agent.log")) as fp:
            self.assertIn("boom", fp.read())

    def test_good_orders_count_even_if_the_agent_died_on_the_way_out(self):
        """The orders file decides, not the exit code."""
        write_obs(self.dir, 10)
        self.assertEqual(make_runner(self.dir, WROTE_THEN_CRASHED)
                         .run_one(10), runner.OK)
        self.assertTrue(os.path.exists(self.orders_path(10)))

    def test_a_hanging_agent_is_killed_and_the_turn_left_pending(self):
        write_obs(self.dir, 11)
        started = time.time()
        make_runner(self.dir, HANGS, timeout=1).run_one(11)
        self.assertLess(time.time() - started, 20, "the timeout did not fire")
        record = self.assert_left_pending(11, runner.TIMEOUT)
        self.assertIn("no answer within", record["detail"])

    def test_the_runner_stops_on_a_failure_so_it_can_be_looked_at(self):
        write_obs(self.dir, 4)
        self.assertEqual(make_runner(self.dir, SILENT)
                         .run_forever(once=False), 1)

    def test_keep_going_leaves_the_failed_turn_behind(self):
        write_obs(self.dir, 4)
        engine = make_runner(self.dir, SILENT, keep_going=True)

        def move_on():
            while not os.path.exists(os.path.join(self.dir, "0004.agent.json")):
                time.sleep(0.01)
            write_obs(self.dir, 5)
            runner.write_json(self.orders_path(5), [])   # someone else played it
        threading.Thread(target=move_on, daemon=True).start()
        deadline = time.time() + 5
        while runner.pending_turn(self.dir) is not None and time.time() < deadline:
            turn = runner.pending_turn(self.dir)
            ok, _why = engine.actionable(turn)
            if ok:
                engine.run_one(turn)
            engine.done.add(turn)
            time.sleep(0.02)
        self.assertIn(4, engine.done)

    def test_a_retry_gets_a_clean_slate(self):
        """A proposal from the failed attempt must not be read as the next."""
        write_obs(self.dir, 6)
        proposed = os.path.join(self.dir, "0006.orders.proposed.json")
        with open(proposed, "w") as fp:
            fp.write("[{\"unit\": 1}]")
        make_runner(self.dir, SILENT).run_one(6)
        self.assertFalse(os.path.exists(self.orders_path(6)))

    def test_two_attempts_are_recorded_separately(self):
        write_obs(self.dir, 6)
        engine = make_runner(self.dir, SILENT, max_attempts=2)
        engine.run_one(6)
        self.assertEqual(len(self.record(6)["attempts"]), 2)

    def test_a_command_that_does_not_exist_is_a_failure_not_a_crash(self):
        write_obs(self.dir, 2)
        engine = runner.TurnRunner(
            self.dir, runner.AgentCommand("definitely-not-a-real-command"),
            poll=0.02, timeout=5)
        self.assertEqual(engine.run_one(2), runner.LAUNCH_FAILED)
        self.assertFalse(os.path.exists(self.orders_path(2)))


class SupersededTest(RunnerTestCase):
    def test_orders_for_a_turn_the_game_has_left_behind_are_discarded(self):
        """Freeciv can move on while the agent is thinking."""
        write_obs(self.dir, 20)
        # The agent takes its time, and the host times the turn out and
        # moves to 21 while it does.
        command = ('sleep 0.4; printf %s "[]" > "$FC_ORDERS"')
        engine = make_runner(self.dir, command)

        def move_on():
            time.sleep(0.1)
            runner.write_json(self.orders_path(20), [])
            write_obs(self.dir, 21)
        threading.Thread(target=move_on, daemon=True).start()
        self.assertEqual(engine.run_one(20), runner.SUPERSEDED)
        with open(self.orders_path(20)) as fp:
            self.assertEqual(json.load(fp), [])     # the host's, not ours
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, "0020.orders.proposed.json")))


class DirectWriteTest(RunnerTestCase):
    def test_an_agent_that_used_fcgame_play_is_noticed_not_failed(self):
        """`fcgame.py play` writes the real path. It has played the turn."""
        write_obs(self.dir, 30)
        command = ('printf %s \'[{"unit": 1}]\' > '
                   '"$FC_TURNS_DIR/0030.orders.json"')
        self.assertEqual(make_runner(self.dir, command).run_one(30),
                         runner.OK)
        record = self.record(30)
        self.assertIn("bypassing the validation gate", record["detail"])

    def test_a_direct_write_of_rubbish_is_still_reported(self):
        write_obs(self.dir, 30)
        command = 'printf %s "{" > "$FC_TURNS_DIR/0030.orders.json"'
        self.assertEqual(make_runner(self.dir, command).run_one(30),
                         runner.INVALID_ORDERS)


class RestartTest(RunnerTestCase):
    def test_a_pending_turn_is_picked_up_after_a_restart(self):
        """The runner died mid-turn; a new one finds the turn still waiting."""
        write_obs(self.dir, 15)
        with open(os.path.join(self.dir, "0015.brief.md"), "w") as fp:
            fp.write("half-finished brief from the dead runner")
        self.assertEqual(make_runner(self.dir).run_forever(once=True), 0)
        self.assertTrue(os.path.exists(self.orders_path(15)))

    def test_a_turn_already_given_up_on_is_not_retried_unasked(self):
        write_obs(self.dir, 16)
        make_runner(self.dir, SILENT).run_one(16)
        fresh = make_runner(self.dir, GOOD)
        ok, why = fresh.actionable(16)
        self.assertFalse(ok)
        self.assertIn("already given up on", why)
        self.assertEqual(fresh.run_forever(once=True), 1)
        self.assertFalse(os.path.exists(self.orders_path(16)))

    def test_retry_failed_tries_it_again(self):
        write_obs(self.dir, 16)
        make_runner(self.dir, SILENT).run_one(16)
        self.assertEqual(make_runner(self.dir, GOOD, retry_failed=True)
                         .run_forever(once=True), 0)
        self.assertTrue(os.path.exists(self.orders_path(16)))

    def test_a_new_game_restarts_the_turn_numbers(self):
        engine = make_runner(self.dir)
        engine.highest_seen = 40
        engine.done = {1, 2, 40}
        engine.watch_for_a_new_game(1)
        self.assertEqual(engine.done, set())

    def test_from_turn_ignores_an_older_game_left_in_the_directory(self):
        write_obs(self.dir, 3)
        engine = make_runner(self.dir, from_turn=10)
        ok, why = engine.actionable(3)
        self.assertFalse(ok)
        self.assertIn("--from-turn", why)

    def test_an_unreadable_observation_is_not_treated_as_a_turn(self):
        with open(os.path.join(self.dir, "0021.obs.json"), "w") as fp:
            fp.write('{"meta": ')            # a write that never finished
        ok, why = make_runner(self.dir).actionable(21)
        self.assertFalse(ok)
        self.assertIn("no readable observation", why)


class LockTest(RunnerTestCase):
    def test_a_second_runner_refuses_to_start(self):
        path = os.path.join(self.dir, "runner.lock")
        with runner.RunnerLock(path):
            with self.assertRaises(runner.RunnerBusy):
                with runner.RunnerLock(path):
                    pass

    def test_the_lock_is_released_when_the_runner_leaves(self):
        path = os.path.join(self.dir, "runner.lock")
        with runner.RunnerLock(path):
            pass
        with runner.RunnerLock(path):
            pass

    def test_the_lock_names_who_holds_it(self):
        path = os.path.join(self.dir, "runner.lock")
        with runner.RunnerLock(path):
            try:
                with runner.RunnerLock(path):
                    self.fail("two runners at once")
            except runner.RunnerBusy as exc:
                self.assertIn("pid %d" % os.getpid(), str(exc))

    def test_a_second_runner_process_refuses_and_the_first_carries_on(self):
        """The real case: two terminals, one game."""
        write_obs(self.dir, 1)
        with runner.RunnerLock(os.path.join(self.dir, "runner.lock")):
            proc = subprocess.run(
                [sys.executable, "fcgame.py", "run-agent",
                 "--turns-dir", self.dir, "--agent-command", "true",
                 "--once", "--shell"],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(proc.returncode, 3)
        self.assertIn("another runner is already watching", proc.stdout)


class BriefingTest(RunnerTestCase):
    def brief_for(self, turn):
        engine = make_runner(self.dir)
        runner.ensure_memory(engine.strategy, runner.STRATEGY_TEMPLATE)
        runner.ensure_memory(engine.journal, runner.JOURNAL_TEMPLATE)
        return runner.build_brief(engine.job_for(turn))

    def test_it_names_the_files_and_the_exact_output_path(self):
        write_obs(self.dir, 42)
        text = self.brief_for(42)
        self.assertIn("0042.obs.txt", text)
        self.assertIn("0042.obs.json", text)
        self.assertIn("0042.orders.proposed.json", text)
        self.assertIn("PLAYBOOK.md", text)

    def test_it_carries_the_previous_turn_result(self):
        write_obs(self.dir, 43)
        runner.write_json(os.path.join(self.dir, "0042.result.json"),
                          {"turn": 42, "results": ["unit 11: sent fortify"]})
        self.assertIn("unit 11: sent fortify", self.brief_for(43))

    def test_it_carries_the_wake_report_when_autoplay_stopped(self):
        write_obs(self.dir, 44)
        runner.write_json(os.path.join(self.dir, "0044.wake.json"),
                          {"turn": 44, "turns_played_under_policy": 6,
                           "why": [{"kind": "first_contact",
                                    "detail": "met the Celts"}]})
        text = self.brief_for(44)
        self.assertIn("first_contact", text)
        self.assertIn("met the Celts", text)

    def test_it_carries_durable_memory(self):
        write_obs(self.dir, 45)
        with open(os.path.join(self.dir, "strategy.md"), "w") as fp:
            fp.write("# Strategy\n\nTake the river valley, then Monarchy.\n")
        with open(os.path.join(self.dir, "journal.md"), "w") as fp:
            fp.write("T40: founded Neapolis on the whale.\n")
        text = self.brief_for(45)
        self.assertIn("Take the river valley", text)
        self.assertIn("founded Neapolis", text)

    def test_memory_files_are_created_with_an_explanation(self):
        write_obs(self.dir, 1)
        make_runner(self.dir).run_one(1)
        for name in ("strategy.md", "journal.md"):
            path = os.path.join(self.dir, name)
            self.assertTrue(os.path.exists(path), name)
            with open(path) as fp:
                self.assertTrue(fp.read().startswith("#"))

    def test_the_journal_is_carried_by_its_tail_not_in_full(self):
        write_obs(self.dir, 50)
        with open(os.path.join(self.dir, "journal.md"), "w") as fp:
            for i in range(500):
                fp.write("T%d: something happened.\n" % i)
        text = self.brief_for(50)
        self.assertIn("T499", text)
        self.assertNotIn("T1:", text)


class AgentCommandTest(RunnerTestCase):
    def job(self, turn=42):
        return runner.TurnJob(self.dir, turn,
                              os.path.join(self.dir, "strategy.md"),
                              os.path.join(self.dir, "journal.md"))

    def test_placeholders_are_filled_in(self):
        command = runner.AgentCommand("myagent --job {brief} --turn {turn}")
        self.assertEqual(command.render(self.job()),
                         ["myagent", "--job",
                          os.path.join(self.dir, "0042.brief.md"),
                          "--turn", "42"])

    def test_a_path_with_a_space_stays_one_argument(self):
        self.dir = tempfile.mkdtemp(suffix=" with space")
        command = runner.AgentCommand("myagent {obs}")
        self.assertEqual(len(command.render(self.job())), 2)

    def test_shell_mode_quotes_what_it_substitutes(self):
        self.dir = tempfile.mkdtemp(suffix=" with space")
        command = runner.AgentCommand("cat {obs} | myagent", shell=True)
        rendered = command.render(self.job())
        self.assertIn("| myagent", rendered)
        self.assertIn("'", rendered)        # the path got quoted

    def test_the_paths_also_arrive_as_environment_variables(self):
        env = self.job().env()
        self.assertEqual(env["FC_TURN"], "42")
        self.assertTrue(env["FC_ORDERS"].endswith("0042.orders.proposed.json"))

    def test_an_empty_command_is_refused(self):
        with self.assertRaises(ValueError):
            runner.AgentCommand("")

    def test_the_briefing_can_be_fed_on_stdin(self):
        write_obs(self.dir, 60)
        command = 'cat > "$FC_TURNS_DIR/seen.txt"; printf %s "[]" > "$FC_ORDERS"'
        make_runner(self.dir, command).run_one(60)
        with open(os.path.join(self.dir, "seen.txt")) as fp:
            self.assertIn("0060.orders.proposed.json", fp.read())

    def test_stdin_none_gives_the_agent_nothing_to_read(self):
        write_obs(self.dir, 61)
        engine = runner.TurnRunner(
            self.dir,
            runner.AgentCommand('cat > "$FC_TURNS_DIR/seen.txt"; '
                                'printf %s "[]" > "$FC_ORDERS"',
                                shell=True, stdin_mode="none"),
            poll=0.02, timeout=10)
        engine.run_one(61)
        with open(os.path.join(self.dir, "seen.txt")) as fp:
            self.assertEqual(fp.read(), "")


class ValidateOrdersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "orders.json")

    def write(self, text):
        with open(self.path, "w") as fp:
            fp.write(text)
        return runner.validate_orders(self.path)

    def test_a_list_of_objects_is_good(self):
        orders, problem = self.write('[{"unit": 1}, {"city": 2}]')
        self.assertIsNone(problem)
        self.assertEqual(len(orders), 2)

    def test_an_empty_list_is_good(self):
        orders, problem = self.write("[]")
        self.assertIsNone(problem)
        self.assertEqual(orders, [])

    def test_a_missing_file_is_reported(self):
        orders, problem = runner.validate_orders(self.path)
        self.assertIsNone(orders)
        self.assertIn("no orders file", problem)

    def test_an_empty_file_is_reported(self):
        orders, problem = self.write("   \n")
        self.assertIsNone(orders)
        self.assertIn("empty", problem)

    def test_a_list_of_strings_is_refused(self):
        orders, problem = self.write('["fortify everything"]')
        self.assertIsNone(orders)
        self.assertIn("not an object", problem)

    def test_prose_wrapped_around_the_json_is_refused(self):
        """A model that explains itself has not written an orders file."""
        orders, problem = self.write('Here are my orders:\n[{"unit": 1}]')
        self.assertIsNone(orders)
        self.assertIn("not valid JSON", problem)


class DryRunTest(RunnerTestCase):
    def test_it_shows_the_work_and_does_none_of_it(self):
        write_obs(self.dir, 70)
        lines = []
        engine = make_runner(self.dir, 'this-would-never-run')
        engine.log = lines.append
        self.assertEqual(engine.dry_run(), 0)
        text = "\n".join(lines)
        self.assertIn("this-would-never-run", text)
        self.assertIn("0070.orders.proposed.json", text)
        self.assertFalse(os.path.exists(self.orders_path(70)))

    def test_nothing_waiting_says_so(self):
        self.assertEqual(make_runner(self.dir).dry_run(), 1)


if __name__ == "__main__":
    unittest.main()
