# freeciv-agent

Play a game of Freeciv against Claude. You use your normal Freeciv client;
Claude joins the same server as another player, through the same network
protocol, under the same rules and the same fog of war.

Verified against stock Debian/Ubuntu `freeciv-server` 2.6.6, ruleset `classic`.

## Quick start

    ./fcgame.py host --ai 3 --skill hard

That starts a server, connects Claude to it, and prints a join command.
In another terminal:

    freeciv-gtk2 -a -p 5556 -s localhost

Pick a nation, click **Ready**, and the game begins. Claude plays its own
turns and prints what it is doing.

## How it works

Claude is a real client, not a puppet. `fcbot/protocol/` implements the
freeciv 2.6 network protocol, generated from freeciv's own machine-readable
spec (`common/packets.def`, vendored at `fcbot/protocol/spec/packets.def`)
rather than reverse-engineered:

* **framing** — `uint16 length | uint8 type | body`, with zlib-compressed
  multi-packet chunks (`length >= 16385`) unpacked transparently.
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
| `fcgame.py` | CLI: host a game and play against Claude |
| `fcbot/protocol/pdef.py` | parser for freeciv's `packets.def` |
| `fcbot/protocol/dataio.py` | wire encodings (mirrors `common/dataio.c`) |
| `fcbot/protocol/codec.py` | packet encode/decode incl. delta compression |
| `fcbot/protocol/connection.py` | socket, framing, compression, login |
| `fcbot/state.py` | game state assembled from the packet stream |
| `fcbot/fcmap.py` | topology: tile indices, native/map coords, directions |
| `fcbot/fcpath.py` | pathfinding over tiles we actually know |
| `fcbot/client.py` | player actions: orders, production, research, rates |
| `fcbot/agent.py` | the baseline playing agent |
| `fcbot/server.py` | launches and drives `freeciv-server` |
| `legacy/` | an earlier savegame-editing approach, superseded |

## Options

    ./fcgame.py host --help

    --ai N            computer players besides you and Claude (default 3)
    --skill LEVEL     novice | easy | normal | hard | cheating | experimental
    --ruleset NAME    default: classic
    --map-size N      map size in thousands of tiles
    --seed N          fix the map and game seed for a repeatable game
    --nation NAME     nation for Claude to play (default: Roman)
    --port N          default 5556
    --timeout SECS    server turn timer; 0 (default) means untimed

## Fair play

Claude uses only what a human client can:

* it reads the same packet stream your client gets, so unexplored terrain and
  foreign units outside its vision are simply absent from its state;
* `auto worker` and `explore` are delegated to the server, exactly as the
  buttons in the GTK client do;
* no server-console commands and no savegame inspection are used during play.

The server console is used only before the game starts, for setup, and is
what starts the game once you click Ready.

## Agent strategy

`fcbot/agent.py` holds a `Strategy` object -- city target, research plan,
build priorities, science rate. It is meant to be adjusted between turns,
which is where higher-level direction gets applied on top of the routine
micro-management the agent handles on its own.

## Vendored files and licensing

`fcbot/protocol/spec/packets.def` is copied verbatim from the Freeciv 2.6.6
sources. Freeciv is licensed GPL-2.0-or-later, so this repository should carry
a GPL-2.0-or-later licence too. The rest of the code was written against the
Freeciv sources as reference.
