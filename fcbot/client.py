"""A real freeciv client: login, pregame setup, and issuing player actions.

This speaks the same protocol as freeciv-gtk2, so the server applies exactly
the same rules and fog of war to us as to any human player.
"""

import socket
import time

from . import fcpath, state
from .protocol.connection import Connection


class Client(object):
    def __init__(self, host="localhost", port=5556, username="claude",
                 packets_def=None, log=None):
        self.conn = Connection(host, port, packets_def)
        self.username = username
        self.game = state.GameState()
        self.log = log or (lambda *a: None)
        self._phase_started = False
        self._game_over = False

    # -- connection ----------------------------------------------------
    def login(self):
        reply = self.conn.login(self.username)
        self.game.conn_id = self.conn.conn_id
        self.log("logged in as %s (conn %s)" % (self.username, self.conn.conn_id))
        return reply

    def close(self):
        self.conn.close()

    # -- packet pump ---------------------------------------------------
    def pump(self, timeout=0.2):
        """Read and apply all currently available packets.

        Returns the list of (name, values) processed.
        """
        got = []
        deadline = time.time() + timeout
        while True:
            packet = self.conn._take_packet()
            if packet is None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    self.conn._fill(timeout=max(0.01, remaining))
                except socket.timeout:
                    break
                continue
            ptype, body = packet
            spec = self.conn.by_number.get(ptype)
            if spec is None:
                self.log("unknown packet type %d, skipped" % ptype)
                continue
            try:
                values = self.conn.codec.decode(ptype, body)
            except Exception as exc:
                self.log("decode failed for %s: %s" % (spec.name, exc))
                continue
            self._preprocess(spec.name, values)
            self.game.handle(spec.name, values)
            got.append((spec.name, values))
        return got

    def _preprocess(self, name, values):
        if name == "PACKET_CONN_PING":
            # The server drops clients that stop ponging.
            self.conn.send("PACKET_CONN_PONG")
        elif name == "PACKET_START_PHASE":
            self._phase_started = True
        elif name == "PACKET_ENDGAME_REPORT":
            self._game_over = True

    def wait_for(self, names, timeout=30.0):
        """Pump until one of `names` arrives; returns its values or None."""
        names = set(names if not isinstance(names, str) else [names])
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, values in self.pump(0.5):
                if name in names:
                    return values
        return None

    # -- pregame -------------------------------------------------------
    @property
    def player_no(self):
        return self.game.player_no

    def select_nation(self, nation_id, leader_name, is_male=True, style=0):
        self.conn.send("PACKET_NATION_SELECT_REQ",
                       player_no=self.player_no,
                       nation_no=nation_id,
                       is_male=is_male,
                       name=leader_name,
                       style=style)

    def set_ready(self, ready=True):
        self.conn.send("PACKET_PLAYER_READY",
                       player_no=self.player_no, is_ready=ready)

    def chat(self, message):
        self.conn.send("PACKET_CHAT_MSG_REQ", message=message)

    # -- in-game actions -----------------------------------------------
    def send_orders(self, unit_id, orders, dest_tile=None):
        """orders: list of dicts with keys order/dir/activity/target."""
        unit = self.game.units.get(unit_id)
        if unit is None:
            return False
        n = len(orders)
        if n == 0:
            return False
        pad = lambda key, default: [o.get(key, default) for o in orders]
        return self.conn.send(
            "PACKET_UNIT_ORDERS",
            unit_id=unit_id,
            src_tile=unit["tile"],
            length=n,
            repeat=False,
            vigilant=False,
            orders=pad("order", state.ORDER_MOVE),
            dir=pad("dir", 0),
            activity=pad("activity", state.ACTIVITY_IDLE),
            target=pad("target", 0),
            dest_tile=dest_tile if dest_tile is not None else unit["tile"])

    def goto(self, unit_id, dest_tile, then=None):
        """Path to dest_tile over known terrain; `then` appends a final order."""
        unit = self.game.units.get(unit_id)
        if unit is None:
            return False
        utype = self.game.ruleset.units.get(unit["type"])
        if utype is None:
            return False
        uclass = utype["unit_class_id"]
        path = fcpath.find_path(self.game, unit["tile"], dest_tile, uclass)
        if path is None:
            return False
        dirs = fcpath.path_directions(self.game.topo, unit["tile"], path)
        if dirs is None:
            return False
        orders = [{"order": state.ORDER_MOVE, "dir": d} for d in dirs]
        if then is not None:
            orders.append(then)
        if not orders:
            return False
        return self.send_orders(unit_id, orders, dest_tile=dest_tile)

    def do_activity(self, unit_id, activity, target=0):
        self.conn.send("PACKET_UNIT_CHANGE_ACTIVITY",
                       unit_id=unit_id, activity=activity, target=target)
        return True

    def build_city(self, unit_id, name):
        self.conn.send("PACKET_UNIT_BUILD_CITY", unit_id=unit_id, name=name)
        return True

    def change_production(self, city_id, kind, value):
        self.conn.send("PACKET_CITY_CHANGE", city_id=city_id,
                       production_kind=kind, production_value=value)
        return True

    def build_unit(self, city_id, unit_type_id):
        return self.change_production(city_id, state.VUT_UTYPE, unit_type_id)

    def build_improvement(self, city_id, improvement_id):
        return self.change_production(city_id, state.VUT_IMPROVEMENT,
                                      improvement_id)

    def buy_production(self, city_id):
        self.conn.send("PACKET_CITY_BUY", city_id=city_id)
        return True

    def set_research(self, tech_id):
        self.conn.send("PACKET_PLAYER_RESEARCH", tech=tech_id)
        return True

    def set_research_goal(self, tech_id):
        self.conn.send("PACKET_PLAYER_TECH_GOAL", tech=tech_id)
        return True

    def set_rates(self, tax, luxury, science):
        self.conn.send("PACKET_PLAYER_RATES",
                       tax=tax, luxury=luxury, science=science)
        return True

    def change_government(self, government_id):
        self.conn.send("PACKET_PLAYER_CHANGE_GOVERNMENT",
                       government=government_id)
        return True

    def end_phase(self):
        self.conn.send("PACKET_PLAYER_PHASE_DONE", turn=self.game.turn)
        self._phase_started = False
        return True

    def auto_settler(self, unit_id):
        """Hand a worker to the server's auto-worker logic."""
        self.conn.send("PACKET_UNIT_AUTOSETTLERS", unit_id=unit_id)
        return True
