# Next session

## Done (2026-09-05): interactive mode

Brian wanted **full turn-by-turn play**: the model decides every move, at
whatever pace that takes. That is built and working.

* `fcbot/observe.py` -- the whole situation as a dict, ids resolved to
  names, plus an ASCII map in map coordinates and a close-up per city.
* `fcbot/orders.py` -- a JSON order list resolved to real actions. A bad
  order is reported and skipped; the rest still run.
* `fcbot/interactive.py` -- writes `<turns>/0042.obs.json` (and a readable
  `.txt` twin), blocks until `0042.orders.json` appears, applies it, writes
  `0042.result.json`, ends the phase.
* `fcgame.py --mode interactive` (now the default) and `./fcgame.py status`.
* Auto mode logs in as `fcbot`, interactive as `claude`, so the wire name
  always says who is actually deciding.

## Still worth doing

* **Resuming mid-game is untested.** `--save-each-turn` writes a save every
  turn, but reconnecting to a game already under way may land the client as
  an observer rather than back in its seat. Worth checking before relying on
  it.
* The observation has no *history*: each turn is a fresh snapshot, so
  noticing "that stack has been getting closer for three turns" is left to
  whoever reads it. A short per-turn diff might be worth adding.
* Auto mode still never wages war and has no diplomacy.
* `fcgame.py` prints `research: goal -> goal set to X` -- the word "goal"
  twice. Cosmetic, in `agent.py` `manage_research` plus its caller.

## What exists and works

The whole client seat is built and verified against freeciv-server 3.2.5
(ported from 2.6.6 after the Pop!_OS 24.04 upgrade; see README):

* `fcbot/protocol/` -- freeciv 3.2 wire protocol from `packets.def`
  (framing, zlib chunks, delta compression, capability negotiation).
  All 203 packets parse and round-trip; 3600+ packets of live 3.2.5
  traffic decoded clean.
* `fcbot/state.py` -- fog-limited game state from the packet stream.
* `fcbot/client.py` -- the action set: `goto`, `do_activity`, `build_city`,
  `change_production`, `buy_production`, `set_research`, `set_research_goal`,
  `set_rates`, `change_government`, `auto_settler`, `chat`, `end_phase`.
* `fcbot/fcmap.py` / `fcbot/fcpath.py` -- topology (iso coords!) and
  pathfinding over known tiles.
* `fcbot/server.py` -- launches and drives freeciv-server.
* `fcbot/observe.py`, `fcbot/orders.py`, `fcbot/interactive.py` -- the
  model's seat (above).
* `tests/` -- 39 tests, all passing.

## Where interactive mode lives

The design that used to be sketched here is implemented; read the code and
the README section "Who is playing" instead. The shape of it:

* observation -- `fcbot/observe.py`, `observation()` and `to_text()`
* orders -- `fcbot/orders.py`, `apply_orders()`
* the loop and the file protocol -- `fcbot/interactive.py`
* tests for all three -- `tests/test_interactive.py`

The one design point worth restating, because it is easy to undo by
accident: the map is rendered in **map coordinates**, not native ones.
Native coordinates are what tile indices are laid out in; on an isometric
map -- which 3.2 defaults to -- they do not correspond to what the movement
directions do, so a render in native coordinates looks plausible and is
wrong.

## Environment notes

* The server and the human's client both come from the Flathub package
  `org.freeciv.gtk322` (3.2.5). `fcbot/server.py` runs the bundled
  `freeciv-server` via `flatpak run --command=freeciv-server`, passing
  `--filesystem=<savedir>` because the sandbox otherwise only maps
  `~/.freeciv`.
* Nothing depends on having the freeciv sources locally: `packets.def` is
  vendored at `fcbot/protocol/spec/packets.def`. To get the 3.2.5 sources
  back for reference:
  `curl -L https://github.com/freeciv/freeciv/archive/refs/tags/R3_2_5.tar.gz | tar xz`
* Careful with `pkill -f` in this repo: patterns like `freeciv-server -p 55`
  match the invoking shell's own command line and kill it. Use
  `pkill -x freeciv-server`.
