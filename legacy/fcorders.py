"""Encode unit orders into a Freeciv 2.6 savegame.

Encoding mirrors server/savegame2.c: order2char, dir2char, activity2char and
num2char. Orders written this way are executed by execute_unit_orders() at the
start of the next phase, for every player -- AI-controlled or not, connected or
not (server/srv_main.c:1178).
"""

from fcmap import DIR_CHARS

ORDER_CHARS = {
    'move': 'm',
    'full_mp': 'w',       # wait for full movement points
    'build_city': 'b',
    'activity': 'a',
    'disband': 'd',
    'build_wonder': 'u',
    'trade_route': 't',
    'homecity': 'h',
    'action_move': 'x',
}

ACTIVITY_CHARS = {
    'idle': 'w', 'pollution': 'p', 'mine': 'm', 'irrigate': 'i',
    'fortified': 'f', 'fortress': 't', 'sentry': 's', 'pillage': 'e',
    'goto': 'g', 'explore': 'x', 'transform': 'o', 'airbase': 'a',
    'fortifying': 'y', 'fallout': 'u', 'base': 'b', 'road': 'R',
    'convert': 'c',
}

NUM_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-+"


def num2char(n):
    if n is None or n < 0 or n >= len(NUM_CHARS):
        return '?'
    return NUM_CHARS[n]


class Order:
    """One step in a unit's order queue."""

    def __init__(self, kind, direction=None, activity=None, target=None):
        if kind not in ORDER_CHARS:
            raise ValueError('unknown order kind: %r' % kind)
        if kind == 'move' and direction is None:
            raise ValueError('move order requires a direction')
        if kind == 'activity':
            if activity not in ACTIVITY_CHARS:
                raise ValueError('unknown activity: %r' % activity)
        self.kind = kind
        self.direction = direction
        self.activity = activity
        self.target = target

    def __repr__(self):
        bits = [self.kind]
        if self.direction is not None:
            bits.append(DIR_CHARS[self.direction])
        if self.activity:
            bits.append(self.activity)
        return 'Order(%s)' % ' '.join(bits)


def encode(orders):
    """Encode a list of Order into the four savegame strings."""
    o_list, d_list, a_list, t_list = [], [], [], []
    for o in orders:
        o_list.append(ORDER_CHARS[o.kind])
        d_list.append(DIR_CHARS[o.direction] if o.direction is not None else '?')
        a_list.append(ACTIVITY_CHARS[o.activity] if o.activity else '?')
        t_list.append(num2char(o.target))
    return ''.join(o_list), ''.join(d_list), ''.join(a_list), ''.join(t_list)


def clear_orders(sf, table, row_index):
    sf.update_row(table, row_index,
                  orders_length=0, orders_index=0,
                  orders_repeat=False, orders_vigilant=False,
                  orders_list='-', dir_list='-',
                  activity_list='-', tgt_list='-')


def set_orders(sf, table, row_index, orders, repeat=False, vigilant=False,
               activity_idle=0):
    """Write an order queue onto one unit row, clearing any current activity."""
    if not orders:
        clear_orders(sf, table, row_index)
        return
    o, d, a, t = encode(orders)
    sf.update_row(table, row_index,
                  orders_length=len(orders), orders_index=0,
                  orders_repeat=repeat, orders_vigilant=vigilant,
                  orders_list=o, dir_list=d,
                  activity_list=a, tgt_list=t,
                  # A lingering activity (fortified, sentry, ...) would other-
                  # wise compete with the new orders.
                  activity=activity_idle, activity_count=0,
                  done_moving=False)


# ---- convenience constructors ----

def move(direction):
    return Order('move', direction=direction)


def move_path(fcmap_obj, start, path_dirs):
    return [Order('move', direction=d) for d in path_dirs]


def found_city():
    return Order('build_city')


def do_activity(name, target=None):
    return Order('activity', activity=name, target=target)


def wait_full_mp():
    return Order('full_mp')
