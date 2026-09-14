# freeciv-agent

Play a game of Freeciv against Claude. You use your normal Freeciv client;
Claude joins the same server as another player, through the same network
protocol, under the same rules and the same fog of war.

Verified against `freeciv-server` 3.2.5, ruleset `classic`.

Ubuntu 24.04 ships freeciv 3.1 in apt, which will not do. The Flathub
package has a matching 3.2 client *and* server in one install, and is what
`fcbot/server.py` launches by default:

    flatpak install flathub org.freeciv.gtk322

## Quick start

Three terminals. The first hosts the game and holds Claude's seat:

    ./fcgame.py host --ai 3 --skill hard --tiles-per-player 250

The second is your own client — the host prints this line for you:

    flatpak run org.freeciv.gtk322 -a -p 5556 -s localhost

Pick a nation, click **Ready**, and the game starts.

The third is what makes Claude actually play. Without it the seat writes an
observation each turn and waits forever, because nothing wakes a model:

    ./fcgame.py run-agent --agent-command 'claude -p --allowedTools Read Write Edit Glob'

That is the whole setup. The runner notices each waiting turn, starts one
fresh Claude for it, checks the orders it wrote, and submits them. Most
turns are played by the standing-orders engine without waking anything;
see "Waking Claude for a turn" for what it does and what it lets the model
do.

### Options worth knowing

    --ai 3                   computer players besides you and Claude
    --skill hard             novice easy normal hard cheating experimental
    --tiles-per-player 250   land tiles each (server default 100, max 1000)
    --map-size 8             OR the whole map in thousands of tiles (max 2048)
    --topology square        square (default here) iso hex iso-hex
    --nation Roman           what Claude plays
    --save-each-turn         a save every turn, so the game survives a reboot
    --timeout 0              server turn timeout; 0 means untimed

`--tiles-per-player` and `--map-size` are mutually exclusive: the first
sizes the map from the player count, the second sets it outright. Passing
neither gives you the server's default of 100 tiles per player, which is
cramped for five players — everyone is a neighbour by turn 10.

A bigger map costs model invocations, not just turns: more exploring, more
first contacts, more settling sites to choose, and every one of those is a
fresh invocation.

On the runner side:

    --timeout 900            seconds one invocation may take
    --once                   play a single turn and exit
    --dry-run                print the briefing and the command, run nothing
    --keep-going             carry on past a turn the agent failed

### Starting a fresh game

The host archives the previous game's per-turn files by itself. It does
**not** touch the two durable memory files, because a mid-game `rejoin`
also builds an `InteractiveAgent` and losing the plan to a host restart
would be worse than carrying a stale one. So before a new game:

    mkdir -p games/turns/game-$(date +%Y%m%d)
    mv games/turns/strategy.md games/turns/journal.md games/turns/runner.jsonl \
       games/turns/game-$(date +%Y%m%d)/

Skip it and Rome wakes on turn 1 holding the last game's map reading, city
sites and treaty promises, all stated with confidence and none of it true.
Anything in that journal worth keeping belongs in `PLAYBOOK.md`, which is
committed and survives on purpose.

## Playing it

`PLAYBOOK.md` is what has been learned by playing: rules that reverse a
decision, how the AIs actually behave in negotiation, what a good opening
looks like. Read it before a game. It exists because the alternative is
rediscovering the same things every session, at a turn or two each.

## Map shape

Freeciv 3.2 defaults to iso-hex — hexagonal tiles. `fcgame.py host` defaults
to `--topology square` instead, because that is the style this game gets
played in. All four are available:

    --topology square    overhead squares, the "classic" look (default here)
    --topology iso       the same square tiles, drawn as diamonds
    --topology hex       hexagons, overhead
    --topology iso-hex   hexagons, isometric — freeciv 3.2's own default

Your GTK client picks a tileset to match whichever the server is running, so
nothing needs setting on your side. The seat handles all four: hex maps drop
one diagonal from the eight directions — plain hex has no NW/SE, iso-hex no
NE/SW — and sending a dropped direction gets the whole orders packet
rejected, so `fcmap.py` works that out from the topology rather than
assuming.

## Who is playing

Two modes, and the difference matters:

    --mode interactive   (default) Claude decides every move
    --mode auto          fcbot/agent.py decides, by fixed rules

**Interactive** is the real thing. Each turn the game writes out everything
it can see and then blocks, untimed, until a file of orders appears:

    games/turns/0042.obs.json     what we can see
    games/turns/0042.obs.txt      the same thing, readable
    games/turns/0042.orders.json  <- you (or Claude) write this
    games/turns/0042.result.json  what each order did

Nothing moves until those orders are written, so a turn can take as long as
it takes; your client just shows the seat as still thinking. Run the host
under `tmux` or `nohup` and the game outlives the session that started it.
Add `--save-each-turn` and it outlives a reboot.

To see what the seat is currently looking at, without touching the game:

    ./fcgame.py status

**Auto** mode is the scripted fallback. `fcbot/agent.py` is a few hundred
lines of if-statements -- it does not wage war, has no diplomacy, and is a
weak opponent. It logs in as `fcbot` rather than `claude` precisely so a
game against the script is never mistaken for a game against the model.

### Standing orders

Playing one turn per wakeup spends nearly every wakeup confirming that
nothing happened. Instead the seat can be given a **policy** -- what to
research, what each city builds, what units of each kind are for -- and it
plays forward under that until something the policy does not cover happens.
Then it stops and writes `NNNN.wake.json` saying why.

A policy arrives through the ordinary orders channel, so one answer can
both play this turn and set what happens after it:

```json
[
  {"unit": 112, "activity": "fortify"},
  {"policy": {
     "research": ["Ceremonial Burial", "Alphabet", "Code of Laws", "Monarchy"],
     "government": "Monarchy",
     "cities": {"default": {"build": [["unit", "Warriors"],
                                      ["unit", "Settlers"]],
                            "tiles": "food"}},
     "units": {"Explorer": "explore", "Workers": "auto_worker",
               "Settlers": "hold", "*": "fortify"},
     "wake_on": {"enemy_within": 4, "every_n_turns": 10}
  }}
]
```

It is **conservative on purpose**: it carries out what the policy says and
stops for everything else. A unit of a type the policy never mentions, a
city with no build rule, an exhausted research plan, a treaty waiting on us,
first contact with anyone, disorder, famine, a hostile unit inside
`enemy_within` tiles, a city lost -- each of those stops it, and when it
stops it sends *nothing*, because a half-played turn is worse than either.

The strategy is the model's and the execution is a rules engine. That line
matters: `fcbot/policy.py` decides nothing the policy did not already say,
and where it would have to, it raises an interrupt instead.

### Playing a turn

The observation/orders files are a good way for two processes to hand a game
back and forth, but a poor thing to drive by hand: every turn costs a check
for the observation, a write of the orders, and a wait for the reply, with
the turn number tracked in between. `play` is all three in one call:

    ./fcgame.py play --orders '[{"unit": 112, "activity": "fortify"}]'

It finds the turn that is waiting, submits the orders, prints what the
server made of each one, and then blocks until the next observation and
prints that. `--orders -` reads them from stdin instead; omitting `--orders`
sends none, which ends the phase unchanged and is a legitimate turn.

Because it blocks until the next observation, running it in the background
turns "is it my turn?" from a question you have to keep asking into one you
get told the answer to — the call returns exactly when there is something to
decide.

### Orders

A JSON list, applied in order. Tiles are an index or an `[x, y]` map
coordinate; units, cities and techs are named, not numbered:

```json
[
  {"unit": 112, "goto": [14, 22], "then": "found_city"},
  {"unit": 115, "activity": "fortify"},
  {"unit": 118, "activity": "explore"},
  {"city": 131, "build": ["unit", "Phalanx"]},
  {"city": 131, "worklist": [["improvement", "Temple"]]},
  {"city": 131, "buy": true},
  {"city": 131, "work_tile": [15, 21]},
  {"city": 131, "stop_working": [16, 21]},
  {"city": 131, "specialist": {"from": "elvis", "to": "scientist"}},
  {"research_goal": "Currency"},
  {"rates": {"tax": 30, "luxury": 0, "science": 70}},
  {"government": "Monarchy"},
  {"chat": "Nice city. It would be a shame if something happened to it."},
  {"diplomacy": "offer", "with": "Pakal", "clauses": ["ceasefire"]},
  {"diplomacy": "accept", "with": "Brennus"}
]
```

### Cities

Each city reports the two boxes it is filling -- shields toward the current
build, food toward the next citizen -- with `food.box` and
`food.turns_to_grow` alongside `turns_to_completion`, so neither deadline has
to be guessed. A unit's `population_cost` is on the production line, because
a size-2 city can never finish Settlers however many shields it banks.

`worked_tiles` and `free_tiles` are the lever for when food or shields are
the binding constraint: `stop_working` frees a tile into a specialist and
`work_tile` claims one, both by `[x, y]`. Tile output is the ruleset's flat
terrain-plus-resource figure -- the right ordering to choose by, not the
exact number a governor would report.

Each city also carries `warnings`: the things that quietly cost turns if
nobody notices them -- a build blocked on population, shields banked past
the cost and being wasted, a negative food surplus, disorder.

### Diplomacy

A treaty is a *meeting* with clauses on the table, and it happens only once
both sides have accepted the table as it stands -- so any clause added later
clears both acceptances. `offer` does the whole dance: opens a meeting if
there is none, puts the clauses down, and accepts our side last.

```json
[
  {"diplomacy": "offer", "with": "Pakal", "clauses": ["ceasefire"]},
  {"diplomacy": "offer", "with": "Brennus", "clauses": [
      "peace",
      {"type": "advance", "value": "Alphabet"},
      {"type": "gold", "value": 50, "from": "them"}
  ]},
  {"diplomacy": "accept", "with": "Brennus"},
  {"diplomacy": "break", "with": "Pakal"}
]
```

Clauses are `ceasefire` (only from war), `peace` (from war or ceasefire),
`alliance`, `embassy`, `vision`, `shared_tiles`, `map`, `seamap`, and with a
value `advance`, `gold` and `city`. `"from"` says who hands the thing over:
`"me"` by default, `"them"` to ask for it instead. Other actions are `meet`,
`withdraw`, `cancel_meeting`, `break` (one step down: alliance -> peace ->
war) and `stop_vision`.

Whether you can talk to someone at all is in the observation's diplomacy
section as `can_negotiate_now`: an embassy makes it permanent, plain contact
lapses a few turns after you last met. Open meetings, who put what on the
table, and who is waiting on whom are under `meetings`; the other side's
answers -- an AI's reason for refusing, a treaty signed or broken -- come
back under `diplomatic_news`.

A bad order does not cost you the turn: it is reported as `FAILED` in the
result file and the rest still run. `sent` in that file means the packet
went out -- whether the server honoured it shows up in the next
observation, which is the only real verdict.

## Waking Claude for a turn

Three things in here look similar and do different jobs. The difference is
the whole reason the runner exists:

| | what it does | what it cannot do |
|---|---|---|
| `fcbot/interactive.py` | makes the freeciv seat **wait**: writes `NNNN.obs.json` and blocks until `NNNN.orders.json` appears | nothing tells anyone the observation is there |
| `./fcgame.py play` | lets a model invocation that **already exists** answer one turn in a single call | it cannot create that invocation |
| `./fcgame.py run-agent` | stays awake, notices the waiting turn, and **starts a fresh invocation** for it | it decides nothing about the game |

The problem is not that the model is slow between turns. It is that between
invocations the model does not exist. Polling a file cannot bring one into
being, and a tool call left blocking for three days is not a design. So
something that is always awake has to do it:

    observation appears -> deterministic filter -> one bounded invocation
      -> proposed orders -> validated -> submitted -> record -> back to waiting

### Running it

Start the host as usual, then, in another terminal:

    ./fcgame.py run-agent \
      --agent-command 'claude -p --allowedTools Read Write Edit Glob' \
      --timeout 900

That is all of the setup. The runner watches `games/turns`, and each time a
turn comes up it starts one `claude -p`, hands it the briefing on stdin,
waits, checks the orders it wrote, and submits them. The log says what it is
doing:

    waiting for turn
    turn 42 observation detected
    turn 42: starting agent
    turn 42: agent ok after 71.3s (exit 0)
    turn 42: orders validated -- 6 order(s) submitted
    waiting for turn

Useful while trying it out:

    --dry-run       print the command and the whole briefing, run nothing
    --once          play one turn and exit  (with --wait N, wait N seconds
                    for a turn to turn up first)
    --keep-going    carry on after a turn the agent failed, rather than
                    stopping so it can be looked at

### The agent command

The command is a template, not a Claude invocation with a wrapper around it.
Everything the job involves reaches the command three ways, because every
CLI takes its input differently:

* **placeholders** — `{brief} {obs} {obs_text} {orders} {turn} {turns_dir}
  {strategy} {journal}`, substituted into the command. Braces that are not
  one of those names are left alone, so a command containing JSON or a shell
  expansion is safe.
* **environment** — the same values as `FC_BRIEF`, `FC_OBS`, `FC_ORDERS`,
  `FC_TURN` and so on.
* **stdin** — the briefing, unless `--stdin none`.

So a shell script, a different model's CLI, or a one-line fake for a test
are all equally valid agents:

    --agent-command 'my-agent --job {brief} --out {orders}'
    --shell --agent-command 'cat {obs_text} | some-model > {orders}'

### What the agent is told, and what it must write

The runner writes `NNNN.brief.md` before each invocation. A wakeup has to be
self-sufficient, because nothing of the previous one survives, so the
briefing carries: the paths to the observation and its readable twin, the
wake report if autoplay stopped for a reason, what last turn's orders
actually did, the durable strategy, the tail of the journal, a pointer to
`PLAYBOOK.md`, and the exact path to write.

That path is **not** the one the game reads. The agent writes
`NNNN.orders.proposed.json`; the host polls only for `NNNN.orders.json`.
The runner parses the proposal, checks it is a JSON list of objects, checks
the turn is still the pending one, and only then renames it into place. The
rename is atomic and is the runner's single act of submission, so a
half-written or half-thought file cannot reach the server.

The runner will **never** write orders of its own. An agent that crashed,
timed out, wrote prose, or wrote nothing leaves the turn pending and the
reason in the log — because an empty orders list is a real move (it ends the
phase unchanged) and has to be chosen, not fallen into.

### Durable memory

Two files, both created with an explanation the first time the runner runs:

    games/turns/strategy.md   the plan that outlives an invocation
    games/turns/journal.md    one short entry per decision point

Paths are configurable with `--strategy` and `--journal`. They hold *intent*
— what we are trying to do, what we promised whom — and deliberately not
game state: freeciv's own files are the only copy of that, and a second
store that drifts out of date is worse than none. Nothing summarises them
automatically yet; the invocation is asked to keep them, and the journal is
carried into the briefing by its tail rather than in full.

The runner keeps its own records separately, which is the machine's account
rather than the model's memory: `NNNN.agent.json` (status, attempts, exit
code, output tails), `NNNN.agent.log` (the agent's output, capped), and
`runner.jsonl`.

### When things go wrong

Every failure case leaves the turn pending and says so:

| | |
|---|---|
| agent wrote nothing | `no_orders`, turn left pending |
| agent wrote prose, or `{...}` instead of `[...]` | `invalid_orders`, nothing submitted |
| agent exited nonzero | reported with its exit code and stderr tail — but if it wrote good orders first, the turn still counts |
| agent hung | killed at `--timeout`, whole process group, turn left pending |
| freeciv moved on meanwhile | `superseded`; the stale proposal is discarded, never submitted |
| observation half-written | not treated as a turn; the runner waits |
| two runners started | the second refuses (`flock` on `games/turns/runner.lock`, released by the kernel however the first dies) |

A failed turn stops the runner by default, so it can be looked at rather
than retried into a loop. Starting it again does **not** silently retry a
turn an earlier run gave up on — `--retry-failed` asks for that explicitly.
`--max-attempts N` retries within a single run.

Turn numbers restart with every game. The host archives the previous game's
per-turn files into `previous-<timestamp>/`, but not `strategy.md` and
`journal.md`: a mid-game rejoin builds an `InteractiveAgent` too, and losing
the plan because the host was restarted would be worse than carrying a stale
one. The runner notices the turn number going backwards and says so; moving
those two files aside for a new game is yours to do.

### What you are agreeing to

`--agent-command` runs whatever you put in it, as you, with your
environment. There is no sandbox here, and `--shell` adds a shell. That is
worth saying plainly because the command above hands a model a `Write` tool
and a repository:

* the tools you grant are the permission boundary. `claude -p --allowedTools
  Read Write Edit Glob` can read and write any file it can reach; adding
  `Bash` gives it your shell. Grant the least that lets it play a turn.
* the briefing is assembled from the observation, the journal, and
  `PLAYBOOK.md`. Chat from other players in the game reaches the model
  through the observation, as game content — do not put anything in the
  journal or the strategy file that you would not want acted on.
* the runner validates **shape**, not intent: a well-formed order list is
  submitted. `orders.py` refuses the illegal ones, and the server refuses
  the rest, but nothing here asks whether a legal move was a good idea.
* the model runs unattended, on your account, once per turn. `--timeout`
  bounds one invocation; nothing bounds how many turns a game has.


## How it works

Claude is a real client, not a puppet. `fcbot/protocol/` implements the
freeciv 3.2 network protocol, generated from freeciv's own machine-readable
spec (`common/networking/packets.def`, vendored at
`fcbot/protocol/spec/packets.def`) rather than reverse-engineered:

* **framing** — `uint16 length | type | body`, with zlib-compressed
  multi-packet chunks (`length >= 16385`) unpacked transparently. The type
  field is one byte during login and two afterwards: freeciv widens it once
  the join is accepted, since 3.2 has packet numbers above 255.
* **delta compression** — each packet type caches the last packet seen, keyed
  by its `key` fields; a bitvector says which non-key fields are present.
  Scalar bools are folded into that bitvector rather than sent separately.
* **capability negotiation** — optional caps (`year32`, `techloss_forgiveness`)
  change which fields exist on the wire, so they are resolved at login and
  applied to every encode and decode.

Because it is a real client, the server enforces fog of war on Claude exactly
as on you: it only ever learns what a human in its seat would see.

## What's here

| file | role |
|---|---|
| `fcgame.py` | CLI: `host` a game, `play` a turn, `run-agent` to wake one per turn, `status` to look |
| `fcbot/protocol/pdef.py` | parser for freeciv's `packets.def` |
| `fcbot/protocol/dataio.py` | wire encodings (mirrors `common/dataio.c`) |
| `fcbot/protocol/codec.py` | packet encode/decode incl. delta compression |
| `fcbot/protocol/connection.py` | socket, framing, compression, login |
| `fcbot/state.py` | game state assembled from the packet stream |
| `fcbot/fcmap.py` | topology: tile indices, native/map coords, directions |
| `fcbot/fcpath.py` | pathfinding over tiles we actually know |
| `fcbot/client.py` | player actions: orders, production, research, rates, treaties |
| `fcbot/observe.py` | what we can see, shaped to be read (including the map) |
| `fcbot/orders.py` | JSON orders resolved to real actions |
| `fcbot/policy.py` | standing orders, and the exceptions that stop them |
| `fcbot/interactive.py` | the turn loop: autoplay under policy, or wait for the model |
| `fcbot/runner.py` | the process that stays awake and starts one invocation per turn |
| `fcbot/agent.py` | the scripted fallback agent (`--mode auto`) |
| `fcbot/server.py` | launches and drives `freeciv-server` |
| `PLAYBOOK.md` | what playing the game has taught, kept between sessions |
| `DESIGN-intermittent.md` | why an absent player needs a different client |
| `DESIGN-agent-runner.md` | what the runner taught, for the next thing that isn't freeciv |
| `legacy/` | an earlier savegame-editing approach, superseded |
| `preserve-2.6.6/` | the old 2.6.6 debs, kept only as a historical reference |

## Notes on the 3.2 protocol

Porting from 2.6 was mostly regenerating from the newer `packets.def`, but a
few things are genuinely different and are easy to get wrong:

* **Unit orders** are `unit_order` structs now, not parallel arrays, and the
  per-purpose orders (build city, disband, trade route) collapsed into
  `ORDER_PERFORM_ACTION` plus an action id from `enum gen_action`.
* **Explore and auto-worker** are server-side agents
  (`PACKET_UNIT_SERVER_SIDE_AGENT_SET`); the server refuses
  `ACTIVITY_EXPLORE` sent as an activity.
* **`enum unit_activity` was renumbered**, and there is no compatibility
  shim -- stale values silently order the wrong thing.
* **The default topology is iso-hex**, where NE and SW are not moves at all.
  A path containing one gets the whole orders packet rejected, so
  `fcmap.Topology.valid_directions()` filters them out.
* **A player's `ai` flag** moved into the `flags` bitvector, and ruleset
  build requirements moved from single tech ids to requirement vectors.

## Options

    ./fcgame.py host --help

    --mode MODE       interactive (default) | auto
    --turns-dir DIR   where interactive mode writes turns
                      (default: <savedir>/turns)
    --save-each-turn  save every turn, so the game survives a reboot
    --ai N            computer players besides you and Claude (default 3)
    --skill LEVEL     novice | easy | normal | hard | cheating | experimental
    --ruleset NAME    default: classic
    --map-size N      map size in thousands of tiles
    --seed N          fix the map and game seed for a repeatable game
    --nation NAME     nation for Claude to play (default: Roman)
    --port N          default 5556
    --timeout SECS    server turn timer; 0 (default) means untimed
                      -- leave it at 0, interactive turns take as long as
                      they take

    ./fcgame.py status [--turn N]     read the latest observation

## Fair play

Claude uses only what a human client can:

* it reads the same packet stream your client gets, so unexplored terrain and
  foreign units outside its vision are simply absent from its state;
* `auto worker` and `explore` are delegated to the server, exactly as the
  buttons in the GTK client do;
* the observation is built only from `GameState`, which is assembled from
  the packet stream -- there is nowhere for it to learn anything the server
  did not send;
* no server-console commands and no savegame inspection are used during play.

The server console is used only before the game starts, for setup, and is
what starts the game once you click Ready.

## The map

The part of the observation that decides how well the seat plays. It is
rendered in **map coordinates**, the ones the movement directions operate
on, so north is up and east is right -- tile *indices* are laid out
differently on isometric maps and would not render sensibly. Two characters
per tile: a lowercase terrain letter, then a marker.

```
                30        35        40
     31 ? ? ? ? ? r*f~f*o o o ? ? ? ?
     32 ? ? ? ? ? r r r o o o s*h m r*
     33 ? ? ? ? ? ? p f~f*e e p~rUh h
     35 ? ? ? ? ? ? ? ? f h*r r*r e fC
```

`C` our city, `X` foreign city, `U` our unit, `E` foreign unit, `*`
resource, `~` river. `?` is unexplored and blank is off the map. Markers are
uppercase and terrain letters are lowercase, so the two halves of a cell can
never be confused; the terrain letters themselves are assigned from whatever
ruleset is loaded and spelled out in the legend. Each city also gets a close
-up view of its own surroundings.

## Agent strategy (auto mode)

`fcbot/agent.py` holds a `Strategy` object -- city target, research plan,
build priorities, science rate. It is meant to be adjusted between turns,
which is where higher-level direction gets applied on top of the routine
micro-management the agent handles on its own.

## Vendored files and licensing

`fcbot/protocol/spec/packets.def` is copied verbatim from the Freeciv 3.2.5
sources (tag `R3_2_5`, `common/networking/packets.def`). Freeciv is licensed
GPL-2.0-or-later, so this repository should carry a GPL-2.0-or-later licence
too. The rest of the code was written against the Freeciv sources as
reference.
