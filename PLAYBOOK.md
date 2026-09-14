# Playing the seat

Things learned by playing, not by reading the rules. Every entry here cost a
turn or several to find out. Read this before a game; add to it during one.

The test for an entry: *would I get this wrong again next time?* If the
answer is no, it does not belong here. If the answer is yes, the better fix
is usually to put it in the observation where it cannot be missed -- and
then record here that it is there.

## Standing orders

Set a policy on turn 1 rather than playing turn-by-turn. Cover every unit
type you own or autoplay stops on it -- that is deliberate, but it means a
policy missing `Workers` will halt the moment one is built. Re-issue the
policy whenever the shape of the game changes: a new unit type, a new city
that wants a different build rule, a war.

* **A unit built this turn arrives idle, whatever the policy says.** The
  policy runs before the city finishes building, so the new unit appears
  after it and nothing has told it what to do. The second Warriors came out
  unfortified and had to be fixed by hand. Expect one idle unit on the turn
  after any build completes.
* **`enemy_within` counts anyone you have no contact with as hostile**, and
  that includes wandering animals. With `enemy_within: 3` a bear walking
  past the capital wakes a whole invocation. On a crowded map 3 is too
  twitchy; pick a radius you would actually change a plan over.

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
* **An AI may clear your offer off the table and counter with its own.**
  Ours was embassy-plus-peace; overnight the meeting held one clause, their
  ceasefire, accepted on their side and waiting on us. The choice then is
  not peace versus ceasefire, it is ceasefire versus nothing. Take it:
  peace is reachable *from* ceasefire, so it costs no future, and adding a
  peace clause instead would clear their acceptance and re-open a
  negotiation they have already walked out of once.
* **An offer that is waiting on them is finished work -- leave it alone.**
  Any new clause clears both acceptances, so "improving" a pending offer
  withdraws it. Say more in chat if you must; do not touch the table.
* **Offer the whole package in one meeting**, not embassy this turn and
  peace the next. Contact is the scarce resource, not clauses.

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
* **`startpos ALL` in the server log means one continent for everybody.**
  It is in the settings the host prints at startup, and it is worth reading
  before turn 1: it decides whether the early game is a land grab against
  close neighbours or a quiet expansion. Assume close neighbours when you
  see it.
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

## Playing a turn under the runner

`./fcgame.py run-agent` starts one invocation per turn, and it exits when
you do. Nothing of it survives except the files it wrote.

* **Write the orders file first, then the journal.** One invocation wrote a
  full journal entry describing the turn it was about to play, and very
  nearly exited without playing it. The orders file is the only thing the
  game reads; everything else is a note to a stranger. Commit the turn,
  then write about it.
* **If a journal entry exists for this turn but the observation is
  unchanged, nothing was sent.** Re-decide from the observation, not from
  the entry -- the entry describes an intention, and intentions do not
  reach the server. A journal that says "founded Roma" next to an
  observation showing 0 cities means the last invocation died before
  submitting.
* **The runner never plays for you.** If the orders file is missing or
  malformed the turn is left pending and the runner stops. Nothing is
  submitted on your behalf, so a turn you did not finish is a turn nobody
  played -- but also a turn nobody ruined.
