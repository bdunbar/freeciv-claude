"""Drive a headless freeciv-server one turn at a time.

The loop is: load a savegame, raise `endturn` by N, let the server play those
turns and end the game, then pick up the resulting savegame. Loading a save
from an already-ended game and continuing works fine, which is what makes the
step-by-step approach possible.
"""

import glob
import os
import subprocess
import threading
import time

SERVER = '/usr/games/freeciv-server'


class ServerError(RuntimeError):
    pass


class Runner:
    def __init__(self, workdir, port=5599, ruleset='classic', verbose=False):
        self.workdir = os.path.abspath(workdir)
        self.saves = os.path.join(self.workdir, 'saves')
        self.logs = os.path.join(self.workdir, 'logs')
        os.makedirs(self.saves, exist_ok=True)
        os.makedirs(self.logs, exist_ok=True)
        self.port = port
        self.ruleset = ruleset
        self.verbose = verbose

    # ---- internals ----

    def _run(self, commands, timeout=300, tag='run'):
        """Feed console commands, then let the server play to `endturn`.

        Two things bite here. `start` returns as soon as the game state flips to
        running, so sending `quit` after it kills the server before a single turn
        is played. And the server treats EOF on stdin as a quit -- which rules out
        subprocess.communicate(), since that closes stdin immediately. So we hold
        stdin open, drain stdout on a thread, and let `-e` end the process itself
        once the game reaches endturn.
        """
        script = '\n'.join(commands) + '\n'
        logfile = os.path.join(self.logs, '%s-%d.log' % (tag, int(time.time() * 1000)))
        proc = subprocess.Popen(
            [SERVER, '-s', self.saves, '-e', '-p', str(self.port),
             '-l', logfile, '--Announce', 'none'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)

        chunks = []
        reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()),
                                  daemon=True)
        reader.start()
        proc.stdin.write(script)
        proc.stdin.flush()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ServerError('server did not finish within %ds; see %s'
                              % (timeout, logfile))
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            reader.join(timeout=5)
        out = ''.join(c for c in chunks if c)
        if self.verbose:
            print(out[-3000:])
        return out, logfile

    def _newest_save(self, newer_than=0.0):
        best, best_t = None, newer_than
        for p in glob.glob(os.path.join(self.saves, '*.sav*')):
            t = os.path.getmtime(p)
            if t > best_t:
                best, best_t = p, t
        return best

    # ---- public API ----

    def new_game(self, players=3, size=2, topology='WRAPX', seed=None,
                 skill='hard', generator=None):
        """Start a fresh game and return the path to the turn-1 savegame.

        topology defaults to plain WRAPX (non-isometric) so that native and map
        coordinates coincide; the iso conversion is implemented but non-iso
        maps are far easier to reason about and to verify.
        """
        before = time.time()
        cmds = [
            'set minplayers 0',
            'set timeout -1',
            'set topology "%s"' % topology,
            'set size %d' % size,
            'set aifill %d' % players,
            'set autosaves "TURN"',
            'set saveturns 1',
            'set endturn 1',
        ]
        if seed is not None:
            cmds += ['set mapseed %d' % seed, 'set gameseed %d' % seed]
        if generator:
            cmds.append('set generator %s' % generator)
        cmds += [skill, 'start']
        out, log = self._run(cmds, tag='new')
        save = self._newest_save(before)
        if not save:
            raise ServerError('no savegame produced; see %s' % log)
        return save

    def step(self, save_path, turns=1, human_player=None):
        """Play `turns` turns from `save_path`; return the new savegame path.

        `human_player` is taken off AI control so that only the orders we wrote
        into the savegame drive it.
        """
        import secfile
        sf = secfile.SecFile(save_path)
        turn = sf.get('game', 'turn', 0)
        before = time.time()
        cmds = [
            'set minplayers 0',
            'set timeout -1',
            'load %s' % save_path,
            'set endturn %d' % (turn + turns),
        ]
        if human_player:
            # `aitoggle` is a *toggle*, not a setter: issuing it unconditionally
            # hands the player back to the AI on every second step. Only emit it
            # when the savegame actually has this player under AI control.
            for sec in sf.player_sections():
                if sf.get(sec, 'name') == human_player:
                    if sf.get(sec, 'ai.control'):
                        cmds.append('aitoggle "%s"' % human_player)
                    break
        cmds.append('start')
        out, log = self._run(cmds, tag='step')
        save = self._newest_save(before)
        if not save:
            raise ServerError('step produced no savegame; see %s\n%s'
                              % (log, out[-2000:]))
        return save
