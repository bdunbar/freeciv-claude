"""Breadth-first pathfinding over terrain the player actually knows about.

Unknown tiles are treated as passable-but-costly so the agent will explore into
them rather than refusing to move; impassable known terrain is avoided.
"""

from collections import deque

WATER = {'Ocean', 'Deep Ocean', 'Lake'}
BLOCKED = {'Inaccessible'}
UNKNOWN = '?'


def passable(terrain, domain='land'):
    if terrain in BLOCKED:
        return False
    if terrain == UNKNOWN:
        return True          # worth walking into; the server will refuse if not
    if domain == 'land':
        return terrain not in WATER
    if domain == 'sea':
        return terrain in WATER
    return True


def find_path(view, start, goal, domain='land', max_nodes=20000):
    """Return a list of direction indices from start to goal, or None."""
    m = view.map
    start, goal = tuple(start), tuple(goal)
    if start == goal:
        return []
    seen = {start: None}
    q = deque([start])
    nodes = 0
    while q and nodes < max_nodes:
        cur = q.popleft()
        nodes += 1
        for d in range(8):
            nxt = m.neighbour(cur[0], cur[1], d)
            if nxt is None or nxt in seen:
                continue
            if nxt != goal and not passable(view.known_terrain(*nxt), domain):
                continue
            seen[nxt] = (cur, d)
            if nxt == goal:
                dirs = []
                node = nxt
                while seen[node] is not None:
                    prev, dd = seen[node]
                    dirs.append(dd)
                    node = prev
                return list(reversed(dirs))
            q.append(nxt)
    return None
