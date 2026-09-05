"""Map geometry for Freeciv 2.6 savegames.

Savegames store *native* coordinates, but Freeciv's direction vectors are
defined in *map* coordinates. On isometric topologies these differ, so every
neighbour lookup has to round-trip through the conversion below (mirrors
NATIVE_TO_MAP_POS / MAP_TO_NATIVE_POS in common/map.h).
"""

TF_WRAPX = 1
TF_WRAPY = 2
TF_ISO = 4
TF_HEX = 8

_TOPO_NAMES = {'WRAPX': TF_WRAPX, 'WRAPY': TF_WRAPY, 'ISO': TF_ISO, 'HEX': TF_HEX}

# Indexed by enum direction8 (common/fc_types.h):
# 0=NW 1=N 2=NE 3=W 4=E 5=SW 6=S 7=SE
DIR_DX = (-1, 0, 1, -1, 1, -1, 0, 1)
DIR_DY = (-1, -1, -1, 0, 0, 1, 1, 1)
DIR_NAMES = ('NW', 'N', 'NE', 'W', 'E', 'SW', 'S', 'SE')
# Numberpad chars used by savegame2.c:dir2char
DIR_CHARS = ('7', '8', '9', '4', '6', '1', '2', '3')

DIR_BY_NAME = {n: i for i, n in enumerate(DIR_NAMES)}


def parse_topology(s):
    flags = 0
    for part in (s or '').split('|'):
        part = part.strip().upper()
        if part in _TOPO_NAMES:
            flags |= _TOPO_NAMES[part]
    return flags


class Map:
    def __init__(self, sf):
        self.sf = sf
        self.topology = 0
        st = sf.table('settings', 'set')
        if st:
            for r in st.rows:
                if r.get('name') == 'topology':
                    self.topology = parse_topology(r.get('value'))
        # Dimensions are not stored explicitly; derive them from the terrain
        # rows, which are one string of xsize chars per row.
        self.terrain_rows = self._rows('map', 't')
        self.ysize = len(self.terrain_rows)
        self.xsize = len(self.terrain_rows[0]) if self.terrain_rows else 0
        self.ident2terrain = {}
        tt = sf.table('savefile', 'terrident')
        if tt:
            for r in tt.rows:
                self.ident2terrain[r['identifier']] = r['name']

    def _rows(self, section, prefix):
        out, i = [], 0
        while True:
            v = self.sf.get(section, '%s%04d' % (prefix, i))
            if v is None:
                break
            out.append(v)
            i += 1
        return out

    @property
    def is_iso(self):
        return bool(self.topology & (TF_ISO | TF_HEX))

    # ---- coordinate conversion ----

    def native_to_map(self, nx, ny):
        if self.is_iso:
            mx = (ny + (ny & 1)) // 2 + nx
            return mx, ny - mx + self.xsize
        return nx, ny

    def map_to_native(self, mx, my):
        if self.is_iso:
            ny = mx + my - self.xsize
            return (2 * mx - ny - (ny & 1)) // 2, ny
        return mx, my

    def normalize(self, nx, ny):
        """Wrap/validate a native position. Returns None if off-map."""
        if self.topology & TF_WRAPX:
            nx %= self.xsize
        elif not (0 <= nx < self.xsize):
            return None
        if self.topology & TF_WRAPY:
            ny %= self.ysize
        elif not (0 <= ny < self.ysize):
            return None
        return nx, ny

    def neighbour(self, nx, ny, direction):
        """Native position one step in `direction` (a direction8 index)."""
        mx, my = self.native_to_map(nx, ny)
        n = self.map_to_native(mx + DIR_DX[direction], my + DIR_DY[direction])
        return self.normalize(*n)

    def direction_to(self, from_pos, to_pos):
        """Direction index stepping from one native tile toward an adjacent one."""
        for d in range(8):
            if self.neighbour(from_pos[0], from_pos[1], d) == tuple(to_pos):
                return d
        return None

    def step_toward(self, from_pos, to_pos):
        """Greedy single step: the adjacent tile minimising map-coord distance."""
        best, best_d = None, None
        tx, ty = self.native_to_map(*to_pos)
        for d in range(8):
            n = self.neighbour(from_pos[0], from_pos[1], d)
            if n is None:
                continue
            mx, my = self.native_to_map(*n)
            dist = max(abs(self._wrap_delta(mx, tx, self.xsize)), abs(my - ty))
            if best is None or dist < best:
                best, best_d = dist, d
        return best_d

    def _wrap_delta(self, a, b, size):
        d = a - b
        if self.topology & TF_WRAPX:
            d = (d + size // 2) % size - size // 2
        return d

    # ---- terrain ----

    def terrain_at(self, nx, ny):
        """Ground-truth terrain name. Use known_terrain_at for agent-visible data."""
        if 0 <= ny < self.ysize and 0 <= nx < len(self.terrain_rows[ny]):
            return self.ident2terrain.get(self.terrain_rows[ny][nx], '?')
        return None
