"""Pathfinding over tiles our player actually knows about.

Uses the same passability rule the server does: a terrain is passable for a
unit if the terrain's `native_to` bitvector has the unit's class bit set.
Unknown tiles are treated as passable-but-expensive so units will probe into
the fog rather than refusing to move.
"""

import heapq

from .state import TILE_UNKNOWN

UNKNOWN_COST = 3        # optimistic guess for unexplored tiles


def is_native(terrain, unit_class_id):
    if terrain is None:
        return False
    return bool(terrain["native_to"] >> unit_class_id & 1)


def move_cost(game, index, unit_class_id):
    """Cost to enter `index`, or None if impassable."""
    tile = game.tiles.get(index)
    if tile is None or tile["known"] == TILE_UNKNOWN:
        return UNKNOWN_COST
    terrain = game.ruleset.terrains.get(tile["terrain"])
    if not is_native(terrain, unit_class_id):
        return None
    return max(1, terrain["movement_cost"])


def find_path(game, start, goal, unit_class_id, max_nodes=20000,
              blocked=()):
    """Dijkstra from start to goal. Returns [tile indices] excluding start."""
    if start == goal:
        return []
    topo = game.topo
    dist = {start: 0}
    prev = {}
    heap = [(0, start)]
    visited = 0

    while heap:
        d, cur = heapq.heappop(heap)
        if cur == goal:
            break
        if d > dist.get(cur, 1 << 30):
            continue
        visited += 1
        if visited > max_nodes:
            break
        for _, nxt in topo.neighbours(cur):
            if nxt in blocked and nxt != goal:
                continue
            c = move_cost(game, nxt, unit_class_id)
            if c is None:
                continue
            nd = d + c
            if nd < dist.get(nxt, 1 << 30):
                dist[nxt] = nd
                prev[nxt] = cur
                heapq.heappush(heap, (nd, nxt))

    if goal not in prev and goal != start:
        return None
    path = []
    cur = goal
    while cur != start:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return path


def path_directions(topo, start, path):
    """Turn a tile path into the direction list unit orders need."""
    dirs = []
    cur = start
    for nxt in path:
        d = topo.direction_to(cur, nxt)
        if d is None:
            return None         # non-adjacent step: path is unusable
        dirs.append(d)
        cur = nxt
    return dirs


def nearest(game, start, candidates, unit_class_id):
    """Closest reachable candidate tile, as (index, path)."""
    best = None
    for c in sorted(candidates,
                    key=lambda t: game.topo.real_distance(start, t)):
        if best is not None and \
           game.topo.real_distance(start, c) >= len(best[1]):
            break
        path = find_path(game, start, c, unit_class_id)
        if path is not None and (best is None or len(path) < len(best[1])):
            best = (c, path)
    return best
