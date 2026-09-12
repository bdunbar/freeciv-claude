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

    ./fcgame.py host --ai 3 --skill hard

That starts a server, connects a second client to it, and prints a join
command. In another terminal:

    flatpak run org.freeciv.gtk322 -a -p 5556 -s localhost

Pick a nation, click **Ready**, and the game begins.

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
| `fcgame.py` | CLI: `host` a game, `play` a turn, `status` to look |
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
| `fcbot/interactive.py` | the turn loop that waits for the model |
| `fcbot/agent.py` | the scripted fallback agent (`--mode auto`) |
| `fcbot/server.py` | launches and drives `freeciv-server` |
| `PLAYBOOK.md` | what playing the game has taught, kept between sessions |
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
