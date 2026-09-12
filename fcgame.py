#!/usr/bin/env python3
"""Host a game of freeciv that you play against Claude.

    ./fcgame.py host --ai 3 --skill hard      # start a game
    echo '[...]' | ./fcgame.py play           # play one turn
    ./fcgame.py status                        # look without touching

Starts a freeciv server, connects a second client to it, then waits for you
to join with your own freeciv client and pick a nation. Once you click
'Ready', the game begins.

Who plays that second seat depends on --mode:

  interactive  (default) every turn is written out as an observation and
               the game waits for a file of orders back. The decisions are
               the model's; this program only carries them.
  auto         fcbot/agent.py plays, by fixed rules. No model involved --
               it logs in as `fcbot` rather than `claude` to keep that
               distinction visible in the game.

Either way it sees exactly what the server tells it: same fog of war as you.
"""

import argparse
import json
import os
import re
import sys
import time

from fcbot.agent import Agent, Strategy
from fcbot.client import Client
from fcbot.interactive import InteractiveAgent
from fcbot.server import Server, TOPOLOGIES
from fcbot import observe, state

STRIP_MARKUP = re.compile(r"\[/?c[^\]]*\]")

#: How to launch a matching 3.2 client. Ubuntu 24.04 has no freeciv 3.2
#: client in apt, so the Flathub package is the one that works here.
DEFAULT_CLIENT = "flatpak run org.freeciv.gtk322"


def log(msg):
    print(msg, flush=True)


def pick_nation(game, wanted):
    for nid, nation in game.ruleset.nations.items():
        if wanted.lower() in (nation["adjective"].lower(),
                              nation["rule_name"].lower()):
            return nid, nation
    return None, None


def humans_ready(game, bot_player):
    """True once at least one other connected human player is ready."""
    for pn, player in game.players.items():
        if pn == bot_player or game.is_ai(player):
            continue
        if player.get("is_connected") and player.get("is_ready"):
            return True
    return False


def other_humans(game, bot_player):
    return [p for pn, p in game.players.items()
            if pn != bot_player and not game.is_ai(p) and p.get("is_connected")]


def cmd_host(args):
    srv = Server(port=args.port, ruleset=args.ruleset, savedir=args.savedir,
                 log_path=args.server_log)
    log("starting freeciv-server on port %d ..." % args.port)
    srv.start()
    srv.configure(ai_players=args.ai, skill=args.skill,
                  mapsize=args.map_size, seed=args.seed,
                  timeout=args.timeout, topology=args.topology,
                  tiles_per_player=args.tiles_per_player)
    log("ruleset=%s  ai_players=%d (%s)  topology=%s"
        % (args.ruleset, args.ai, args.skill, args.topology))

    # The name on the wire should say who is actually deciding: the model
    # in interactive mode, the scripted agent in auto mode.
    username = args.username or ("claude" if args.mode == "interactive"
                                 else "fcbot")
    client = Client(port=args.port, username=username, log=log)
    client.login()
    client.pump(3.0)
    while client.player_no is None:
        client.pump(1.0)
    game = client.game

    nid, nation = pick_nation(game, args.nation)
    if nid is None:
        log("no nation matching %r; using the server's choice" % args.nation)
    else:
        client.select_nation(nid, args.leader)
        log("%s is playing the %s (leader %s)" %
            (username, nation["adjective"], args.leader))
    client.pump(1.0)
    # Deliberately NOT ready yet: the server starts the game the moment every
    # player is ready, and AI players always are. Readying now would start the
    # game before you had a chance to join.

    print()
    log("=" * 68)
    log(" Join the game with your own client:")
    log("")
    log("     %s -a -p %d -s localhost" % (args.client, args.port))
    log("")
    log(" ...then pick a nation and click 'Ready'. The game starts")
    log(" automatically once you are ready.")
    log("=" * 68)
    print()

    deadline = time.time() + args.join_timeout
    announced = set()
    while not humans_ready(game, client.player_no):
        client.pump(1.0)
        for p in other_humans(game, client.player_no):
            key = p["name"]
            if key not in announced:
                announced.add(key)
                log("%s joined (waiting for Ready)" % p["name"])
        if time.time() > deadline:
            log("nobody joined within %ds; giving up." % args.join_timeout)
            srv.stop()
            return 1

    log("you are ready -- %s is readying up, game starting" % username)
    client.set_ready(True)
    client.pump(1.0)

    # Readying up normally triggers the start by itself; nudge the server if
    # it has not begun after a few seconds.
    started = time.time()
    while not game.units and time.time() - started < 10:
        client.pump(1.0)
    if not game.units:
        srv.start_game()

    if args.mode == "interactive":
        turns_dir = args.turns_dir or os.path.join(args.savedir, "turns")
        agent = InteractiveAgent(client, turns_dir, log=log)
        log("interactive mode: each turn waits for orders in %s" % turns_dir)
    else:
        strategy = Strategy()
        strategy.target_cities = args.target_cities
        agent = Agent(client, strategy, log=log)
    return play_loop(client, agent, srv, args)


def play_loop(client, agent, srv, args):
    game = client.game
    last_turn = -1
    idle_since = time.time()

    while True:
        client.pump(0.5)

        if client._game_over:
            log("game over.")
            break
        if not srv.running:
            log("server exited.")
            break

        me = game.me
        if me is None or not game.units:
            if time.time() - idle_since > args.stall_timeout:
                log("no game state after %ds; stopping." % args.stall_timeout)
                break
            continue

        # Our phase is live when the server told us so and we have not
        # already declared ourselves done for this turn.
        if client._phase_started and not me.get("phase_done"):
            idle_since = time.time()
            if game.turn != last_turn:
                last_turn = game.turn
            report = agent.play_turn()
            if isinstance(report, dict):
                summarise(game, report, agent)
            client.end_phase()
            client.pump(0.5)
            if args.save_each_turn:
                srv.save("turn%04d" % game.turn)
            # Interactive turns block for as long as the model takes, so the
            # stall timer must not count that against us.
            idle_since = time.time()
        elif time.time() - idle_since > args.stall_timeout:
            log("no phase for %ds; stopping." % args.stall_timeout)
            break

    if args.save_on_exit:
        srv.save(args.save_on_exit)
        time.sleep(1)
    srv.stop()
    return 0


def _turn_files(turns_dir):
    """{turn number: True} for every observation written so far."""
    turns = {}
    for name in os.listdir(turns_dir):
        match = re.match(r"(\d{4})\.obs\.json$", name)
        if match:
            turns[int(match.group(1))] = True
    return turns


def _pending_turn(turns_dir):
    """The turn waiting on us: the newest observation with no orders yet."""
    if not os.path.isdir(turns_dir):
        return None
    turns = _turn_files(turns_dir)
    if not turns:
        return None
    latest = max(turns)
    if os.path.exists(os.path.join(turns_dir, "%04d.orders.json" % latest)):
        return None                 # already answered; the host is thinking
    return latest


def _await(predicate, timeout, poll=0.5):
    """Wait for `predicate()` to return something truthy, or None on timeout."""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() >= deadline:
            return None
        time.sleep(poll)


def cmd_rejoin(args):
    """Reconnect the seat to a server that is already running.

    `host` owns its server and dies with it; if the host process falls over
    -- a bad reload, an unhandled packet -- the game itself is still there,
    with our player sitting disconnected. Starting a new host would start a
    new server. This puts the seat back in the chair it was in.
    """
    client = Client(host=args.host, port=args.port, username=args.username,
                    log=log)
    log("reconnecting to %s:%d as %s ..." % (args.host, args.port,
                                             args.username))
    client.login()
    deadline = time.time() + 60
    while client.game.player_no is None and time.time() < deadline:
        client.pump(1.0)
    if client.game.player_no is None:
        log("connected, but the server gave us no player slot")
        return 1
    me = client.game.me or {}
    log("back in as %s (player %s), turn %d"
        % (me.get("name", "?"), client.game.player_no, client.game.turn))

    agent = InteractiveAgent(client, args.turns_dir, log=log)
    while not client._game_over:
        client.pump(1.0)
        if client._phase_started:
            agent.play_turn()
            client.end_phase()
    return 0


def cmd_play(args):
    """Submit one turn's orders and print the next observation.

    The file protocol is a good way for two processes to hand a game back and
    forth, but it is a poor thing to drive by hand: every turn costs a
    check for the observation, a write of the orders, and a wait for the
    reply, with the turn number tracked in between. This is all three in one
    call -- and because it blocks until the next observation, running it in
    the background turns "is it my turn?" from a question you have to keep
    asking into one you get told the answer to.
    """
    turns_dir = args.turns_dir
    # Reading stdin unasked would hang whenever stdin is an open pipe rather
    # than a terminal -- which is the normal case for a scripted caller. So
    # stdin is opt-in, the conventional way: `--orders -`.
    if args.orders == "-":
        orders_text = sys.stdin.read().strip()
    else:
        orders_text = (args.orders or "").strip()
    try:
        orders = json.loads(orders_text) if orders_text else []
    except ValueError as exc:
        log("orders are not valid JSON: %s" % exc)
        return 2
    if not isinstance(orders, list):
        log("orders must be a JSON list, got %s" % type(orders).__name__)
        return 2

    turn = _await(lambda: _pending_turn(turns_dir), args.timeout)
    if turn is None:
        log("no turn is waiting for orders in %s after %ds -- is the host "
            "running, and has the game started?" % (turns_dir, args.timeout))
        return 1

    path = os.path.join(turns_dir, "%04d.orders.json" % turn)
    tmp = path + ".partial"
    with open(tmp, "w") as fp:
        json.dump(orders, fp, indent=2)
        fp.write("\n")
    os.replace(tmp, path)
    log("turn %d: submitted %d order(s)" % (turn, len(orders)))

    # What the server made of them, then what it looks like now.
    result_path = os.path.join(turns_dir, "%04d.result.json" % turn)
    if _await(lambda: os.path.exists(result_path),
              min(30, args.timeout), poll=0.25) and orders:
        with open(result_path) as fp:
            for line in json.load(fp).get("results", []):
                print("  %s" % line)
        print()

    next_obs = os.path.join(turns_dir, "%04d.obs.json" % (turn + 1))
    if _await(lambda: os.path.exists(next_obs), args.timeout) is None:
        log("turn %d applied, but no observation for turn %d after %ds. The "
            "game may have ended, or the host may have stopped -- check the "
            "host log." % (turn, turn + 1, args.timeout))
        return 1
    with open(next_obs) as fp:
        print(observe.to_text(json.load(fp)))
    return 0


def cmd_status(args):
    """Show what the bot is looking at, without touching the game."""
    if not os.path.isdir(args.turns_dir):
        log("no turns directory at %s" % args.turns_dir)
        return 1
    files = sorted(f for f in os.listdir(args.turns_dir)
                   if f.endswith(".obs.json"))
    if not files:
        log("no observations written yet in %s" % args.turns_dir)
        return 1
    if args.turn is not None:
        wanted = "%04d.obs.json" % args.turn
        if wanted not in files:
            log("no observation for turn %d (have %s)" %
                (args.turn, ", ".join(f[:4] for f in files)))
            return 1
        chosen = wanted
    else:
        chosen = files[-1]
    with open(os.path.join(args.turns_dir, chosen)) as fp:
        obs = json.load(fp)
    print(observe.to_text(obs))
    orders = obs.get("orders_go_in")
    if orders and not os.path.exists(orders):
        print()
        print("Waiting for orders in %s" % orders)
    return 0


def summarise(game, report, agent):
    me = game.me
    cities = game.my_cities()
    units = game.my_units()
    known = sum(1 for t in game.tiles.values() if t["known"] != 0)
    research = game.my_research or {}
    tech_name = game.ruleset.techs.get(research.get("researching", -1), {}).get("name", "-")

    log("-- turn %d (%s) | %d cities, %d units, %d tiles known, %d gold, "
        "researching %s" % (game.turn, year_label(game.year), len(cities),
                            len(units), known, me.get("gold", 0), tech_name))
    for line in report["cities"].values():
        log("     %s" % line)
    if report["research"]:
        log("     research: %s" % report["research"])


def year_label(year):
    return "%d BC" % -year if year < 0 else "%d AD" % year


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    host = sub.add_parser("host", help="host a game and play against Claude")
    host.add_argument("--port", type=int, default=5556)
    host.add_argument("--ai", type=int, default=3,
                      help="number of computer players besides you and Claude")
    host.add_argument("--skill", default="normal",
                      choices=["novice", "easy", "normal", "hard",
                               "cheating", "experimental"])
    host.add_argument("--ruleset", default="classic")
    host.add_argument("--tiles-per-player", type=int, default=None,
                      help="size the map from the player count instead "
                           "(server default 100, max 1000). Mutually "
                           "exclusive with --map-size.")
    host.add_argument("--map-size", type=int, default=None,
                      help="whole map in thousands of tiles (max 2048). The "
                           "server ignores this unless mapsize is set to "
                           "FULLSIZE, which --map-size now does for you.")
    host.add_argument("--seed", type=int, default=None)
    host.add_argument("--topology", default="square",
                      choices=sorted(TOPOLOGIES),
                      help="tile shape: square (overhead squares, the "
                           "default here), iso (the same squares drawn as "
                           "diamonds), hex, or iso-hex (freeciv's own "
                           "default). Your client picks a matching tileset.")
    host.add_argument("--nation", default="Roman",
                      help="nation for Claude to play")
    host.add_argument("--leader", default="Claudius")
    host.add_argument("--username", default=None,
                      help="login name (default: 'claude' in interactive "
                           "mode, 'fcbot' in auto mode)")
    host.add_argument("--client", default=DEFAULT_CLIENT,
                      help="command shown for joining with your own client")
    host.add_argument("--target-cities", type=int, default=6)
    host.add_argument("--timeout", type=int, default=0,
                      help="server turn timeout in seconds (0 = untimed)")
    host.add_argument("--mode", default="interactive",
                      choices=["interactive", "auto"],
                      help="interactive: the model plays every turn through "
                           "files; auto: the scripted agent plays")
    host.add_argument("--turns-dir", default=None,
                      help="where interactive mode writes observations "
                           "(default: <savedir>/turns)")
    host.add_argument("--save-each-turn", action="store_true",
                      help="save the game every turn, so it survives a reboot")
    host.add_argument("--savedir", default="games")
    host.add_argument("--server-log", default=None)
    host.add_argument("--join-timeout", type=int, default=900)
    host.add_argument("--stall-timeout", type=int, default=600)
    host.add_argument("--save-on-exit", default=None)
    host.set_defaults(func=cmd_host)

    rejoin = sub.add_parser(
        "rejoin", help="reconnect the seat to a server already running")
    rejoin.add_argument("--host", default="localhost")
    rejoin.add_argument("--port", type=int, default=5556)
    rejoin.add_argument("--username", default="claude")
    rejoin.add_argument("--turns-dir", default=os.path.join("games", "turns"))
    rejoin.set_defaults(func=cmd_rejoin)

    play = sub.add_parser(
        "play", help="submit one turn's orders and print the next observation")
    play.add_argument("--turns-dir", default=os.path.join("games", "turns"))
    play.add_argument("--orders", default=None,
                      help="the orders as a JSON list, or '-' to read them "
                           "from stdin. Omitted means no orders, which ends "
                           "the phase unchanged -- a legitimate turn.")
    play.add_argument("--timeout", type=int, default=600,
                      help="seconds to wait for a turn to be ready, and "
                           "again for the next observation")
    play.set_defaults(func=cmd_play)

    status = sub.add_parser(
        "status", help="print the latest observation as readable text")
    status.add_argument("--turns-dir", default=os.path.join("games", "turns"))
    status.add_argument("--turn", type=int, default=None,
                        help="a specific turn (default: the latest)")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("\ninterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
