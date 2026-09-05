"""Packet encode/decode, including freeciv's delta compression.

Delta rules (mirroring common/generate_packets.py):
  * Each packet type keeps a cache of the last packet seen, keyed by the
    values of its 'key' fields.
  * A delta packet is: a 'fields' bitvector over the non-key fields, then
    all key fields, then only those non-key fields whose bit is set.
  * Scalar bools are *folded into the bitvector*: the bit carries the value
    itself and no bytes are written for the field.
  * Arrays flagged 'diff' are sent as (uint8 index, value) pairs ending
    with index 255.
"""

from . import constants
from .dataio import DataIn, DataOut

_ARR_WHOLE = ("string", "bit_string", "city_map",
              "tech_list", "unit_list", "building_list")


def _bv_bytes(nbits):
    # Mirrors _BV_BYTES: zero bits still occupies one byte.
    return ((nbits - 1) // 8 + 1) if nbits > 0 else 1


def _is_folded_bool(field):
    return field.public_type == "bool" and not field.is_array


class Codec(object):
    def __init__(self, packets_by_number, caps=None):
        self.packets = packets_by_number
        # Held by reference: capabilities are negotiated *after* the codec is
        # built, and they decide which fields exist on the wire.
        self.caps = caps if isinstance(caps, set) else set(caps or ())
        self.recv_cache = {}
        self.send_cache = {}

    # -- capability gating --------------------------------------------
    def field_active(self, field):
        if field.add_cap and field.add_cap not in self.caps:
            return False
        if field.remove_cap and field.remove_cap in self.caps:
            return False
        return True

    def _fields_of(self, packet):
        return [f for f in packet.fields if self.field_active(f)]

    def _split(self, packet):
        active = self._fields_of(packet)
        keys = [f for f in active if f.is_key]
        others = [f for f in active if not f.is_key]
        return keys, others

    # -- defaults ------------------------------------------------------
    def zero(self, field):
        if field.dataio_type == "memory":
            return b"\0" * constants.resolve_size(field.sizes[0][0])
        if field.dataio_type == "string":
            if len(field.sizes) >= 2:
                return [""] * constants.resolve_size(field.sizes[0][0])
            return ""
        if field.dataio_type == "worklist":
            return []
        if field.dataio_type in ("tech_list", "unit_list", "building_list"):
            return []
        if field.dataio_type == "requirement":
            base = {"type": 0, "value": 0, "range": 0,
                    "survives": False, "present": False, "quiet": False}
        elif field.dataio_type == "action_probability":
            base = (0, 0)
        elif field.dataio_type == "bitvector":
            base = 0
        elif field.public_type == "bool":
            base = False
        elif field.dataio_type in ("ufloat", "sfloat"):
            base = 0.0
        else:
            base = 0

        if not field.sizes:
            return base
        if len(field.sizes) == 1:
            return [base] * constants.resolve_size(field.sizes[0][0])
        n1 = constants.resolve_size(field.sizes[0][0])
        n2 = constants.resolve_size(field.sizes[1][0])
        return [[base] * n2 for _ in range(n1)]

    def blank(self, packet):
        return {f.name: self.zero(f) for f in self._fields_of(packet)}

    # -- single-value get/put -----------------------------------------
    def _get_one(self, din, field):
        t = field.dataio_type
        if t == "bitvector":
            return din.get_bitvector(constants.bv_bytes(field.public_type))
        if t in ("ufloat", "sfloat"):
            return getattr(din, "get_" + t)(field.float_factor)
        return getattr(din, "get_" + t)()

    def _put_one(self, dout, field, value):
        t = field.dataio_type
        if t == "bitvector":
            dout.put_bitvector(value, constants.bv_bytes(field.public_type))
        elif t in ("ufloat", "sfloat"):
            getattr(dout, "put_" + t)(value, field.float_factor)
        else:
            getattr(dout, "put_" + t)(value)

    def _count(self, field, dim, values):
        full, transfer = field.sizes[dim]
        if transfer is not None:
            return int(values.get(transfer, 0) or 0)
        return constants.resolve_size(full)

    # -- whole-field get/put ------------------------------------------
    def get_field(self, din, field, values, old):
        t = field.dataio_type

        if t == "memory":
            n = self._count(field, 0, values)
            full = constants.resolve_size(field.sizes[0][0])
            cur = bytearray(old.get(field.name) or (b"\0" * full))
            cur[:n] = din.get_memory(n)
            return bytes(cur)

        whole = (t in _ARR_WHOLE and len(field.sizes) == 1)
        if not field.sizes or whole:
            return self._get_one(din, field)

        if len(field.sizes) == 1:
            cur = list(old.get(field.name) or self.zero(field))
            if field.diff:
                while True:
                    idx = din.get_uint8()
                    if idx == 255:
                        break
                    cur[idx] = self._get_one(din, field)
                return cur
            n = self._count(field, 0, values)
            for i in range(n):
                cur[i] = self._get_one(din, field)
            return cur

        # two dimensions
        n1 = self._count(field, 0, values)
        if t == "string":
            # A 2D string field is a vector of strings, not a grid of chars:
            # the second dimension is just each string's buffer size.
            cur = list(old.get(field.name) or self.zero(field))
            for i in range(n1):
                cur[i] = din.get_string()
            return cur
        cur = [list(row) for row in (old.get(field.name) or self.zero(field))]
        n2 = self._count(field, 1, values)
        for i in range(n1):
            for j in range(n2):
                cur[i][j] = self._get_one(din, field)
        return cur

    def put_field(self, dout, field, values, old):
        t = field.dataio_type
        value = values[field.name]

        if t == "memory":
            n = self._count(field, 0, values)
            dout.put_memory(value, n)
            return

        whole = (t in _ARR_WHOLE and len(field.sizes) == 1)
        if not field.sizes or whole:
            self._put_one(dout, field, value)
            return

        if len(field.sizes) == 1:
            if field.diff:
                prev = old.get(field.name) or self.zero(field)
                n = self._count(field, 0, values)
                for i in range(n):
                    if i >= len(prev) or prev[i] != value[i]:
                        dout.put_uint8(i)
                        self._put_one(dout, field, value[i])
                dout.put_uint8(255)
                return
            n = self._count(field, 0, values)
            for i in range(n):
                self._put_one(dout, field, value[i])
            return

        n1 = self._count(field, 0, values)
        if t == "string":
            for i in range(n1):
                dout.put_string(value[i])
            return
        n2 = self._count(field, 1, values)
        for i in range(n1):
            for j in range(n2):
                self._put_one(dout, field, value[i][j])

    # -- packets -------------------------------------------------------
    def decode(self, ptype, payload):
        packet = self.packets.get(ptype)
        if packet is None:
            raise KeyError("unknown packet type %d" % ptype)
        din = DataIn(payload)
        keys, others = self._split(packet)

        if not packet.delta:
            values = {}
            for f in self._fields_of(packet):
                values[f.name] = self.get_field(din, f, values, {})
            return values

        bits = din.get_bitvector(_bv_bytes(len(others)))
        values = {}
        for f in keys:
            values[f.name] = self.get_field(din, f, values, {})

        cache_key = (ptype, tuple(values[f.name] for f in keys))
        old = self.recv_cache.get(cache_key)
        if old is None:
            old = self.blank(packet)
        merged = dict(old)
        merged.update(values)
        values = merged

        for i, f in enumerate(others):
            isset = bool(bits >> i & 1)
            if _is_folded_bool(f):
                values[f.name] = isset
            elif isset:
                values[f.name] = self.get_field(din, f, values, old)

        self.recv_cache[cache_key] = dict(values)
        for cancelled in packet.cancel:
            num = self._number_of(cancelled)
            self.recv_cache.pop((num, cache_key[1]), None)
        return values

    def encode(self, ptype, values):
        """Return the packet body, or None if delta says nothing to send."""
        packet = self.packets.get(ptype)
        if packet is None:
            raise KeyError("unknown packet type %d" % ptype)
        keys, others = self._split(packet)

        full = self.blank(packet)
        full.update(values)
        values = full
        dout = DataOut()

        if not packet.delta:
            for f in self._fields_of(packet):
                self.put_field(dout, f, values, {})
            return dout.data()

        cache_key = (ptype, tuple(values[f.name] for f in keys))
        old = self.send_cache.get(cache_key)
        if old is None:
            old = self.blank(packet)
            different = 1          # never seen: force a send
        else:
            different = 0

        bits = 0
        for i, f in enumerate(others):
            differs = old.get(f.name) != values[f.name]
            if differs:
                different += 1
            if _is_folded_bool(f):
                if values[f.name]:
                    bits |= 1 << i
            elif differs:
                bits |= 1 << i

        if packet.is_info != "no" and different == 0:
            return None

        dout.put_bitvector(bits, _bv_bytes(len(others)))
        for f in keys:
            self.put_field(dout, f, values, old)
        for i, f in enumerate(others):
            if _is_folded_bool(f):
                continue
            if bits >> i & 1:
                self.put_field(dout, f, values, old)

        self.send_cache[cache_key] = dict(values)
        for cancelled in packet.cancel:
            num = self._number_of(cancelled)
            self.send_cache.pop((num, cache_key[1]), None)
        return dout.data()

    def _number_of(self, name):
        for num, p in self.packets.items():
            if p.name == name:
                return num
        return None
