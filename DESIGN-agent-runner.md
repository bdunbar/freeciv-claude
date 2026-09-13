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

Of the six stages, the two weak ones are **authorized action** -- currently
just the tool grant -- and **verification**, which only checks that the
answer is well formed. Those are the two to build properly next, and neither
of them needs freeciv to work on.
