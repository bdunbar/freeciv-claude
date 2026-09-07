"""A real freeciv client: login, pregame setup, and issuing player actions.

This speaks the same protocol as freeciv-gtk3.22, so the server applies
exactly the same rules and fog of war to us as to any human player.
"""

import socket
import time

from . import fcpath, state
from .protocol.connection import Connection

#: common/fc_types.h -- "no target of this kind".
NO_TARGET = -1
EXTRA_NONE = -1
#: enum unit_activity's terminator; what a non-activity order carries.
ACTIVITY_LAST = 16
#: enum gen_action's "no action", which is ACTION_COUNT itself.
ACTION_NONE = 125


def _unit_order(o):
    """Fill one unit_order struct the way client/goto.c does: every field
    the order does not use carries its explicit "none" value."""
    order = o.get("order", state.ORDER_MOVE)
    return {
        "order": order,
        "dir": o.get("dir", 0),
        "activity": o.get("activity", ACTIVITY_LAST)
                    if order == state.ORDER_ACTIVITY else ACTIVITY_LAST,
        "target": o.get("target", NO_TARGET),
        "sub_target": o.get("sub_target", NO_TARGET),
        "action": o.get("action", ACTION_NONE),
    }


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
        """orders: list of dicts with keys order/dir/activity/target/
        sub_target/action. 3.2 sends these as unit_order structs rather than
        the parallel arrays 2.6 used."""
        unit = self.game.units.get(unit_id)
        if unit is None:
            return False
        n = len(orders)
        if n == 0:
            return False
        return self.conn.send(
            "PACKET_UNIT_ORDERS",
            unit_id=unit_id,
            src_tile=unit["tile"],
            length=n,
            repeat=False,
            vigilant=False,
            orders=[_unit_order(o) for o in orders],
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

    def do_activity(self, unit_id, activity, target=EXTRA_NONE):
        if activity == state.ACTIVITY_EXPLORE:
            # The 3.2 server refuses ACTIVITY_EXPLORE here and wants the
            # server-side agent instead (unithand.c).
            return self.explore(unit_id)
        # Setting an activity by hand takes the unit back from any server
        # agent, exactly as request_new_unit_activity_targeted() does.
        self.set_server_side_agent(unit_id, state.SSA_NONE)
        self.conn.send("PACKET_UNIT_CHANGE_ACTIVITY",
                       unit_id=unit_id, activity=activity, target=target)
        return True

    def do_action(self, unit_id, action_type, target_id,
                  sub_target=NO_TARGET, name=""):
        """3.2 routes one-off unit actions through the generalized action
        system rather than a packet per action."""
        self.conn.send("PACKET_UNIT_DO_ACTION",
                       actor_id=unit_id, target_id=target_id,
                       sub_tgt_id=sub_target, name=name,
                       action_type=action_type)
        return True

    def build_city(self, unit_id, name):
        unit = self.game.units.get(unit_id)
        if unit is None:
            return False
        # ACTION_FOUND_CITY targets a tile, so the target is where we stand.
        return self.do_action(unit_id, state.ACTION_FOUND_CITY,
                              unit["tile"], name=name)

    def set_server_side_agent(self, unit_id, agent):
        self.conn.send("PACKET_UNIT_SERVER_SIDE_AGENT_SET",
                       unit_id=unit_id, agent=agent)
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

    def set_worklist(self, city_id, entries):
        """entries: [(kind, value)] to build after the current item."""
        self.conn.send("PACKET_CITY_WORKLIST", city_id=city_id,
                       worklist=list(entries))
        return True

    def make_worker(self, city_id, tile_id):
        """Put a citizen to work on a tile, taking them off whatever they
        were doing. The server rearranges the rest if it has to."""
        self.conn.send("PACKET_CITY_MAKE_WORKER",
                       city_id=city_id, tile_id=tile_id)
        return True

    def make_specialist(self, city_id, tile_id):
        """Take the citizen off a worked tile; they become the default
        specialist (an entertainer, in the classic ruleset)."""
        self.conn.send("PACKET_CITY_MAKE_SPECIALIST",
                       city_id=city_id, tile_id=tile_id)
        return True

    def change_specialist(self, city_id, from_id, to_id):
        self.conn.send("PACKET_CITY_CHANGE_SPECIALIST",
                       city_id=city_id, **{"from": from_id, "to": to_id})
        return True

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

    # -- diplomacy -----------------------------------------------------
    #
    # A treaty is a meeting with clauses on the table. Either side opens the
    # meeting, both add clauses, and it takes effect only once both have
    # accepted -- where "accept" means accepting the table as it stands, so
    # any later clause clears both acceptances.

    def init_meeting(self, counterpart):
        """Ask to open a meeting. The server refuses unless we have contact
        or an embassy (could_meet_with_player)."""
        self.conn.send("PACKET_DIPLOMACY_INIT_MEETING_REQ",
                       counterpart=counterpart)
        return True

    def cancel_meeting(self, counterpart):
        self.conn.send("PACKET_DIPLOMACY_CANCEL_MEETING_REQ",
                       counterpart=counterpart)
        return True

    def create_clause(self, counterpart, giver, clause_type, value=0):
        """Put a clause on the table. `giver` is the player number of
        whoever hands the thing over -- us or them."""
        self.conn.send("PACKET_DIPLOMACY_CREATE_CLAUSE_REQ",
                       counterpart=counterpart, giver=giver,
                       type=clause_type, value=value)
        return True

    def remove_clause(self, counterpart, giver, clause_type, value=0):
        self.conn.send("PACKET_DIPLOMACY_REMOVE_CLAUSE_REQ",
                       counterpart=counterpart, giver=giver,
                       type=clause_type, value=value)
        return True

    def accept_treaty(self, counterpart):
        """Toggle our acceptance of the treaty as it currently stands."""
        self.conn.send("PACKET_DIPLOMACY_ACCEPT_TREATY_REQ",
                       counterpart=counterpart)
        return True

    def cancel_pact(self, other, clause=None):
        """Break what we have with `other`, one step at a time: alliance ->
        armistice/peace -> war. CLAUSE_CEASEFIRE is the client's dummy value
        for "downgrade whatever we have" (client/gui-gtk-3.22/plrdlg.c);
        CLAUSE_VISION and CLAUSE_SHARED_TILES withdraw just those instead."""
        self.conn.send("PACKET_DIPLOMACY_CANCEL_PACT",
                       other_player_id=other,
                       clause=state.CLAUSE_CEASEFIRE if clause is None
                              else clause)
        return True

    def end_phase(self):
        self.conn.send("PACKET_PLAYER_PHASE_DONE", turn=self.game.turn)
        self._phase_started = False
        return True

    def auto_settler(self, unit_id):
        """Hand a worker to the server's auto-worker logic."""
        return self.set_server_side_agent(unit_id, state.SSA_AUTOSETTLER)

    def explore(self, unit_id):
        """Hand a unit to the server's auto-explore logic."""
        return self.set_server_side_agent(unit_id, state.SSA_AUTOEXPLORE)
