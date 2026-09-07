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

## From the first real game (2026-09-05, turns 1-15)

Played a live game as Claudius of the Romans against Brian's Celts and three
hard AIs, driven entirely through the observation/orders files. It works --
the seat founded a city, expanded, fought off nothing, and held a
conversation. What it turned up, roughly in order of how much it cost me:

### 1. There is no diplomacy at all  -- DONE (2026-09-07)

Built: `client.py` speaks all five meeting packets plus `CANCEL_PACT`,
`state.py` mirrors the treaty on the table (including the rule that any new
clause clears both acceptances), `observe.py` reports open meetings, whether
we can talk to a player at all, and the other side's answers, and `orders.py`
has a `diplomacy` verb -- `meet`, `offer`, `accept`, `withdraw`,
`cancel_meeting`, `break`, `stop_vision`. Verified live against a 3.2.5
server: war -> ceasefire with a hard AI in one order, and a peace-plus-
embassies offer left pending with the AI's refusal ("I wish to see you keep
the current ceasefire for a bit longer first") coming back as diplomatic
news. Also fixed here: `chat_since()` no longer echoes our own lines.

What the note said at the time:

> The biggest gap. Both AIs I met declared war on sight and then repeatedly
> offered a ceasefire, and there is no way to accept. `orders.py` has no
> diplomacy verb because `client.py` implements no treaty packets:
> `PACKET_DIPLOMACY_INIT_MEETING_REQ`, `_CREATE_CLAUSE_REQ`,
> `_ACCEPT_TREATY_REQ`, `_CANCEL_MEETING_REQ`, plus handling the incoming
> `PACKET_DIPLOMACY_*` so a pending meeting shows up in the observation.
>
> Without it the seat cannot make peace, trade techs, form an alliance, or
> respond to Brian as anything but a chat partner -- in a five-player game
> that is most of the strategy space missing. Do this one first.

### 2. The map overview blows up as the explorer wanders

`observe._overview_radius()` takes the distance to the furthest *known*
tile, so one explorer 20 tiles away turned the render into a 43x43 grid that
was ~90% `?`, with my actual territory in one corner. It should key off
where my cities and units are, not the frontier -- or render the bounding
box of known tiles instead of a square around one centre.

### 3. Orders are not checked against the observation

I ordered research on Alphabet when the observation already listed it under
`known`. The server dropped it silently (`handle_player_research` requires
`TECH_PREREQS_KNOWN`), the result file said "sent", and a turn of research
was lost. `orders.py` can catch this class of thing cheaply: reject a tech
in `known`, a unit or city id that is not ours, a rate split that a
government forbids.

### 4. The observation cannot see two things it needs

* **Unit population cost.** Settlers cost 2 pop here, so a city needs to be
  size 3. I learned that from an `E_CITY_CANTBUILD` event after buying a
  settler the city could not build. `pop_cost` is in `PACKET_RULESET_UNIT`
  and should be on each city's build options.
* **Turns to grow.** Cities report `turns_to_completion` for shields but
  nothing for food, so I twice mis-estimated how long expansion was blocked
  (once as seven turns when it was one). Add food box size and turns to
  grow.

### 5. No city tile management

For ten turns straight the binding constraint was food, and there was no
lever: no way to rearrange worked tiles or ask for a food-focused
arrangement. `PACKET_CITY_MAKE_WORKER` and the CM parameter packets exist;
the orders schema exposes neither.

### 6. Smaller things

* ~~`GameState.chat_since()` returns our own messages too~~ -- fixed
  2026-09-07 alongside diplomacy.
* Editing `observe.py` or `orders.py` mid-game does nothing -- the host
  imported them at startup, and restarting it drops the client out of the
  game. Re-importing them per turn would make the seat tunable while
  playing, which is exactly when you notice what is wrong with it.
* Shield overflow: while a build is blocked on population, shields pile up
  past the cost and are wasted. I handled it by hand twice (slotting in a
  Warriors, then a Workers). Worth surfacing in the observation as a
  warning rather than leaving it to be noticed.

## Still worth doing (from building it, before the game)

* **Resuming mid-game is untested.** `--save-each-turn` writes a save every
  turn, but reconnecting to a game already under way may land the client as
  an observer rather than back in its seat. Worth checking before relying on
  it.
* The observation has no *history*: each turn is a fresh snapshot, so
  noticing "that stack has been getting closer for three turns" is left to
  whoever reads it. A short per-turn diff might be worth adding.
* Auto mode still never wages war and has no diplomacy. The packets and
  the `Client` methods exist now, so an auto-mode policy is the only
  missing piece.
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
  `set_rates`, `change_government`, `auto_settler`, `chat`, `end_phase`,
  and the treaty set: `init_meeting`, `create_clause`, `remove_clause`,
  `accept_treaty`, `cancel_meeting`, `cancel_pact`.
* `fcbot/fcmap.py` / `fcbot/fcpath.py` -- topology (iso coords!) and
  pathfinding over known tiles.
* `fcbot/server.py` -- launches and drives freeciv-server.
* `fcbot/observe.py`, `fcbot/orders.py`, `fcbot/interactive.py` -- the
  model's seat (above).
* `tests/` -- 59 tests, all passing.

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
