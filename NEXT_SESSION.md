# Next session: interactive mode (Claude actually plays)

## Decision (2026-09-05)

Brian wants **full turn-by-turn play**: the model decides every move. He
accepted the slow, asynchronous pace explicitly -- play a turn, walk away,
come back later.

The scripted `fcbot/agent.py` is a scaffold, not the goal. It stays as a
fallback (`--mode auto`) but is not what we are building toward.

Also: rename the bot's visible identity from `claude` to `fcbot` in auto mode.
Labelling scripted rules "Claude" misled Brian once already.

## What exists and works

The whole client seat is built and verified against freeciv-server 2.6.6:

* `fcbot/protocol/` -- freeciv 2.6 wire protocol from `packets.def`
  (framing, zlib chunks, delta compression, capability negotiation).
  All 185 packets parse; 2371 packets of live traffic decoded clean.
* `fcbot/state.py` -- fog-limited game state from the packet stream.
* `fcbot/client.py` -- the action set: `goto`, `do_activity`, `build_city`,
  `change_production`, `buy_production`, `set_research`, `set_research_goal`,
  `set_rates`, `change_government`, `auto_settler`, `chat`, `end_phase`.
* `fcbot/fcmap.py` / `fcbot/fcpath.py` -- topology (iso coords!) and
  pathfinding over known tiles.
* `fcbot/server.py` -- launches and drives freeciv-server.
* `tests/test_protocol.py` -- 10 tests, all passing.

**Nothing new is needed in the protocol layer.** This is a UI problem now:
get the situation to the model, get decisions back.

## The design to build

Add `fcbot/interactive.py` and a `--mode interactive` flag to `fcgame.py host`.

Turn loop, when our phase starts:

1. Write `game/turns/0042.obs.json` -- everything we can see.
2. Block, polling for `game/turns/0042.orders.json`.
3. Apply the orders, `end_phase()`, repeat.

The server runs untimed (`--timeout 0`), so blocking indefinitely is safe and
the human's client just shows us as still thinking. Run the host process under
`tmux`/`nohup` so the game survives this session ending.

### Observation format

Must be fog-limited (it already is -- the server never tells us more) and rich
enough to decide well:

* **meta** -- turn, year, gold, tax/lux/sci, government, revolution status
* **research** -- current tech, bulbs/cost, goal, list of known techs, and what
  is researchable next
* **my units** -- id, type, tile index *and* map (x,y), moves left, hp,
  veteran, current activity, pending orders, home city
* **my cities** -- id, name, tile, size, food/shield/trade surplus and stock,
  what it is building and turns to completion, worklist, improvements built,
  units garrisoned, whether it is in disorder
* **map** -- this is the part that decides how well I play. A compact ASCII
  render of known terrain with units/cities marked, plus a per-city local view.
  Include resources, rivers and terrain letters. Do not just dump tile JSON.
* **foreign** -- units and cities seen, owner, distance from my nearest city
* **diplomacy** -- player list, embassies, war/peace/ceasefire state
* **events since last turn** -- chat and notify messages: attacks, cities lost
  or founded, techs learned, wonders built

### Orders format

A list of actions mirroring `client.py`, resolved by name not raw id where
possible:

```json
[
  {"unit": 112, "goto": 1482, "then": "found_city"},
  {"unit": 115, "activity": "fortify"},
  {"unit": 118, "activity": "explore"},
  {"city": 131, "build": ["unit", "Phalanx"]},
  {"city": 131, "worklist": [["improvement", "Temple"]]},
  {"research_goal": "Currency"},
  {"rates": {"tax": 30, "luxury": 0, "science": 70}},
  {"chat": "Nice city. It would be a shame if something happened to it."}
]
```

`chat` is worth having: it lets me talk to Brian in-game, which is half the
fun of a human opponent.

### Also worth building

* **Save/resume.** `srv.save(name)` each turn, so a game survives a reboot.
  Reconnecting mid-game needs checking -- a client that joins after the game
  has started may land as an observer rather than taking its player back.
  That path is untested.
* **A `status` command** that prints the current observation as text, so Brian
  can see what I am looking at.

## Known rough edges

* `fcgame.py` prints `research: goal -> goal set to X` -- the word "goal"
  appears twice. Cosmetic, in `agent.py` `manage_research` plus the caller.
* Auto mode logs in as `claude`; should be `fcbot`.
* The agent never wages war or does diplomacy. Irrelevant once interactive
  mode lands, but it is why auto mode is a weak opponent.

## Environment notes

* Freeciv sources were unpacked at `/tmp/freeciv-2.6.6` and will be gone after
  a reboot. Nothing depends on them: `packets.def` is vendored at
  `fcbot/protocol/spec/packets.def`. To get the sources back for reference:
  `apt-get source freeciv`.
* Careful with `pkill -f` in this repo: patterns like `freeciv-server -p 55`
  match the invoking shell's own command line and kill it. Use
  `pkill -x freeciv-server`.
