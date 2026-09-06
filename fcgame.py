#!/usr/bin/env python3
"""Host a game of freeciv that you play against Claude.

    ./fcgame.py host --ai 3 --skill hard

Starts a freeciv server, connects Claude's client to it, then waits for you
to join with your normal freeciv client and pick a nation. Once you click
'Ready', the game starts and Claude plays its own turns.

Claude sees exactly what the server tells it -- same fog of war as you.
"""

import argparse
import re
import sys
import time

from fcbot.agent import Agent, Strategy
from fcbot.client import Client
from fcbot.server import Server
from fcbot import state

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
                  timeout=args.timeout)
    log("ruleset=%s  ai_players=%d (%s)" % (args.ruleset, args.ai, args.skill))

    client = Client(port=args.port, username=args.username, log=log)
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
        log("Claude is playing the %s (leader %s)" %
            (nation["adjective"], args.leader))
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

    log("you are ready -- Claude is readying up, game starting")
    client.set_ready(True)
    client.pump(1.0)

    # Readying up normally triggers the start by itself; nudge the server if
    # it has not begun after a few seconds.
    started = time.time()
    while not game.units and time.time() - started < 10:
        client.pump(1.0)
    if not game.units:
        srv.start_game()

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
            summarise(game, report, agent)
            client.end_phase()
            client.pump(0.5)
        elif time.time() - idle_since > args.stall_timeout:
            log("no phase for %ds; stopping." % args.stall_timeout)
            break

    if args.save_on_exit:
        srv.save(args.save_on_exit)
        time.sleep(1)
    srv.stop()
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
    host.add_argument("--map-size", type=int, default=None,
                      help="map size in thousands of tiles")
    host.add_argument("--seed", type=int, default=None)
    host.add_argument("--nation", default="Roman",
                      help="nation for Claude to play")
    host.add_argument("--leader", default="Claudius")
    host.add_argument("--username", default="claude")
    host.add_argument("--client", default=DEFAULT_CLIENT,
                      help="command shown for joining with your own client")
    host.add_argument("--target-cities", type=int, default=6)
    host.add_argument("--timeout", type=int, default=0,
                      help="server turn timeout in seconds (0 = untimed)")
    host.add_argument("--savedir", default="games")
    host.add_argument("--server-log", default=None)
    host.add_argument("--join-timeout", type=int, default=900)
    host.add_argument("--stall-timeout", type=int, default=600)
    host.add_argument("--save-on-exit", default=None)
    host.set_defaults(func=cmd_host)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("\ninterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
