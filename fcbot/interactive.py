"""Interactive mode: the model plays the turns, through two files.

When our phase starts we write everything we can see to

    <dir>/0042.obs.json

and then wait -- indefinitely, since the server runs untimed -- for

    <dir>/0042.orders.json

to appear. The orders are applied, the results are written back to
0042.result.json so a mistake is visible next turn, and the phase ends.

Waiting is the point: a turn can take as long as it takes, and the human's
client just shows us as still thinking. Run the host under tmux or nohup so
the game outlives the session that started it.
"""

import importlib
import json
import os
import shutil
import time

from . import observe, orders

POLL_SECONDS = 2.0


class InteractiveAgent(object):
    def __init__(self, client, turns_dir, log=None, on_turn=None,
                 poll_seconds=POLL_SECONDS, reload_each_turn=True):
        self.client = client
        self.game = client.game
        self.dir = turns_dir
        self.log = log or (lambda *a: None)
        #: called with (turn, obs_path) once an observation is waiting
        self.on_turn = on_turn
        self.poll_seconds = poll_seconds
        #: re-import observe.py and orders.py each turn, so the seat can be
        #: tuned while the game is running
        self.reload_each_turn = reload_each_turn
        self.last_message_mark = 0
        os.makedirs(self.dir, exist_ok=True)
        self.archive_previous_game()

    def archive_previous_game(self):
        """Move an earlier game's files out of the way before playing.

        Turn numbers restart with every game, so a turns directory left over
        from a previous one already holds an orders file for every turn we
        are about to ask about -- and `wait_for_orders` would take each of
        them the instant it wrote the observation. That is not theoretical:
        one game replayed two-day-old orders, chat included, against units
        that no longer existed, at about a turn a second, and never founded
        a city. The old files are kept, not deleted; they are a record of a
        game that was played.
        """
        leftovers = [n for n in os.listdir(self.dir)
                     if n.endswith((".obs.json", ".obs.txt", ".orders.json",
                                    ".result.json"))]
        if not leftovers:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archive = os.path.join(self.dir, "previous-%s" % stamp)
        os.makedirs(archive, exist_ok=True)
        for name in leftovers:
            shutil.move(os.path.join(self.dir, name),
                        os.path.join(archive, name))
        self.log("moved %d file(s) from an earlier game into %s"
                 % (len(leftovers), archive))
        return archive

    # -- paths ---------------------------------------------------------
    def path(self, turn, suffix):
        return os.path.join(self.dir, "%04d.%s.json" % (turn, suffix))

    # -- the turn ------------------------------------------------------
    def write_observation(self, turn):
        obs = observe.observation(self.game, self.last_message_mark)
        obs["orders_go_in"] = self.path(turn, "orders")
        obs["how_to_answer"] = HOW_TO_ANSWER
        path = self.path(turn, "obs")
        _write_json(path, obs)
        # A plain-text twin, because that is what is actually readable.
        with open(self.path(turn, "obs").replace(".json", ".txt"), "w") as fp:
            fp.write(observe.to_text(obs) + "\n")
        self.last_message_mark = obs["message_mark"]
        return path, obs

    def wait_for_orders(self, turn, deadline=None):
        """Block until the orders file appears. Returns the parsed orders,
        or None if we gave up or the game moved on without us."""
        path = self.path(turn, "orders")
        while True:
            if os.path.exists(path):
                try:
                    with open(path) as fp:
                        return json.load(fp)
                except ValueError as exc:
                    # Half-written file: say so and keep waiting rather than
                    # throwing away the turn.
                    self.log("orders file is not valid JSON yet (%s)" % exc)
            if deadline is not None and time.time() > deadline:
                return None
            # Keep the connection alive and the state current while we wait;
            # this is also what answers the server's pings.
            self.client.pump(self.poll_seconds)
            if self.game.turn != turn:
                self.log("turn moved on to %d while waiting" % self.game.turn)
                return None

    def reload_modules(self):
        """Pick up edits to observe.py and orders.py without restarting.

        Restarting the host drops the seat out of the game, so without this
        the two files you most want to fix are the two you cannot touch --
        and mid-game is exactly when you find out what is wrong with them.
        A file that will not import is reported and the old one kept.
        """
        for module in (observe, orders):
            try:
                importlib.reload(module)
            except Exception as exc:
                self.log("could not reload %s, keeping the loaded one: %s"
                         % (module.__name__, exc))

    def play_turn(self, deadline=None):
        if self.reload_each_turn:
            self.reload_modules()
        turn = self.game.turn
        path, _obs = self.write_observation(turn)
        self.log("turn %d: waiting for orders -- observation in %s" %
                 (turn, path))
        if self.on_turn:
            self.on_turn(turn, path)

        order_list = self.wait_for_orders(turn, deadline)
        if order_list is None:
            self.log("turn %d: no orders; ending the phase unchanged" % turn)
            return []

        results = orders.apply_orders(self.client, order_list)
        for line in results:
            self.log("  %s" % line)
        _write_json(self.path(turn, "result"), {
            "turn": turn,
            "note": ("'sent' means the packet went out; the server's verdict "
                     "is in the next turn's observation."),
            "orders": order_list,
            "results": results})
        self.client.pump(0.5)
        return results


HOW_CITIES_WORK = (
    "Cities: 'food.box' is what growth costs and 'food.turns_to_grow' when "
    "it arrives; 'worked_tiles' and 'free_tiles' are the levers when food or "
    "shields are the binding constraint -- free a tile with 'stop_working' "
    "and claim one with 'work_tile', both by [x, y]. 'population_cost' on a "
    "unit is citizens it consumes, so a size-2 city can never finish "
    "Settlers. Read 'warnings' on each city first: they are the things that "
    "quietly cost turns. Tile output is the ruleset's flat terrain figure -- "
    "the right ordering, not the exact number."
)

HOW_DIPLOMACY_WORKS = (
    "Diplomacy: talk to a player only while 'can_negotiate_now' is true in "
    "the diplomacy section -- an embassy makes that permanent, plain contact "
    "lapses after a few turns. 'offer' opens a meeting if there is none, "
    "puts the clauses on the table and accepts our side; the deal happens "
    "when both sides have accepted, and any new clause clears both "
    "acceptances. Open meetings, and who is waiting on whom, are in the "
    "'meetings' section. Clauses: \"ceasefire\" (only from war), "
    "\"peace\" (from war or ceasefire), \"alliance\", \"embassy\", "
    "\"vision\", \"shared_tiles\", \"map\", \"seamap\", and with a "
    "value {\"type\": \"gold\", \"value\": 50}, "
    "{\"type\": \"advance\", \"value\": \"Alphabet\"}, "
    "{\"type\": \"city\", \"value\": \"Roma\"}. Add "
    "\"from\": \"them\" to ask for a thing instead of giving it (the "
    "default is \"me\"). Other actions: \"meet\", \"withdraw\" (take "
    "clauses back off the table), \"cancel_meeting\", \"break\" (drop a "
    "step: alliance -> peace -> war), \"stop_vision\"."
)

HOW_TO_ANSWER = (
    "Write a JSON list of orders to the path in 'orders_go_in'. Examples: "
    '{"unit": 112, "goto": [14, 22], "then": "found_city"}, '
    '{"unit": 115, "activity": "fortify"}, '
    '{"unit": 118, "activity": "explore"}, '
    '{"city": 131, "build": ["unit", "Phalanx"]}, '
    '{"city": 131, "worklist": [["improvement", "Temple"]]}, '
    '{"city": 131, "buy": true}, '
    '{"city": 131, "work_tile": [15, 21]}, '
    '{"city": 131, "stop_working": [16, 21]}, '
    '{"city": 131, "specialist": {"from": "elvis", "to": "scientist"}}, '
    '{"research_goal": "Currency"}, '
    '{"rates": {"tax": 30, "luxury": 0, "science": 70}}, '
    '{"government": "Monarchy"}, '
    '{"chat": "Nice city."}, '
    '{"diplomacy": "offer", "with": "Pakal", "clauses": ["ceasefire"]}, '
    '{"diplomacy": "accept", "with": "Pakal"}. '
    "A tile is either an index or an [x, y] map coordinate. Orders are "
    "applied in order and each one's outcome is reported back."
    " " + HOW_CITIES_WORK + " " + HOW_DIPLOMACY_WORKS
)


def _write_json(path, payload):
    # Write-then-rename, so a reader never sees a half-written file.
    tmp = path + ".partial"
    with open(tmp, "w") as fp:
        json.dump(payload, fp, indent=2, sort_keys=False)
        fp.write("\n")
    os.replace(tmp, path)
