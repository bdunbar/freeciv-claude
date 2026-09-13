# A client for a player who isn't there

## The actual problem

The client is not the problem. The problem is that the player is
**intermittent**: between invocations the model does not exist. Not slow --
absent. Every mechanism built so far is a workaround for that one fact:

* `play` blocking in the background, so its *return* is a nudge
* the human pinging when it has stalled
* `/loop` polling on a timer

Three workarounds for a missing primitive: the game has no way to ring a
doorbell. Worse, the design assumes a player sitting in a chair, and there
isn't one.

Three consequences, all of which have already cost us:

1. **No wake signal.** Turns sat unplayed for days because nothing told
   anyone it was our move. *(Built, 2026-09-13: `fcbot/runner.py` and
   `./fcgame.py run-agent` -- a process that stays awake and starts one
   bounded invocation per turn. See the README.)*
2. **Every wakeup reloads context.** The situation is re-read and
   re-reasoned from scratch each turn.
3. **Plans live in the model's context, not in the system.** Strategy
   evaporates between sessions -- which is why "how to play" kept being
   rediscovered, at a turn or two each time.

## The shift

Stop treating one wakeup as one turn.

Today a 200-turn game costs 200 wakeups, nearly all of them spent
confirming that nothing interesting happened. Instead: **one wakeup per
decision point.** The client plays forward under standing orders and stops
when something actually needs judgement.

    now:      obs -> model -> orders -> obs -> model -> orders -> ...
    proposed: policy -> [autoplay N turns] -> interrupt -> model -> policy

A 200-turn game should need perhaps 20-30 wakeups. That is not an
optimisation, it is a different shape.

## Components

**1. Standing orders.** Not per-turn orders -- policy. Research order,
government target, per-city build rules, tile bias (food/shields/trade),
expansion target and candidate sites, unit doctrine, diplomatic stance
(what to accept unprompted, what never to give away).

**2. An interrupt spec.** What stops autoplay:

    first contact - treaty offered - enemy within N tiles - city attacked
    or lost - worklist empty - research idle - disorder or famine -
    settler arrived - every N turns regardless

**3. An autoplay engine.** Deterministic code executing the policy. This is
`fcbot/agent.py`, which already exists and was sidelined -- but in its
proper role. It is **not the player**: it carries out standing orders and
stops when it needs a decision. The strategy is the model's; the execution
is a rules engine. That distinction has to stay visible in the logs and the
names, not just in a footnote.

**4. A wake report.** When autoplay halts: why it stopped, the observation,
and a *diff* since the last decision -- "this stack has been getting closer
for three turns" -- which is the history the observation has never had.

**5. Durable plan state.** A plan file the client carries and the
observation echoes back: current strategy, city-site targets, promises made
to other players. So a wakeup restores context from the system rather than
from the model's memory of it.

## Language

Keep the protocol layer in Python. It is 2,851 lines of working, tested
freeciv 3.2 client -- 203 packets, delta compression, capability
negotiation, verified against a live server. Rewriting that in another
language is weeks of work for no functional gain; it was already ported
once, from 2.6, and doing it again buys nothing.

But the **file protocol is a language boundary**. The autoplay/policy
engine reads observations and writes orders, and nothing says it has to be
Python. If Go is wanted, that is the separable piece: a Go binary against
the same `NNNN.obs.json` / `NNNN.orders.json` files, with the protocol
layer untouched. Low risk, real separation, and the interesting work is
there rather than in the packet codec.

## The general shape

The freeciv-specific answer generalises, which is the point of writing it
down. When waking an agent is expensive:

* keep state **outside** the agent, so a wakeup is cheap and self-sufficient
* let **deterministic code** handle everything that does not need judgement
* **aggregate** events rather than firing on each one
* wake on **exceptions**, not on ticks
