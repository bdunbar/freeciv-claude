"""Byte-level encoders/decoders mirroring freeciv's
common/networking/dataio_raw.c (3.2).

All multi-byte integers are network byte order (big endian).
"""

import struct

from . import constants


class DataIn(object):
    def __init__(self, buf, pos=0):
        self.buf = buf
        self.pos = pos

    def remaining(self):
        return len(self.buf) - self.pos

    def take(self, n):
        if self.pos + n > len(self.buf):
            raise EOFError("short read: want %d, have %d" % (n, self.remaining()))
        chunk = self.buf[self.pos:self.pos + n]
        self.pos += n
        return chunk

    # -- primitives ---------------------------------------------------
    def get_uint8(self):
        return self.take(1)[0]

    def get_uint16(self):
        return struct.unpack(">H", self.take(2))[0]

    def get_uint32(self):
        return struct.unpack(">I", self.take(4))[0]

    def get_sint8(self):
        return struct.unpack(">b", self.take(1))[0]

    def get_sint16(self):
        return struct.unpack(">h", self.take(2))[0]

    def get_sint32(self):
        return struct.unpack(">i", self.take(4))[0]

    def get_bool8(self):
        return self.get_uint8() != 0

    def get_bool32(self):
        return self.get_uint32() != 0

    def get_ufloat(self, factor):
        return self.get_uint32() / float(factor)

    def get_sfloat(self, factor):
        return self.get_sint32() / float(factor)

    def get_memory(self, size):
        return self.take(size)

    def get_string(self):
        end = self.buf.find(b"\0", self.pos)
        if end < 0:
            raise EOFError("unterminated string")
        raw = self.buf[self.pos:end]
        self.pos = end + 1
        return raw.decode("utf-8", "replace")

    def get_bitvector(self, nbytes):
        return int.from_bytes(self.take(nbytes), "little")

    def get_worklist(self):
        length = self.get_uint8()
        return [(self.get_uint8(), self.get_uint8()) for _ in range(length)]

    # estring is the escaped-string variant; on the raw wire it is a string
    # (dataio_raw.h: #define dio_get_estring_raw dio_get_string_raw).
    get_estring = get_string

    def get_cm_parameter(self):
        o = constants.CONSTANTS["O_LAST"]
        return {
            "minimal_surplus": [self.get_sint16() for _ in range(o)],
            "max_growth": self.get_bool8(),
            "require_happy": self.get_bool8(),
            "allow_disorder": self.get_bool8(),
            "allow_specialists": self.get_bool8(),
            "factor": [self.get_uint16() for _ in range(o)],
            "happy_factor": self.get_uint16(),
        }

    def get_unit_order(self):
        return {
            "order": self.get_uint8(),
            "activity": self.get_uint8(),
            "target": self.get_sint32(),
            "sub_target": self.get_sint16(),
            "action": self.get_uint8(),
            "dir": self.get_sint8(),
        }

    def get_requirement(self):
        return {
            "type": self.get_uint8(),
            "value": self.get_sint32(),
            "range": self.get_uint8(),
            "survives": self.get_bool8(),
            "present": self.get_bool8(),
            "quiet": self.get_bool8(),
        }

    def get_action_probability(self):
        return (self.get_uint8(), self.get_uint8())


class DataOut(object):
    def __init__(self):
        self.parts = []

    def data(self):
        return b"".join(self.parts)

    def put_uint8(self, v):
        self.parts.append(struct.pack(">B", v & 0xFF))

    def put_uint16(self, v):
        self.parts.append(struct.pack(">H", v & 0xFFFF))

    def put_uint32(self, v):
        self.parts.append(struct.pack(">I", v & 0xFFFFFFFF))

    def put_sint8(self, v):
        self.put_uint8(v if v >= 0 else v + 0x100)

    def put_sint16(self, v):
        self.put_uint16(v if v >= 0 else v + 0x10000)

    def put_sint32(self, v):
        self.put_uint32(v if v >= 0 else v + 0x100000000)

    def put_bool8(self, v):
        self.put_uint8(1 if v else 0)

    def put_bool32(self, v):
        self.put_uint32(1 if v else 0)

    def put_ufloat(self, v, factor):
        self.put_uint32(int(v * factor))

    def put_sfloat(self, v, factor):
        self.put_sint32(int(v * factor))

    def put_memory(self, data, size):
        data = bytes(data)
        if len(data) < size:
            data = data + b"\0" * (size - len(data))
        self.parts.append(data[:size])

    def put_string(self, s):
        if isinstance(s, str):
            s = s.encode("utf-8")
        self.parts.append(s + b"\0")

    def put_bitvector(self, value, nbytes):
        self.parts.append(int(value).to_bytes(nbytes, "little"))

    def put_worklist(self, entries):
        entries = entries or []
        self.put_uint8(len(entries))
        for kind, number in entries:
            self.put_uint8(kind)
            self.put_uint8(number)

    put_estring = put_string

    def put_cm_parameter(self, p):
        o = constants.CONSTANTS["O_LAST"]
        for i in range(o):
            self.put_sint16(p["minimal_surplus"][i])
        self.put_bool8(p["max_growth"])
        self.put_bool8(p["require_happy"])
        self.put_bool8(p["allow_disorder"])
        self.put_bool8(p["allow_specialists"])
        for i in range(o):
            self.put_uint16(p["factor"][i])
        self.put_uint16(p["happy_factor"])

    def put_unit_order(self, o):
        self.put_uint8(o["order"])
        self.put_uint8(o["activity"])
        self.put_sint32(o["target"])
        self.put_sint16(o["sub_target"])
        self.put_uint8(o["action"])
        self.put_sint8(o["dir"])

    def put_requirement(self, r):
        self.put_uint8(r["type"])
        self.put_sint32(r["value"])
        self.put_uint8(r["range"])
        self.put_bool8(r["survives"])
        self.put_bool8(r["present"])
        self.put_bool8(r["quiet"])

    def put_action_probability(self, ap):
        self.put_uint8(ap[0])
        self.put_uint8(ap[1])
