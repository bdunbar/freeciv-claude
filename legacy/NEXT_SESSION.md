# Where we left off

## Status

The savegame-driven harness is **built and verified** — see `README.md` for the
architecture, the encoding reference, and the bugs found along the way.

Working and tested: multi-turn move queues, founding cities, production changes,
tax/science rates, research goals, BFS pathfinding over known terrain, a
fog-respecting board view with known resources, and a headless server driver
that steps one turn at a time.

## The change of direction (this is the important part)

The plan was originally "Claude plays solo." Brian's actual goal is a
**head-to-head duel: he picks a civ, I pick a civ, we compete.**

Nothing built so far is wasted — the harness is exactly what my side needs. What
is missing is the mechanism for two human-controlled civs in one game.

### Design sketch for the duel (not yet built)

Brian plays his civ in the real `freeciv-gtk2` client; I play mine by writing
orders into the savegame between turns. The client can open our saves directly:

    freeciv-gtk2 --file <save>

Proposed per-turn loop, with the client's own spawned server as the single
source of truth for advancing turns (avoids double-executing my orders):

1. I write my civ's orders into the current save.
2. Brian opens that save in gtk2, plays his turn normally, hits End Turn.
3. He saves the resulting game and tells me the path.
4. I read it, write my next orders, repeat.

Fog integrity holds naturally: he sees only his nation in the client, and I read
only my own player section.

### The blocker I had just resolved

`turnblock` (server/settings.c:2608) defaults to ON, which means "the game turn
is not advanced until all players have finished their turn, **including
disconnected players**." With two non-AI civs and only one of them connected,
the turn would never end.

So a duel game must be created with **`turnblock` disabled**, which persists in
the savegame's `[settings]` section and so is inherited by the client's spawned
server. This was verified as the correct setting but **not yet tested end to
end.**

## Next steps

1. Add a `newduel` command: N human-controlled civs, `turnblock` disabled,
   `timeout 0`, both players taken off AI control, optional AI rivals.
2. Test headlessly that a game with two non-AI civs advances turns.
3. Test the client relay: does gtk2 preserve the other player's queued orders
   across a load/save cycle? (Expected yes — orders live in the savegame — but
   unverified.)
4. Decide the format with Brian: pure 1v1, or 1v1 plus AI rivals; map size;
   ruleset.

## Open questions for Brian

- Pure duel, or duel plus a couple of AI civs to complicate things?
- Same continent (early contact, sharp game) or separate landmasses (builder
  game, later contact)?
- Should I play blind — no peeking at his player section — on the honour system,
  or should we add a hard split so my tooling literally cannot read it?

## The abandoned solo game

`games/ourgame/` holds the started-but-unplayed solo game (Khmer, turn 1, two
Settlers, no capital founded). Kept only as a working fixture for the harness;
the duel will start fresh.

    python3 play.py --work games/ourgame board
