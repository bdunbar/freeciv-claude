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

### 2. The map overview blows up as the explorer wanders  -- DONE (2026-09-07)

The overview is now keyed on where we have *settled* (cities, or units
before the first city) plus a margin, capped at radius 16; every render
trims edge rows and columns that hold nothing but `?`; and units the
overview does not reach get their own small close-up under
`map.around_units_further_afield`. Checked live with three auto-explorers
scattered across a size-1 map: the empire view stayed 10x11 while each
wanderer got its own window.

The original note:

`observe._overview_radius()` takes the distance to the furthest *known*
tile, so one explorer 20 tiles away turned the render into a 43x43 grid that
was ~90% `?`, with my actual territory in one corner. It should key off
where my cities and units are, not the frontier -- or render the bounding
box of known tiles instead of a square around one centre.

### 3. Orders are not checked against the observation  -- DONE (2026-09-07)

`orders.py` now refuses a tech we already know or whose prerequisites are
not in hand (pointing at `research_goal` instead), a work_tile outside the
city radius, a specialist we do not have, and the diplomatic clauses the
server would drop. Production that the city is too small to finish is sent
with a NOTE rather than refused -- queueing it while the city grows is a
real thing to want. Rate splits are *not* checked: a government's maximum
rate is an effect and never reaches the client.

The original note:

I ordered research on Alphabet when the observation already listed it under
`known`. The server dropped it silently (`handle_player_research` requires
`TECH_PREREQS_KNOWN`), the result file said "sent", and a turn of research
was lost. `orders.py` can catch this class of thing cheaply: reject a tech
in `known`, a unit or city id that is not ours, a rate split that a
government forbids.

### 4. The observation cannot see two things it needs  -- DONE (2026-09-07)

Both added: `population_cost` on a city's unit production (with a warning
when the city is too small), and `food.box` / `food.turns_to_grow` from
`city_granary_size()`. Build costs now go through the game's `shieldbox`
rather than the raw ruleset number.

The original note:

* **Unit population cost.** Settlers cost 2 pop here, so a city needs to be
  size 3. I learned that from an `E_CITY_CANTBUILD` event after buying a
  settler the city could not build. `pop_cost` is in `PACKET_RULESET_UNIT`
  and should be on each city's build options.
* **Turns to grow.** Cities report `turns_to_completion` for shields but
  nothing for food, so I twice mis-estimated how long expansion was blocked
  (once as seven turns when it was one). Add food box size and turns to
  grow.

### 5. No city tile management  -- DONE (2026-09-07)

`work_tile`, `stop_working` and `specialist` on a city order, over
`PACKET_CITY_MAKE_WORKER` / `_MAKE_SPECIALIST` / `_CHANGE_SPECIALIST`. The
observation lists `worked_tiles` and `free_tiles` with each tile's terrain,
resource and base output so there is something to choose between. Verified
live: freeing one tile and claiming another moved the worked set.

The original note:

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
* ~~Shield overflow~~ -- fixed 2026-09-07: it is a city `warning` now,
  along with a blocked build, a negative food surplus and disorder.

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
* ~~`fcgame.py` prints `research: goal -> goal set to X`~~ -- fixed
  2026-09-07.

## Map shape (2026-09-07)

Brian prefers non-hex maps, so `fcgame.py host --topology` picks between
`square` (the new default), `iso`, `hex` and `iso-hex` (freeciv's own
default). Building it turned up a real bug: `Topology.is_isometric` is
`ISO | HEX`, because freeciv counts a hex map as isometric for *coordinate*
purposes — but which diagonal a hex map drops turns on the ISO flag alone.
Plain hex was therefore getting iso-hex's directions, which would have had
the server reject every orders packet aimed along them. `has_iso_flag` is
now separate from `is_isometric`, and single steps in every legal direction
were checked live on all three shapes.

## Driving the seat (2026-09-07)

Playing by hand cost three shell calls a turn -- poll for the observation,
write the orders, poll for the reply -- plus tracking the turn number.
`./fcgame.py play` does all of it in one call and prints the order results
and the next observation together. Run it in the background and its return
*is* the "your move" signal, which is the part that was actually missing:
nothing ever told the player their turn had come.

Not changed, deliberately: the two-file protocol itself. It is simple,
debuggable, survives a restart, and works. The gap was a client for it, not
a different design.

## Where things stand (2026-09-07)

Everything the first game turned up is fixed and, where it touches the wire,
checked against a live 3.2.5 server. What is left is the list above:
resuming mid-game, a per-turn diff, and an auto-mode diplomacy policy --
none of them things the first game actually tripped over. The next real
information probably comes from playing another game rather than from
building more.

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
* `tests/` -- 81 tests, all passing.

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
