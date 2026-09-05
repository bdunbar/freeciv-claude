"""Launch and drive a freeciv-server process.

We own the server, so its console (on stdin) is available for setup and for
starting the game. That is also how the human player's seat gets reserved.
"""

import os
import subprocess
import threading
import time

SERVER_BIN = "/usr/games/freeciv-server"


class Server(object):
    def __init__(self, port=5556, savedir=None, ruleset="classic",
                 binary=SERVER_BIN, log_path=None):
        self.port = port
        self.ruleset = ruleset
        self.binary = binary
        self.savedir = savedir or os.path.join(os.getcwd(), "games")
        self.log_path = log_path
        self.proc = None
        self.output = []
        self._reader = None

    def start(self, extra_args=()):
        os.makedirs(self.savedir, exist_ok=True)
        args = [self.binary, "-p", str(self.port), "-s", self.savedir,
                "--Announce", "none"]
        args.extend(extra_args)
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_for_line("Now accepting new client connections", 30)
        return self

    def _read_output(self):
        log = open(self.log_path, "a") if self.log_path else None
        try:
            for line in self.proc.stdout:
                self.output.append(line.rstrip("\n"))
                if log:
                    log.write(line)
                    log.flush()
        except (ValueError, IOError):
            pass
        finally:
            if log:
                log.close()

    def _wait_for_line(self, needle, timeout):
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            while seen < len(self.output):
                if needle in self.output[seen]:
                    return True
                seen += 1
            time.sleep(0.05)
        raise RuntimeError("server did not start: %s" %
                           "\n".join(self.output[-10:]))

    def command(self, cmd):
        """Run a server console command."""
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("server is not running")
        self.proc.stdin.write(cmd.rstrip("\n") + "\n")
        self.proc.stdin.flush()
        time.sleep(0.05)
        return True

    def configure(self, ai_players=3, skill="normal", mapsize=None,
                  seed=None, settings=None, timeout=0):
        """Set up the game before anyone joins.

        ai_players counts only the computer players; the two human seats
        (yours and the bot's) are added on top via aifill.
        """
        self.command("set ruleset %s" % self.ruleset)
        if skill:
            # The skill level is a bare console command ("easy", "hard", ...)
            # and sets the default for AI players aifill creates next.
            self.command(skill)
        self.command("set aifill %d" % (ai_players + 2))
        self.command("set timeout %d" % timeout)
        if mapsize is not None:
            self.command("set size %d" % mapsize)
        if seed is not None:
            self.command("set mapseed %d" % seed)
            self.command("set gameseed %d" % seed)
        for key, value in (settings or {}).items():
            self.command("set %s %s" % (key, value))
        return self

    def start_game(self):
        return self.command("start")

    def save(self, name):
        return self.command("save %s" % name)

    def stop(self, timeout=10):
        if not self.proc:
            return
        try:
            if self.proc.poll() is None:
                self.command("quit")
                self.proc.wait(timeout=timeout)
        except Exception:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None
