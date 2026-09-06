"""Map topology: tile indices, native/map coordinates, directions.

Freeciv keeps three coordinate systems. We need two:
  * native coords  -- what tile indices are laid out in (index = y*xsize + x)
  * map coords     -- what the DIRECTION deltas in unit orders operate on
On non-isometric maps the two coincide; on isometric maps they don't, and
getting this wrong silently sends units to the wrong tiles.
"""

# enum topo_flag (common/fc_types.h). 3.2 renumbered these and moved
# wrapping out into its own enum, sent as MAP_INFO's `wrap_id`; the
# TF_OLD_WRAP* bits are only kept for reading pre-3.1 savegames.
TF_ISO = 1
TF_HEX = 2
TF_OLD_WRAPX = 4
TF_OLD_WRAPY = 8

# enum wrap_flag
WRAP_X = 1
WRAP_Y = 2

# direction8: NW N NE W E SW S SE, deltas in *map* coordinates.
# The deltas are the same in every topology (common/map.c); what changes is
# which of the eight are legal moves at all -- see valid_directions().
DIR_DX = (-1, 0, 1, -1, 1, -1, 0, 1)
DIR_DY = (-1, -1, -1, 0, 0, 1, 1, 1)
DIR_NAMES = ("NW", "N", "NE", "W", "E", "SW", "S", "SE")
DIR8_COUNT = 8

DIR8_NORTHWEST, DIR8_NORTH, DIR8_NORTHEAST = 0, 1, 2
DIR8_WEST, DIR8_EAST = 3, 4
DIR8_SOUTHWEST, DIR8_SOUTH, DIR8_SOUTHEAST = 5, 6, 7

# Cardinal directions differ per topology; for square-iso maps these four
# are the ones that share an edge rather than a corner.
DIR_REVERSE = (7, 6, 5, 4, 3, 2, 1, 0)


class Topology(object):
    def __init__(self, xsize, ysize, topology_id, wrap_id=0):
        self.xsize = xsize
        self.ysize = ysize
        self.topology_id = topology_id
        self.wrap_id = wrap_id

    @property
    def is_isometric(self):
        return bool(self.topology_id & (TF_ISO | TF_HEX))

    @property
    def wrap_x(self):
        return bool(self.wrap_id & WRAP_X)

    @property
    def wrap_y(self):
        return bool(self.wrap_id & WRAP_Y)

    @property
    def is_hex(self):
        return bool(self.topology_id & TF_HEX)

    def valid_directions(self):
        """Directions that are legal moves here (is_valid_dir_calculate()).

        Hex maps drop one diagonal pair: plain hex has no SE/NW, iso-hex --
        which is freeciv 3.2's default topology -- has no NE/SW. Sending a
        dropped direction gets the whole orders packet rejected.
        """
        if not self.is_hex:
            return tuple(range(DIR8_COUNT))
        dropped = ((DIR8_NORTHEAST, DIR8_SOUTHWEST) if self.is_isometric
                   else (DIR8_SOUTHEAST, DIR8_NORTHWEST))
        return tuple(d for d in range(DIR8_COUNT) if d not in dropped)

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
        """Tile index one step in `direction`, or None if off-map or if the
        direction does not exist in this topology."""
        if direction not in self.valid_directions():
            return None
        map_x, map_y = self.index_to_map(index)
        return self.map_to_index(map_x + DIR_DX[direction],
                                 map_y + DIR_DY[direction])

    def neighbours(self, index):
        """[(direction, neighbour_index)] for all on-map neighbours."""
        out = []
        for d in self.valid_directions():
            n = self.step(index, d)
            if n is not None:
                out.append((d, n))
        return out

    def direction_to(self, src, dst):
        """The direction stepping from src reaches dst, else None."""
        for d in self.valid_directions():
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
        """Moves needed ignoring terrain (map_vector_to_real_distance()).

        Square maps allow all eight steps, so it is a Chebyshev distance.
        Hex maps are missing one diagonal, so a vector pointing that way
        costs the full sum instead of the max.
        """
        dx, dy = self._map_delta(src, dst)
        if self.is_hex:
            if self.is_isometric:
                no_diagonal = (dx < 0 < dy) or (dy < 0 < dx)
            else:
                no_diagonal = (dx > 0 and dy > 0) or (dx < 0 and dy < 0)
            if no_diagonal:
                return abs(dx) + abs(dy)
        return max(abs(dx), abs(dy))

    def sq_distance(self, src, dst):
        if self.is_hex:
            # Hex has no pythagorean shortcut; freeciv just squares the
            # real distance (map_vector_to_sq_distance()).
            d = self.real_distance(src, dst)
            return d * d
        dx, dy = self._map_delta(src, dst)
        return dx * dx + dy * dy
