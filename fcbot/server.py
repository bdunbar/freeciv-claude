"""Launch and drive a freeciv-server process.

We own the server, so its console (on stdin) is available for setup and for
starting the game. That is also how the human player's seat gets reserved.
"""

import os
import re
import shutil
import subprocess
import threading
import time

#: The protocol layer is generated from freeciv 3.2's packets.def, so the
#: server has to be a 3.2 one. Ubuntu 24.04 ships 3.1 in apt; the Flathub
#: client package bundles a matching 3.2 freeciv-server, so prefer that.
REQUIRED_VERSION = (3, 2)
FLATPAK_APP = "org.freeciv.gtk322"
SERVER_BIN = "/usr/games/freeciv-server"

_VERSION_RE = re.compile(r"server for Freeciv version (\d+)\.(\d+)\.(\d+)")


def _flatpak_installed(app=FLATPAK_APP):
    if not shutil.which("flatpak"):
        return False
    try:
        out = subprocess.run(["flatpak", "info", app],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def default_command(savedir):
    """The argv prefix that runs a freeciv 3.2 server.

    The flatpak sandbox only maps ~/.freeciv by default, so the save
    directory is handed in explicitly rather than relying on a persistent
    `flatpak override`.
    """
    if _flatpak_installed():
        return ["flatpak", "run", "--command=freeciv-server",
                "--filesystem=%s" % savedir, FLATPAK_APP]
    for candidate in (SERVER_BIN, shutil.which("freeciv-server")):
        if candidate and os.path.exists(candidate):
            return [candidate]
    raise RuntimeError(
        "no freeciv-server found: install the %s flatpak "
        "(flatpak install flathub %s) or a freeciv %d.%d server"
        % (FLATPAK_APP, FLATPAK_APP, REQUIRED_VERSION[0], REQUIRED_VERSION[1]))


#: `set topology` is a bitwise setting; these are the four shapes a map can
#: have. Freeciv 3.2's own default is iso-hex, which is the odd one out --
#: the other three are square tiles, differing only in how they are drawn.
#: The human's client picks a matching tileset for whichever is set.
TOPOLOGIES = {
    "square": "",                # overhead squares, the "classic" look
    "iso": "ISO",                # the same squares, drawn as diamonds
    "hex": "Hex",
    "iso-hex": "ISO|Hex",        # freeciv 3.2's default
}


class Server(object):
    def __init__(self, port=5556, savedir=None, ruleset="classic",
                 binary=None, log_path=None):
        self.port = port
        self.ruleset = ruleset
        # Absolute: flatpak's --filesystem= rejects relative paths, and the
        # sandboxed server does not share our working directory anyway.
        self.savedir = os.path.abspath(savedir or "games")
        # A string keeps the old single-binary form working; the default is
        # a full argv because the flatpak needs `flatpak run ...` in front.
        if binary is None:
            self.command_prefix = None      # resolved in start(), needs savedir
        elif isinstance(binary, str):
            self.command_prefix = [binary]
        else:
            self.command_prefix = list(binary)
        self.version = None
        self.log_path = log_path
        self.proc = None
        self.output = []
        self._reader = None

    def start(self, extra_args=()):
        os.makedirs(self.savedir, exist_ok=True)
        if self.command_prefix is None:
            self.command_prefix = default_command(self.savedir)
        args = list(self.command_prefix)
        args += ["-p", str(self.port), "-s", self.savedir,
                 "--Announce", "none"]
        args.extend(extra_args)
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_for_line("Now accepting new client connections", 30)
        self._check_version()
        return self

    def _check_version(self):
        for line in self.output:
            m = _VERSION_RE.search(line)
            if m:
                self.version = tuple(int(g) for g in m.groups())
                break
        if self.version and self.version[:2] != REQUIRED_VERSION:
            self.stop()
            raise RuntimeError(
                "freeciv-server is %d.%d.%d but the protocol layer speaks "
                "%d.%d; %s"
                % (self.version + REQUIRED_VERSION
                   + (" ".join(self.command_prefix),)))

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
                  seed=None, settings=None, timeout=0, topology=None,
                  tiles_per_player=None):
        """Set up the game before anyone joins.

        ai_players counts only the computer players; the two human seats
        (yours and the bot's) are added on top via aifill.

        `mapsize` is the whole map in thousands of tiles (the server caps it
        at 2048, i.e. two million tiles); `tiles_per_player` sizes the map
        from the number of players instead, which is the server's own
        default at 100. Pass one or the other, not both.
        """
        self.command("set ruleset %s" % self.ruleset)
        if skill:
            # The skill level is a bare console command ("easy", "hard", ...)
            # and sets the default for AI players aifill creates next.
            self.command(skill)
        self.command("set aifill %d" % (ai_players + 2))
        self.command("set timeout %d" % timeout)
        if topology is not None:
            if topology not in TOPOLOGIES:
                raise ValueError("topology must be one of %s, got %r"
                                 % (", ".join(sorted(TOPOLOGIES)), topology))
            value = TOPOLOGIES[topology]
            # An empty bitwise value has to be quoted or the console reads
            # the line as a query rather than an assignment.
            self.command('set topology "%s"' % value)
        if mapsize is not None:
            # `size` is only consulted when `mapsize` says to use it. The
            # 3.2 server ships with mapsize=PLAYER, which sizes the map from
            # `tilesperplayer` instead and silently ignores `size` -- so
            # setting `size` alone does nothing at all.
            self.command("set mapsize FULLSIZE")
            self.command("set size %d" % mapsize)
        if tiles_per_player is not None:
            self.command("set mapsize PLAYER")
            self.command("set tilesperplayer %d" % tiles_per_player)
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
