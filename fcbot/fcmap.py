"""Map topology: tile indices, native/map coordinates, directions.

Freeciv keeps three coordinate systems. We need two:
  * native coords  -- what tile indices are laid out in (index = y*xsize + x)
  * map coords     -- what the DIRECTION deltas in unit orders operate on
On non-isometric maps the two coincide; on isometric maps they don't, and
getting this wrong silently sends units to the wrong tiles.
"""

TF_WRAPX = 1
TF_WRAPY = 2
TF_ISO = 4
TF_HEX = 8

# direction8: NW N NE W E SW S SE, deltas in *map* coordinates.
DIR_DX = (-1, 0, 1, -1, 1, -1, 0, 1)
DIR_DY = (-1, -1, -1, 0, 0, 1, 1, 1)
DIR_NAMES = ("NW", "N", "NE", "W", "E", "SW", "S", "SE")
DIR8_COUNT = 8

# Cardinal directions differ per topology; for square-iso maps these four
# are the ones that share an edge rather than a corner.
DIR_REVERSE = (7, 6, 5, 4, 3, 2, 1, 0)


class Topology(object):
    def __init__(self, xsize, ysize, topology_id):
        self.xsize = xsize
        self.ysize = ysize
        self.topology_id = topology_id

    @property
    def is_isometric(self):
        return bool(self.topology_id & (TF_ISO | TF_HEX))

    @property
    def wrap_x(self):
        return bool(self.topology_id & TF_WRAPX)

    @property
    def wrap_y(self):
        return bool(self.topology_id & TF_WRAPY)

    @property
    def is_hex(self):
        return bool(self.topology_id & TF_HEX)

    def size(self):
        return self.xsize * self.ysize

    # -- index <-> native ---------------------------------------------
    def index_to_native(self, index):
        return index % self.xsize, index // self.xsize

    def native_to_index(self, nat_x, nat_y):
        return nat_y * self.xsize + nat_x

    # -- native <-> map -----------------------------------------------
    def native_to_map(self, nat_x, nat_y):
        if self.is_isometric:
            map_x = (nat_y + (nat_y & 1)) // 2 + nat_x
            map_y = nat_y - map_x + self.xsize
            return map_x, map_y
        return nat_x, nat_y

    def map_to_native(self, map_x, map_y):
        if self.is_isometric:
            nat_y = map_x + map_y - self.xsize
            nat_x = (2 * map_x - nat_y - (nat_y & 1)) // 2
            return nat_x, nat_y
        return map_x, map_y

    # -- normalization -------------------------------------------------
    def normalize_native(self, nat_x, nat_y):
        """Wrap into range, or None if the position is off-map."""
        if self.wrap_x:
            nat_x %= self.xsize
        elif not 0 <= nat_x < self.xsize:
            return None
        if self.wrap_y:
            nat_y %= self.ysize
        elif not 0 <= nat_y < self.ysize:
            return None
        return nat_x, nat_y

    def map_to_index(self, map_x, map_y):
        nat = self.normalize_native(*self.map_to_native(map_x, map_y))
        if nat is None:
            return None
        return self.native_to_index(*nat)

    def index_to_map(self, index):
        return self.native_to_map(*self.index_to_native(index))

    # -- movement ------------------------------------------------------
    def step(self, index, direction):
        """Tile index one step in `direction`, or None if off-map."""
        map_x, map_y = self.index_to_map(index)
        return self.map_to_index(map_x + DIR_DX[direction],
                                 map_y + DIR_DY[direction])

    def neighbours(self, index):
        """[(direction, neighbour_index)] for all on-map neighbours."""
        out = []
        for d in range(DIR8_COUNT):
            n = self.step(index, d)
            if n is not None:
                out.append((d, n))
        return out

    def direction_to(self, src, dst):
        """The direction stepping from src reaches dst, else None."""
        for d in range(DIR8_COUNT):
            if self.step(src, d) == dst:
                return d
        return None

    # -- distances ------------------------------------------------------
    def _map_delta(self, src, dst):
        sx, sy = self.index_to_map(src)
        dx_, dy_ = self.index_to_map(dst)
        dx, dy = dx_ - sx, dy_ - sy
        # Shortest wrap-around delta, mirroring base_map_distance_vector.
        if self.wrap_x:
            half = self.xsize // 2
            while dx > half:
                dx -= self.xsize
            while dx < -half:
                dx += self.xsize
        if self.wrap_y:
            half = self.ysize // 2
            while dy > half:
                dy -= self.ysize
            while dy < -half:
                dy += self.ysize
        return dx, dy

    def real_distance(self, src, dst):
        """Moves needed ignoring terrain: Chebyshev distance in map coords."""
        dx, dy = self._map_delta(src, dst)
        return max(abs(dx), abs(dy))

    def sq_distance(self, src, dst):
        dx, dy = self._map_delta(src, dst)
        return dx * dx + dy * dy
