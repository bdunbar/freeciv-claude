"""Socket connection to a freeciv 3.2 server.

Wire framing (common/packets.c):
  uint16 length (whole packet, header included) | uint8 type | body

  length == 0xffff        -> jumbo compressed chunk, uint32 real length follows
  length >= 16385         -> compressed chunk, real length = length - 16385
Compressed chunks are zlib streams holding several concatenated plain
packets; they are inflated and fed back through the same parser.

The type field is one byte during login and two bytes afterwards: freeciv
widens it once the join is accepted, because 3.2 has packet numbers above
255 (see packet_header_set() in common/networking/packets.c). The narrow
header is fixed for backward compatibility and cannot be negotiated away.
"""

import os
import socket
import zlib

from . import constants, pdef
from .codec import Codec

COMPRESSION_BORDER = 16 * 1024 + 1
JUMBO_SIZE = 0xFFFF

PACKETS_DEF_SEARCH = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec", "packets.def"),
    "/usr/share/games/freeciv/packets.def",
]

# Packet type width before and after a successful join.
LOGIN_TYPE_SIZE = 1
POST_LOGIN_TYPE_SIZE = 2


def _find_packets_def(path=None):
    candidates = [path] if path else []
    candidates += PACKETS_DEF_SEARCH
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise IOError("cannot find packets.def; pass packets_def=...")


class Connection(object):
    def __init__(self, host="localhost", port=5556, packets_def=None):
        self.host = host
        self.port = port
        path = _find_packets_def(packets_def)
        self.by_number, self.by_name = pdef.parse(path)
        self.caps = set()
        self.codec = Codec(self.by_number, self.caps)
        self.sock = None
        self.buf = bytearray()
        self.conn_id = None
        self.server_caps = ""
        self.server_version = None
        self.type_size = LOGIN_TYPE_SIZE

    def name_to_number(self, name):
        return self.by_name[name].number

    # -- lifecycle -----------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=30)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    # -- raw io --------------------------------------------------------
    def send(self, packet_name, **values):
        ptype = self.name_to_number(packet_name)
        body = self.codec.encode(ptype, values)
        if body is None:
            return False        # delta: identical is-info packet, suppressed
        length = len(body) + 2 + self.type_size
        header = (length.to_bytes(2, "big")
                  + ptype.to_bytes(self.type_size, "big"))
        self.sock.sendall(header + body)
        return True

    def _fill(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("server closed the connection")
        self.buf.extend(chunk)

    def _take_packet(self):
        """Pop one packet from the buffer, or None if incomplete."""
        while True:
            if len(self.buf) < 2:
                return None
            len_read = int.from_bytes(self.buf[0:2], "big")

            if len_read == JUMBO_SIZE:
                if len(self.buf) < 6:
                    return None
                whole = int.from_bytes(self.buf[2:6], "big")
                header_size = 6
            elif len_read >= COMPRESSION_BORDER:
                whole = len_read - COMPRESSION_BORDER
                header_size = 2
            else:
                whole = len_read
                header_size = None

            if whole > len(self.buf):
                return None

            if header_size is not None:
                payload = bytes(self.buf[header_size:whole])
                del self.buf[:whole]
                self.buf[:0] = zlib.decompress(payload)
                continue

            head = 2 + self.type_size
            if whole < head:
                raise ValueError("corrupt packet stream (len=%d)" % whole)
            ptype = int.from_bytes(self.buf[2:head], "big")
            body = bytes(self.buf[head:whole])
            del self.buf[:whole]
            return ptype, body

    def receive(self, timeout=None):
        """Return (packet_name, values) for the next packet."""
        while True:
            got = self._take_packet()
            if got is None:
                self._fill(timeout)
                continue
            ptype, body = got
            packet = self.by_number.get(ptype)
            if packet is None:
                # Unknown type: we cannot resync, since length framing is
                # fine but delta caches would drift. Surface it loudly.
                raise KeyError("unknown packet type %d" % ptype)
            values = self.codec.decode(ptype, body)
            return packet.name, values

    # -- handshake -----------------------------------------------------
    def login(self, username):
        self.connect()
        self.send("PACKET_SERVER_JOIN_REQ",
                  username=username,
                  capability=constants.NETWORK_CAPSTRING,
                  version_label="",
                  major_version=constants.MAJOR_VERSION,
                  minor_version=constants.MINOR_VERSION,
                  patch_version=constants.PATCH_VERSION)
        # The server brackets its replies with PROCESSING_STARTED/FINISHED,
        # and 3.2 announces its own version first (connecthand.c sends
        # PACKET_SERVER_INFO as soon as capabilities check out).
        while True:
            name, values = self.receive(timeout=30)
            if name == "PACKET_SERVER_JOIN_REPLY":
                break
            if name == "PACKET_SERVER_INFO":
                self.server_version = (values["major_version"],
                                       values["minor_version"],
                                       values["patch_version"])
                continue
            if name not in ("PACKET_PROCESSING_STARTED",
                            "PACKET_PROCESSING_FINISHED"):
                raise RuntimeError("expected join reply, got %s" % name)
        if not values["you_can_join"]:
            raise RuntimeError("server refused login: %s" % values["message"])

        self.server_caps = values["capability"]
        # Negotiated capabilities are the intersection of both capstrings.
        mine = set(constants.NETWORK_CAPSTRING.replace("+", " ").split())
        theirs = set(self.server_caps.replace("+", " ").split())
        self.caps.clear()
        self.caps.update(mine & theirs)
        # Field layout depends on the negotiated caps, so anything cached
        # from before negotiation is not comparable.
        self.codec.recv_cache.clear()
        self.codec.send_cache.clear()
        self.conn_id = values["conn_id"]
        # The server widens the type field the moment it sends the accepted
        # join reply, so every packet after this one uses the wide header.
        self.type_size = POST_LOGIN_TYPE_SIZE
        return values
