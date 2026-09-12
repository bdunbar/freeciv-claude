# Playing the seat

Things learned by playing, not by reading the rules. Every entry here cost a
turn or several to find out. Read this before a game; add to it during one.

The test for an entry: *would I get this wrong again next time?* If the
answer is no, it does not belong here. If the answer is yes, the better fix
is usually to put it in the observation where it cannot be missed -- and
then record here that it is there.

## Rules that reverse a decision

* **The government tile penalty.** Under Despotism (classic ruleset), a tile
  yielding more than 2 of any output loses one of it. So an ocean Fish at
  3/0/2 is really 2/0/2 -- *worse* than a Whales at 2/1/2, which keeps its
  shield. This reverses the ranking, and "best food tile" advice is wrong
  until you leave Despotism. Now reported as `meta.tile_output_penalty` and
  applied to every tile figure the observation prints.
* **Settlers cost 2 population.** A city cannot finish one until it is size
  3, however many shields it banks -- and shields above the cost are simply
  wasted while it waits. Reported as `population_cost` plus a city warning.
* **Peace is reachable directly from war**; a ceasefire is only reachable
  *from* war. So if an AI offers a ceasefire and you would rather have
  peace, ask for peace -- you do not have to take the ceasefire first.

## Research

* **A goal does not redirect research already under way.** The server picks
  something the moment the game starts -- Masonry, in one game -- and
  `research_goal` only takes effect once that finishes. Set the *current*
  tech too, on turn 1, or several turns go into a dead end. Check the first
  observation: if `researching` is not on the path to `goal`, change it
  while only a few bulbs are sunk.
* Monarchy in the classic ruleset wants Ceremonial Burial and Code of Laws,
  and Code of Laws wants Alphabet. Masonry is not on that path.

## Diplomacy

* **Contact lapses.** Meeting someone gives ~20 turns of contact, and when
  it runs out you cannot talk to them at all until you meet again. An
  embassy makes the channel permanent.
* **One embassy in either direction is enough** to keep talking. Hard AIs
  will not *give* an embassy -- they refused, and 20 gold did not move them
  -- but they will happily *accept* one. Giving yours away costs some
  intelligence and buys a permanent channel, which is the better trade.
* **AIs open with threats and sign anyway.** "Make it worth my letting you
  live, or be crushed" was followed by a ceasefire the same turn. Ask.
* **Adding a clause clears both acceptances.** Offer first, accept last --
  which is what the `offer` verb does for you.
* **`withdraw` then `accept` in the same turn does not work.** The accept
  reads local state that is still stale until the next pump, says "already
  accepted", and sends nothing. Split them across two turns.

## Openings

* **Found on turn 1 if the tile is decent.** Grassland or plains, coastal if
  possible. Moving one tile to a better centre is worth a turn; moving two
  is usually not. Forest is a poor centre: 1 food.
* **Growth before shields while blocked on population.** The city will
  auto-pick a balanced set of tiles; if the constraint is size, take the
  food tiles by hand.

## The map

* **It wraps east-west.** An explorer walking west off x=2 reappears at
  x=222. There is no western edge to be backed against.
* **`--map-size` is in thousands of tiles** and only works because we force
  `mapsize FULLSIZE`; the server's own default sizes the map from
  `tilesperplayer` and ignores `size` entirely.

## What the host will not pick up mid-game

`observe.py` and `orders.py` are re-imported every turn, so they can be
fixed while a game runs. **`state.py` is not** -- and neither is anything
that depends on packets that streamed past at login. The government tile
penalty needed `PACKET_RULESET_EFFECT`, which arrives once at login, so
teaching the seat about it mid-game was impossible: that fix only takes
effect in the next game.
