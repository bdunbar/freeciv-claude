# What the runner taught, for the next thing that isn't freeciv

`DESIGN-intermittent.md` works out why the seat needed a runner.
This is the part that is not about freeciv.

The runner is a prototype of a shape that keeps coming up wherever an agent
has to act on something that happens while nobody is watching -- a
Kubernetes event, a CloudWatch alarm, a queue, a PR, another agent:

    event -> deterministic filter -> bounded model invocation
          -> authorized action -> verification -> durable record

All six stages exist in `fcbot/runner.py`, minimally, in about 600 lines.
Playing one game through it is the only evidence any of this has, which is
worth remembering while reading it.

## The reframing everything else follows from

**The agent is not a process. It is a function call with a large, variable
cost.**

It cannot hold a socket, a subscription, a watch, or a session. Anything
that requires continuous presence has to live somewhere else. That inverts
the usual mental model, in which "the agent" is the system and the tools
hang off it: here the deterministic runner *is* the system, and the model is
an expensive subroutine it calls when it reaches something it cannot decide.

Every rule below is a consequence of that one sentence. If a design starts
to violate one of them, it is usually because somebody has quietly gone back
to imagining a process.

## What the game actually demonstrated

### The missing primitive was invocation creation, not communication

Three notification mechanisms were built before the runner -- `play`
blocking in the background so its return was a nudge, a human pinging when
things stalled, `/loop` on a timer -- and all three failed in exactly the
same way. Each could only tell a session that *already existed* that there
was work. None could bring one into being.

So something deterministic has to be continuously awake, and it cannot be
the model. That is the entire architecture, and nothing about it is
freeciv-shaped.

### The agent must never touch the authoritative path

The agent writes `NNNN.orders.proposed.json`. The host polls only for
`NNNN.orders.json`. The runner parses the proposal, checks it, and performs
one `os.replace()`.

The rename **is** the act of submission, and it belongs to the deterministic
code. Nothing half-written, half-thought, or half-finished can reach the
server, because the path the server watches is only ever written atomically,
by something that is not a model.

Substitute `terraform plan` -> policy check -> `apply`, or proposed manifest
-> admission controller -> apply. The structure does not change.

### Absence of a decision is not a benign decision

The runner will not write an empty orders list. A crash, a timeout, prose
instead of JSON -- each leaves the turn pending, loudly, and stops.

This is the rule with the sharpest teeth in an operational setting, because
the tempting default is always available and always wrong: a failed agent
must not yield a scale-to-zero, an empty allowlist, an empty firewall rule,
or a deploy of nothing. Failure has to leave the work **pending and
visible**. "The agent did not answer" and "the agent answered: do nothing"
are different facts and must not collapse into each other.

In the game, `[]` is a legal move -- it ends the phase unchanged. That is
exactly why it has to be *chosen* and can never be *defaulted into*.

### Reconcile from observed state; keep no authoritative memory

`pending_turn()` is derived entirely from what is on disk. The runner's
in-memory set of finished turns is an optimisation, not a source of truth.
Restart is therefore free, and correctness never depends on what the runner
remembers.

This is a controller loop -- observed state versus desired state -- arrived
at by following the failure cases rather than by imitating Kubernetes, which
is mild evidence it is the right shape.

### Check supersession at commit time, not decision time

The agent thinks for 100 seconds. The world moves while it thinks.

So the check that the turn is still the pending one happens immediately
before the rename, not when the job was packaged. There is still a window --
there always is -- but it is as small as the code can make it.

This is compare-and-swap: `resourceVersion` conflicts, conditional writes,
optimistic concurrency. Rediscovered rather than invented, which is the
usual outcome of taking concurrency seriously.

### Durable memory holds intent, not state

`strategy.md` and `journal.md` contain no game state on purpose. Not one
unit, not one tile. Freeciv's own files are the only copy of what is
happening, and the briefing points at them.

The rule generalises cleanly and is easy to get wrong under pressure: never
mirror the cluster into the agent's memory. A second store of changing state
drifts, and a confidently stale store is worse than no store. What belongs
in durable memory is what the system of record cannot tell you -- what we
are trying to do, what we promised, what we already ruled out and why.

### The deterministic filter is where the economics live

Turns 2-9 of the first game were played by `fcbot/policy.py` with zero model
invocations. One decision point in ten.

Wake on exceptions, not on ticks. The filter is not a cost optimisation
bolted on afterwards; it is what makes the pattern affordable enough to run
at all.

### Time is the adversary, not the network

Ordinary integration risk is that the call fails. Here the risk is that the
call **succeeds slowly**.

An invocation takes one to two minutes of wall clock. Every fact it read at
the start may be false by the time it writes, and it has no way to find out:
it cannot poll, it is not watching anything, and it will not be told. The
whole of its world is the snapshot it was handed.

This is not the latency an ordinary distributed system budgets for. It is
enormous, highly variable, and metered. It means:

* the window between read and commit is measured in **minutes**, so anything
  that can change in minutes must be re-checked by the deterministic layer
  immediately before the commit, not trusted from the briefing;
* an agent cannot be given work whose correctness depends on the world
  holding still, unless something outside it is holding the world still;
* "retry the invocation" is not free the way retrying an HTTP call is free,
  so the filter that decides whether to invoke at all carries more weight
  than any retry policy.

Freeciv was gentle here: the server is untimed and will wait all night. A
foreign system that expires a lock, closes a window, or moves a market
while the agent thinks is the same problem without the mercy.

### Never let the agent be the sole author of the record of what it did

This is the finding to build policy around, because it was observed rather
than reasoned to, and because it is the one that quietly corrupts everything
downstream.

On turn 1 the invocation wrote a detailed journal entry describing a turn it
had **not played**: which city it founded, on which tile, and why. Confident,
specific, and false. It caught itself on re-reading the observation and
recovered, and the runner recorded a clean success.

Had it not caught itself, the durable record would have said the turn was
played. Nothing else would have disagreed, because nothing else was writing
a record.

So the audit trail must come from the deterministic layer observing
outcomes, never from the agent's self-report:

* `runner.jsonl` and `NNNN.agent.json` are written by the runner. They say
  what was actually run and what actually landed.
* `journal.md` is written by the model. It says what the model believed and
  intended.

Keeping those two separate was a guess when this was built and turned out to
matter more than expected. An agent's account of its own actions is
**testimony, not telemetry**. Store it, use it for continuity, and never let
it be the thing you verify against.

### Agents perform durable side effects out of order

This one was not predicted. It was observed.

On turn 1 the invocation wrote its journal entry -- a durable side effect --
*before* writing the orders file. It then re-read the observation, noticed
the turn was unchanged, worked out that nothing had been sent, re-derived
the decision and wrote the orders properly. It also left a note to itself
about the mistake. It recovered, and the runner recorded a clean success.

The lesson survives the recovery: an agent will commit side effects in the
wrong order, and cannot be relied on to notice. The real commit must be
last, must be the only thing that counts, and must be performed by the
deterministic layer. Everything the agent writes before that point is a
draft, including its own memory.

## What this has not taught

Being clear about this matters more than the list above, because the list
above is the part that is easy to over-generalise from one game.

* **Partial irreversible work is untouched.** A turn's orders go in as one
  file. An agent that makes three of seven AWS calls and then times out is a
  materially harder problem, and there is no compensation, rollback or
  saga here at all. The single atomic commit is the trick that makes
  everything else simple, and most real systems do not offer one.
* **`os.replace()` and `flock` are single-machine luxuries.** Neither
  survives crossing a node boundary. The duplicate-runner protection is
  advisory and local; a real deployment needs a lease.
* **Verification is of shape, not intent.** A well-formed order list is
  submitted. `orders.py` refuses the illegal, the server refuses the rest,
  and nothing anywhere asks whether a legal move was a good one. In
  production that is the expensive half, and it is entirely missing.
* **There is no human-in-the-loop gate.** Anything shape-valid commits
  automatically. Adequate for a game; not for an IAM policy.
* **The permission boundary is the tool grant and nothing else.**
  `--allowedTools` is the real boundary, not the briefing text. Instructions
  in a prompt are not a control, and the briefing is assembled partly from
  input other players wrote.
* **One game.** Low stakes, single tenant, one machine, no adversary with
  anything to gain.

Worth listing the gifts freeciv gave us, because a reader will not get them
next time: the server **waits forever**, there is a **single writer**, the
commit is a **local atomic rename**, there is **one tenant**, there is **no
adversary**, and the entire turn commits as **one file**. Remove any one of
those and something above gets substantially harder. Remove the last one and
most of it has to be redesigned.

Of the six stages, the two weak ones are **authorized action** -- currently
just the tool grant -- and **verification**, which only checks that the
answer is well formed. Those are the two to build properly next, and neither
of them needs freeciv to work on.
