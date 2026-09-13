"""The doorbell: a process that is awake so the model does not have to be.

`interactive.py` makes the seat *wait* for orders. `fcgame.py play` lets an
invocation that already exists answer one turn. Neither can bring an
invocation into being, and that is the whole problem: between invocations
the model is not slow, it is absent. A turn can sit unplayed for days
because nothing told anyone it was our move.

So: a small, dumb, always-on loop.

    observation appears -> deterministic filter -> one bounded invocation
      -> proposed orders -> validated -> submitted -> record -> back to waiting

The runner decides nothing about the game. It decides *when there is work*,
packages it, runs one agent, and checks that what came back is well formed.
Everything it knows about freeciv is the shape of a filename.

Two rules it will not bend:

* it never writes orders of its own. A failed agent leaves the turn pending
  and says so. An empty turn is a real move and must be chosen, not
  defaulted into by a crash.
* the agent writes `NNNN.orders.proposed.json`, not `NNNN.orders.json`. The
  host polls only for the latter, so nothing half-formed can reach the game;
  the rename into place is the runner's single, atomic act of submission.
"""

import errno
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

#: How much of an agent's output to keep in the per-turn record, and in the
#: per-turn log file. Enough to see a traceback, not enough to fill a disk.
TAIL_BYTES = 4000
LOG_BYTES = 256 * 1024

#: Journal lines carried into the briefing. The journal is durable memory,
#: not an archive to re-read in full every turn.
JOURNAL_TAIL_LINES = 60

OBS_RE = re.compile(r"(\d{4})\.obs\.json$")

#: Terminal statuses for one turn. Only "ok" submits.
OK = "ok"
NO_ORDERS = "no_orders"
INVALID_ORDERS = "invalid_orders"
EXIT_NONZERO = "exit_nonzero"
TIMEOUT = "timeout"
SUPERSEDED = "superseded"
LAUNCH_FAILED = "launch_failed"


# -- reading the turns directory ---------------------------------------

def turn_numbers(turns_dir):
    """Every turn that has an observation, newest last."""
    if not os.path.isdir(turns_dir):
        return []
    return sorted(int(m.group(1)) for m in
                  (OBS_RE.match(n) for n in os.listdir(turns_dir)) if m)


def pending_turn(turns_dir):
    """The turn waiting on us: the newest observation with no orders yet.

    Only the newest counts. An older unanswered observation means the game
    moved on without us -- answering it now would be answering a question
    about a board that no longer exists.
    """
    turns = turn_numbers(turns_dir)
    if not turns:
        return None
    latest = turns[-1]
    if os.path.exists(os.path.join(turns_dir, "%04d.orders.json" % latest)):
        return None                 # already answered; the host is thinking
    return latest


def read_json(path):
    """Parse a JSON file, or None if it is missing or not (yet) valid.

    Everything here is written write-then-rename, so a half-written file
    should be impossible -- but a file from an earlier game, a truncated
    disk, or an agent that used `>` rather than a rename all show up the
    same way, and none of them is worth crashing the runner over.
    """
    try:
        with open(path) as fp:
            return json.load(fp)
    except (IOError, OSError, ValueError):
        return None


def write_json(path, payload):
    tmp = path + ".partial"
    with open(tmp, "w") as fp:
        json.dump(payload, fp, indent=2)
        fp.write("\n")
    os.replace(tmp, path)


# -- one runner at a time ----------------------------------------------

class RunnerLock(object):
    """An advisory lock on the turns directory, held for the process's life.

    Two runners on one game would each launch an agent for the same turn,
    and whichever finished second would find its proposal superseded -- or
    worse, both would be mid-flight when the turn was submitted. `flock` is
    the right tool: the kernel drops it when the process dies, however it
    dies, so there is no stale lock file to reap after a crash.
    """

    def __init__(self, path):
        self.path = path
        self.fp = None

    def __enter__(self):
        self.fp = open(self.path, "a+")
        try:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            self.fp.seek(0)
            held = self.fp.read().strip() or "another process"
            self.fp.close()
            self.fp = None
            raise RunnerBusy(held)
        self.fp.seek(0)
        self.fp.truncate()
        self.fp.write("pid %d since %s\n" %
                      (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
        self.fp.flush()
        return self

    def __exit__(self, *exc):
        if self.fp is not None:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
            self.fp.close()
            self.fp = None
        return False


class RunnerBusy(Exception):
    """Another runner already holds this turns directory."""


# -- the job handed to one invocation ----------------------------------

class TurnJob(object):
    """Everything one bounded invocation needs, as paths.

    Deliberately paths and not content: freeciv's own files are the
    canonical tactical state, and copying them into a second store is how
    two sources of truth get started.
    """

    def __init__(self, turns_dir, turn, strategy, journal):
        self.turns_dir = turns_dir
        self.turn = turn
        self.strategy = strategy
        self.journal = journal

    def path(self, suffix):
        return os.path.join(self.turns_dir, "%04d.%s" % (self.turn, suffix))

    @property
    def obs(self):
        return self.path("obs.json")

    @property
    def obs_text(self):
        return self.path("obs.txt")

    @property
    def orders(self):
        """Where the agent writes. Not where the host reads."""
        return self.path("orders.proposed.json")

    @property
    def submitted(self):
        """Where the runner puts it once it is known to be well formed."""
        return self.path("orders.json")

    @property
    def brief(self):
        return self.path("brief.md")

    @property
    def record(self):
        return self.path("agent.json")

    @property
    def log(self):
        return self.path("agent.log")

    def fields(self):
        return {
            "turn": str(self.turn),
            "turns_dir": self.turns_dir,
            "obs": self.obs,
            "obs_text": self.obs_text,
            "orders": self.orders,
            "brief": self.brief,
            "strategy": self.strategy or "",
            "journal": self.journal or "",
        }

    def env(self):
        return dict(("FC_" + k.upper(), v) for k, v in self.fields().items())


# -- durable memory ----------------------------------------------------

STRATEGY_TEMPLATE = """# Strategy

The plan that outlives any one invocation. Freeciv's own files hold what is
happening; this holds what we are *trying to do* and why. Keep it short
enough to read every turn -- if it grows past a page it has stopped being a
plan and become a diary, and the diary is `journal.md`.

## Aim

(not set yet -- the first invocation should write one)

## Standing orders

Set a policy through the orders channel so most turns need no invocation at
all. See README, "Standing orders".

## Promises and grudges

(who we have made peace with, what we agreed to, who is not to be trusted)
"""

JOURNAL_TEMPLATE = """# Campaign journal

One short entry per decision point, newest at the bottom. What changed, what
was decided, and what to watch for next. This is the only memory that
crosses invocations besides the strategy file, so write it for a reader who
was not there -- because next turn, nobody was.
"""


def ensure_memory(path, template):
    """Create a durable-memory file with its explanation, if it is missing."""
    if path and not os.path.exists(path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as fp:
            fp.write(template)
    return path


def tail_lines(path, count):
    try:
        with open(path) as fp:
            lines = fp.read().splitlines()
    except (IOError, OSError):
        return []
    return lines[-count:]


def read_text(path, limit=8000):
    try:
        with open(path) as fp:
            text = fp.read()
    except (IOError, OSError):
        return ""
    if len(text) > limit:
        text = text[-limit:]
        text = "...(truncated)...\n" + text
    return text


def build_brief(job):
    """Assemble the whole situation for an invocation that remembers nothing.

    A wakeup has to be self-sufficient. Nothing here relies on a previous
    conversation still existing, because it does not.
    """
    out = []
    out.append("# Freeciv turn %d" % job.turn)
    out.append("")
    out.append("You are playing one turn of a game of Freeciv, as a player "
               "in a real game against a human and several AIs. This is the "
               "whole of your task: read the situation, decide, write the "
               "orders file, and stop. You will not be asked a follow-up "
               "question -- there is nobody to ask you one.")
    out.append("")
    out.append("## Read these")
    out.append("")
    out.append("* `%s` -- the situation, readable" % job.obs_text)
    out.append("* `%s` -- the same thing as JSON, with the order schema in "
               "`how_to_answer`" % job.obs)
    if job.strategy:
        out.append("* `%s` -- the standing plan" % job.strategy)
    if job.journal:
        out.append("* `%s` -- what previous turns decided" % job.journal)
    out.append("* `PLAYBOOK.md` -- rules that have already cost us a turn "
               "to learn. Read it.")
    out.append("")

    wake = read_json(os.path.join(job.turns_dir, "%04d.wake.json" % job.turn))
    if wake:
        out.append("## Why you were woken")
        out.append("")
        out.append("Autoplay under the standing orders stopped here:")
        out.append("")
        for why in wake.get("why", []):
            out.append("* **%s** -- %s" % (why.get("kind", "?"),
                                           why.get("detail", "")))
        out.append("")
        out.append("It played %s turn(s) under the policy before stopping."
                   % wake.get("turns_played_under_policy", "?"))
        out.append("")

    previous = read_json(os.path.join(job.turns_dir,
                                      "%04d.result.json" % (job.turn - 1)))
    if previous:
        out.append("## What last turn's orders did")
        out.append("")
        for line in previous.get("results", [])[:40]:
            out.append("* %s" % line)
        out.append("")
        out.append("('sent' only means the packet went out. The server's "
                   "verdict is in this turn's observation.)")
        out.append("")

    if job.strategy and os.path.exists(job.strategy):
        out.append("## Strategy (from `%s`)" % job.strategy)
        out.append("")
        out.append(read_text(job.strategy))
        out.append("")

    if job.journal and os.path.exists(job.journal):
        lines = tail_lines(job.journal, JOURNAL_TAIL_LINES)
        if lines:
            out.append("## Journal, most recent (from `%s`)" % job.journal)
            out.append("")
            out.extend(lines)
            out.append("")

    out.append("## Write your orders here")
    out.append("")
    out.append("    %s" % job.orders)
    out.append("")
    out.append("A JSON **list** of order objects -- the schema and examples "
               "are in `how_to_answer` in the observation. An empty list "
               "`[]` is a legitimate turn (it ends the phase unchanged), so "
               "write it deliberately if that is the decision, but never as "
               "a way out of being unsure.")
    out.append("")
    out.append("Write that path and no other. The game is **not** watching "
               "it; a separate process checks it is well formed and only "
               "then hands it to the game. So a malformed file costs you "
               "nothing but the turn, and a half-written one cannot reach "
               "the server. Do not run `fcgame.py play`, and do not write "
               "`%s` yourself." % job.submitted)
    out.append("")
    out.append("Then, if anything happened worth knowing next time, append "
               "a short entry to `%s`, and update `%s` if the plan itself "
               "changed. Those two files are your only memory."
               % (job.journal or "the journal", job.strategy or "the plan"))
    out.append("")
    return "\n".join(out) + "\n"


# -- invoking the agent ------------------------------------------------

class AgentResult(object):
    def __init__(self, status, exit_code=None, duration=0.0,
                 stdout="", stderr="", detail=""):
        self.status = status
        self.exit_code = exit_code
        self.duration = duration
        self.stdout = stdout
        self.stderr = stderr
        self.detail = detail

    def as_dict(self):
        return {"status": self.status, "exit_code": self.exit_code,
                "duration_seconds": round(self.duration, 1),
                "detail": self.detail,
                "stdout_tail": self.stdout[-TAIL_BYTES:],
                "stderr_tail": self.stderr[-TAIL_BYTES:]}


def substitute(text, fields):
    """Fill in `{obs}` and friends, and leave every other brace alone.

    `str.format` is the obvious thing and the wrong one: an agent command is
    quite likely to contain a literal brace -- a JSON example in a prompt, a
    shell expansion, an awk program -- and `format` would either swallow it
    or raise. Only the names we actually define are substituted.
    """
    return PLACEHOLDER.sub(
        lambda m: fields.get(m.group(1), m.group(0)), text)


PLACEHOLDER = re.compile(r"\{(\w+)\}")


class AgentCommand(object):
    """How to start one bounded invocation. Deliberately not Claude-shaped.

    The command is a template; every path the job involves is substituted
    into it by name, is exported as an `FC_*` environment variable, and the
    briefing is offered on stdin. Three ways in, because every CLI takes its
    input differently and the point of this layer is that swapping the model
    -- or replacing it with a shell script for a test -- changes one string.
    """

    def __init__(self, template, shell=False, stdin_mode="brief", cwd=None):
        if not template:
            raise ValueError("an agent command is required")
        self.template = template
        self.shell = shell
        self.stdin_mode = stdin_mode
        self.cwd = cwd

    def render(self, job):
        fields = job.fields()
        if self.shell:
            # Quote the substitutions: a path is data, and a path with a
            # space in it must not become two arguments.
            return substitute(self.template,
                              dict((k, shlex.quote(v))
                                   for k, v in fields.items()))
        return [substitute(part, fields)
                for part in shlex.split(self.template)]

    def describe(self, job):
        rendered = self.render(job)
        return rendered if self.shell else " ".join(shlex.quote(p)
                                                    for p in rendered)

    def run(self, job, timeout, log):
        """Run it once, bounded. Never raises for the agent's own failure."""
        rendered = self.render(job)
        env = dict(os.environ)
        env.update(job.env())
        stdin_text = (build_brief_text(job) if self.stdin_mode == "brief"
                      else None)
        started = time.time()
        try:
            proc = subprocess.Popen(
                rendered, shell=self.shell, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE if stdin_text is not None
                else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                # Its own process group, so a timeout kills the whole tree
                # rather than leaving the real work orphaned behind a
                # wrapper script.
                start_new_session=True, text=True)
        except (OSError, ValueError) as exc:
            return AgentResult(LAUNCH_FAILED, duration=time.time() - started,
                               detail="could not start the agent: %s" % exc)
        try:
            out, err = proc.communicate(stdin_text, timeout=timeout)
            status = OK if proc.returncode == 0 else EXIT_NONZERO
            return AgentResult(status, proc.returncode,
                               time.time() - started, out or "", err or "")
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, err = proc.communicate()
            return AgentResult(TIMEOUT, proc.returncode,
                               time.time() - started, out or "", err or "",
                               detail="no answer within %ds" % timeout)


def build_brief_text(job):
    """The briefing, preferring the file the runner already wrote."""
    text = read_text(job.brief, limit=200000)
    return text or build_brief(job)


def _kill_tree(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except OSError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


# -- what the agent came back with -------------------------------------

def validate_orders(path):
    """(orders, problem). Orders are a JSON list of objects, or nothing."""
    if not os.path.exists(path):
        return None, "no orders file at %s" % path
    try:
        with open(path) as fp:
            text = fp.read()
    except (IOError, OSError) as exc:
        return None, "could not read %s: %s" % (path, exc)
    if not text.strip():
        return None, "the orders file is empty"
    try:
        orders = json.loads(text)
    except ValueError as exc:
        return None, "the orders file is not valid JSON: %s" % exc
    if not isinstance(orders, list):
        return None, ("orders must be a JSON list, got %s"
                      % type(orders).__name__)
    for i, order in enumerate(orders):
        if not isinstance(order, dict):
            return None, ("order %d is a %s, not an object"
                          % (i, type(order).__name__))
    return orders, None


# -- the loop ----------------------------------------------------------

class TurnRunner(object):
    """Wait for a turn, wake an agent for it, check what came back.

    The whole of its judgement is: is there work, has it already been done,
    and is the answer well formed. Everything about the game itself belongs
    to the model on one side and to freeciv on the other.
    """

    def __init__(self, turns_dir, command, strategy=None, journal=None,
                 timeout=900, poll=2.0, max_attempts=1, retry_delay=5,
                 keep_going=False, from_turn=None, retry_failed=False,
                 wait_for_turn=0, log=None):
        self.turns_dir = turns_dir
        self.command = command
        self.strategy = strategy
        self.journal = journal
        self.timeout = timeout
        self.poll = poll
        self.max_attempts = max(1, max_attempts)
        self.retry_delay = retry_delay
        self.keep_going = keep_going
        self.from_turn = from_turn
        self.retry_failed = retry_failed
        #: in --once mode, how long to wait for a turn to appear at all
        self.wait_for_turn = wait_for_turn
        self.log = log or (lambda *a: None)
        #: turns this process has finished with, one way or the other
        self.done = set()
        self.highest_seen = None

    # -- paths
    def job_for(self, turn):
        return TurnJob(self.turns_dir, turn, self.strategy, self.journal)

    def ledger_path(self):
        return os.path.join(self.turns_dir, "runner.jsonl")

    def note(self, entry):
        """Append one line to the runner's own ledger.

        Separate from the journal on purpose: the journal is the model's
        memory and the model writes it. This is the machine's record of what
        it ran, and nothing reads it but a person looking for what went
        wrong.
        """
        entry = dict(entry)
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with open(self.ledger_path(), "a") as fp:
                fp.write(json.dumps(entry) + "\n")
        except (IOError, OSError):
            pass

    # -- deciding whether there is work
    def previous_record(self, turn):
        return read_json(self.job_for(turn).record)

    def already_settled(self, turn):
        """True if an earlier run of the runner gave up on this turn.

        A restart must not mean an automatic retry: if the agent failed the
        same way three times before the machine rebooted, waking it a fourth
        time unasked is how a loop starts. Brian can ask for the retry.
        """
        if self.retry_failed:
            return False
        record = self.previous_record(turn)
        if not record:
            return False
        return (record.get("status") != OK
                and len(record.get("attempts", [])) >= self.max_attempts)

    def actionable(self, turn):
        """(ok, why-not). A turn we can actually hand to an agent."""
        if turn is None:
            return False, None
        if self.from_turn is not None and turn < self.from_turn:
            return False, "turn %d is before --from-turn %d" % (turn,
                                                               self.from_turn)
        if turn in self.done:
            return False, None
        if self.already_settled(turn):
            return False, ("turn %d was already given up on (%s); "
                           "pass --retry-failed to try it again"
                           % (turn, self.previous_record(turn)
                              .get("status", "?")))
        obs = read_json(self.job_for(turn).obs)
        if obs is None:
            # Written atomically by the host, so this is a file from a dead
            # game or a disk in trouble; either way it is not our move.
            return False, "turn %d has no readable observation yet" % turn
        return True, None

    def watch_for_a_new_game(self, turn):
        """Turn numbers restart with every game; notice when they do."""
        if self.highest_seen is not None and turn < self.highest_seen:
            self.log("turn went back to %d from %d -- that is a new game; "
                     "forgetting what the last one did" % (turn,
                                                           self.highest_seen))
            self.done.clear()
            # The host archives the previous game's per-turn files, but not
            # the durable memory: a rejoin mid-game builds an
            # InteractiveAgent too, and losing the plan because the host was
            # restarted would be worse than carrying a stale one. So say it
            # out loud instead of deciding on Brian's behalf.
            self.log("note: %s and %s still describe the previous game. "
                     "Move them aside if this is a fresh start."
                     % (self.strategy, self.journal))
        self.highest_seen = turn

    # -- one turn
    def run_one(self, turn):
        """Wake an agent for this turn. Returns a status string."""
        job = self.job_for(turn)
        ensure_memory(self.strategy, STRATEGY_TEMPLATE)
        ensure_memory(self.journal, JOURNAL_TEMPLATE)

        # A proposal left over from a previous attempt, or a previous run,
        # would be read as this attempt's answer.
        _unlink(job.orders)

        with open(job.brief, "w") as fp:
            fp.write(build_brief(job))

        attempts = []
        status, detail, orders = None, None, None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                self.log("turn %d: retrying (attempt %d of %d)"
                         % (turn, attempt, self.max_attempts))
                time.sleep(self.retry_delay)
                _unlink(job.orders)
            self.log("turn %d: starting agent" % turn)
            result = self.command.run(job, self.timeout, self.log)
            _write_log(job.log, result)
            status, detail, orders = self.judge(job, turn, result)
            record = result.as_dict()
            record["attempt"] = attempt
            record["outcome"] = status
            record["outcome_detail"] = detail
            attempts.append(record)
            self.log("turn %d: agent %s after %.1fs%s"
                     % (turn, result.status, result.duration,
                        "" if result.exit_code is None
                        else " (exit %s)" % result.exit_code))
            if status in (OK, SUPERSEDED):
                break

        submitted = status == OK
        if submitted:
            self.log("turn %d: orders validated -- %d order(s) submitted"
                     % (turn, len(orders)))
            if not orders:
                self.log("turn %d: note, that is an empty turn -- the phase "
                         "ends unchanged" % turn)
        else:
            self.log("turn %d: NOT submitted -- %s" % (turn, detail))
            self.log("turn %d: the turn is still pending; diagnostics in %s"
                     % (turn, job.log))

        write_json(job.record, {
            "turn": turn,
            "status": status,
            "detail": detail,
            "submitted": submitted,
            "orders_count": len(orders) if orders is not None else None,
            "command": self.command.describe(job),
            "attempts": attempts,
        })
        self.note({"turn": turn, "status": status, "detail": detail,
                   "attempts": len(attempts)})
        return status

    def judge(self, job, turn, result):
        """What the agent left behind, and whether it may be submitted.

        The orders file decides, not the exit code: an agent that wrote good
        orders and then fell over on the way out has still played the turn,
        and an agent that exited 0 having written nothing has not.
        """
        if os.path.exists(job.orders):
            orders, problem = validate_orders(job.orders)
            if problem:
                return self._with_process_failure(result, INVALID_ORDERS,
                                                  problem)
            # The board may have moved while the agent was thinking: the host
            # can time out, the game can end, a policy can play the turn.
            # Check immediately before the rename, which is as close as we
            # can get.
            if pending_turn(self.turns_dir) != turn:
                _unlink(job.orders)
                return SUPERSEDED, ("turn %d stopped being the pending turn "
                                    "while the agent was thinking; its "
                                    "orders were discarded" % turn), None
            os.replace(job.orders, job.submitted)
            return OK, None, orders

        # Some agents will reach for `fcgame.py play` out of habit, which
        # writes the real path directly. That has already gone to the game,
        # so there is nothing left to gate -- but say so, because it skipped
        # the check that exists to catch a bad turn before the server sees it.
        if os.path.exists(job.submitted):
            orders, problem = validate_orders(job.submitted)
            if problem:
                return INVALID_ORDERS, ("the agent wrote %s directly, and %s"
                                        % (job.submitted, problem)), None
            return OK, ("submitted directly by the agent, bypassing the "
                        "validation gate"), orders

        return self._with_process_failure(
            result, NO_ORDERS, "no orders file at %s" % job.orders)

    @staticmethod
    def _with_process_failure(result, status, problem):
        """Report the agent's own failure alongside what it failed to write.

        "exit 3, and no orders file" is a diagnosis; either half alone is a
        symptom.
        """
        if result.status == OK:
            return status, problem, None
        cause = result.detail or "the agent exited %s" % result.exit_code
        return result.status, "%s, and %s" % (cause, problem), None

    # -- the loop proper
    def run_forever(self, once=False):
        with RunnerLock(os.path.join(self.turns_dir, "runner.lock")):
            self.log("runner up on %s; agent: %s"
                     % (self.turns_dir, self.command.template))
            self.log("waiting for turn")
            complained = set()
            give_up_at = time.time() + self.wait_for_turn
            while True:
                turn = pending_turn(self.turns_dir)
                if turn is not None:
                    self.watch_for_a_new_game(turn)
                ok, why = self.actionable(turn)
                if not ok:
                    if why and why not in complained:
                        complained.add(why)
                        self.log(why)
                    if once and time.time() >= give_up_at:
                        self.log("nothing is waiting for orders in %s; "
                                 "--once has nothing to do" % self.turns_dir)
                        return 1
                    time.sleep(self.poll)
                    continue

                self.log("turn %d observation detected" % turn)
                status = self.run_one(turn)
                self.done.add(turn)
                if once:
                    return 0 if status == OK else 1
                if status != OK and not self.keep_going:
                    self.log("stopping so the turn can be looked at. Fix it "
                             "and start the runner again, or use "
                             "--keep-going to leave failed turns behind.")
                    return 1
                self.log("waiting for turn")
                give_up_at = time.time() + self.wait_for_turn

    def dry_run(self):
        """Show what would be done for the pending turn, and do none of it."""
        turn = pending_turn(self.turns_dir)
        if turn is None:
            self.log("no turn is waiting for orders in %s" % self.turns_dir)
            return 1
        job = self.job_for(turn)
        ensure_memory(self.strategy, STRATEGY_TEMPLATE)
        ensure_memory(self.journal, JOURNAL_TEMPLATE)
        with open(job.brief, "w") as fp:
            fp.write(build_brief(job))
        self.log("turn %d is waiting." % turn)
        self.log("")
        self.log("would run: %s" % self.command.describe(job))
        self.log("stdin:     %s" % ("the briefing"
                                    if self.command.stdin_mode == "brief"
                                    else "nothing"))
        self.log("briefing:  %s" % job.brief)
        self.log("expects:   %s" % job.orders)
        self.log("then:      renamed to %s" % job.submitted)
        self.log("")
        self.log("-- %s --" % job.brief)
        self.log(build_brief(job))
        return 0


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_log(path, result):
    """Keep the agent's output where it can be read, and bounded."""
    parts = []
    if result.stdout:
        parts.append("-- stdout --\n" + result.stdout)
    if result.stderr:
        parts.append("-- stderr --\n" + result.stderr)
    text = "\n".join(parts)
    if len(text) > LOG_BYTES:
        text = ("...(%d bytes trimmed)...\n" % (len(text) - LOG_BYTES)
                + text[-LOG_BYTES:])
    try:
        with open(path, "w") as fp:
            fp.write(text)
    except (IOError, OSError):
        pass
