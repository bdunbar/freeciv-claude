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

import json
import os
import time

from . import observe
from .orders import apply_orders

POLL_SECONDS = 2.0


class InteractiveAgent(object):
    def __init__(self, client, turns_dir, log=None, on_turn=None,
                 poll_seconds=POLL_SECONDS):
        self.client = client
        self.game = client.game
        self.dir = turns_dir
        self.log = log or (lambda *a: None)
        #: called with (turn, obs_path) once an observation is waiting
        self.on_turn = on_turn
        self.poll_seconds = poll_seconds
        self.last_message_mark = 0
        os.makedirs(self.dir, exist_ok=True)

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

    def play_turn(self, deadline=None):
        turn = self.game.turn
        path, _obs = self.write_observation(turn)
        self.log("turn %d: waiting for orders -- observation in %s" %
                 (turn, path))
        if self.on_turn:
            self.on_turn(turn, path)

        orders = self.wait_for_orders(turn, deadline)
        if orders is None:
            self.log("turn %d: no orders; ending the phase unchanged" % turn)
            return []

        results = apply_orders(self.client, orders)
        for line in results:
            self.log("  %s" % line)
        _write_json(self.path(turn, "result"), {
            "turn": turn,
            "note": ("'sent' means the packet went out; the server's verdict "
                     "is in the next turn's observation."),
            "orders": orders,
            "results": results})
        self.client.pump(0.5)
        return results


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
    '{"research_goal": "Currency"}, '
    '{"rates": {"tax": 30, "luxury": 0, "science": 70}}, '
    '{"government": "Monarchy"}, '
    '{"chat": "Nice city."}, '
    '{"diplomacy": "offer", "with": "Pakal", "clauses": ["ceasefire"]}, '
    '{"diplomacy": "accept", "with": "Pakal"}. '
    "A tile is either an index or an [x, y] map coordinate. Orders are "
    "applied in order and each one's outcome is reported back."
    " " + HOW_DIPLOMACY_WORKS
)


def _write_json(path, payload):
    # Write-then-rename, so a reader never sees a half-written file.
    tmp = path + ".partial"
    with open(tmp, "w") as fp:
        json.dump(payload, fp, indent=2, sort_keys=False)
        fp.write("\n")
    os.replace(tmp, path)
