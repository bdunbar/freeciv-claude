"""Parser for freeciv's common/packets.def.

This is the authoritative protocol spec that freeciv itself code-generates
from (common/generate_packets.py). We parse the same file so the wire format
is derived, not guessed.
"""

import re

# Base dataio types that need no further alias resolution.
_FLOAT_RE = re.compile(r"^([su])float(\d+)$")


class Field(object):
    __slots__ = ("name", "dataio_type", "public_type", "float_factor",
                 "sizes", "is_key", "diff", "add_cap", "remove_cap")

    def __init__(self, name, dataio_type, public_type, float_factor, sizes):
        self.name = name
        self.dataio_type = dataio_type
        self.public_type = public_type
        self.float_factor = float_factor
        # list of (full_size_expr, transfer_field_or_None); [] means scalar
        self.sizes = sizes
        self.is_key = False
        self.diff = False
        self.add_cap = None
        self.remove_cap = None

    @property
    def is_array(self):
        return len(self.sizes)

    def __repr__(self):
        return "<Field %s %s%s>" % (self.dataio_type, self.name,
                                    "".join("[%s:%s]" % s for s in self.sizes))


class Packet(object):
    __slots__ = ("name", "number", "flags", "fields", "key_fields",
                 "other_fields", "delta", "is_info", "cancel")

    def __init__(self, name, number, flags):
        self.name = name
        self.number = number
        self.flags = flags
        self.fields = []
        self.cancel = []

    def finish(self):
        # A packet with no fields carries no body at all (generate_packets.py
        # forces no_packet/delta=0 in that case).
        self.delta = "no-delta" not in self.flags and len(self.fields) > 0
        if "is-game-info" in self.flags:
            self.is_info = "game"
        elif "is-info" in self.flags:
            self.is_info = "yes"
        else:
            self.is_info = "no"
        self.key_fields = [f for f in self.fields if f.is_key]
        self.other_fields = [f for f in self.fields if not f.is_key]

    def __repr__(self):
        return "<Packet %s=%d %d fields>" % (self.name, self.number,
                                             len(self.fields))


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for line in text.split("\n"):
        line = re.sub(r"//.*$", "", line)
        line = re.sub(r"#.*$", "", line)
        out.append(line)
    return "\n".join(out)


def _resolve_type(spec, typedefs):
    """Resolve a type name to (dataio_type, public_type, float_factor)."""
    seen = set()
    while spec in typedefs:
        if spec in seen:
            raise ValueError("circular typedef: %s" % spec)
        seen.add(spec)
        spec = typedefs[spec]
    m = re.match(r"^([a-z_0-9]+)\((.*)\)$", spec)
    if not m:
        raise ValueError("unparsable type: %r" % spec)
    dataio_type, public_type = m.group(1), m.group(2)
    float_factor = None
    fm = _FLOAT_RE.match(dataio_type)
    if fm:
        float_factor = int(fm.group(2))
        dataio_type = fm.group(1) + "float"
    return dataio_type, public_type, float_factor


def parse(path):
    """Parse packets.def, returning (packets_by_number, packets_by_name)."""
    with open(path) as fp:
        text = _strip_comments(fp.read())

    typedefs = {}
    packets = []
    current = None

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        if line.startswith("type "):
            name, _, rhs = line[5:].partition("=")
            typedefs[name.strip()] = rhs.strip()
            continue

        if current is None:
            m = re.match(r"^(PACKET_[A-Z0-9_]+)\s*=\s*(\d+)\s*;?\s*(.*)$", line)
            if not m:
                continue
            flagtext = m.group(3)
            flags = [f.strip() for f in flagtext.split(",") if f.strip()]
            current = Packet(m.group(1), int(m.group(2)), flags)
            for f in flags:
                cm = re.match(r"^cancel\((PACKET_[A-Z0-9_]+)\)$", f)
                if cm:
                    current.cancel.append(cm.group(1))
            continue

        if line == "end":
            current.finish()
            packets.append(current)
            current = None
            continue

        _parse_field_line(line, current, typedefs)

    by_number = {}
    by_name = {}
    for p in packets:
        if p.number in by_number:
            raise ValueError("duplicate packet number %d" % p.number)
        by_number[p.number] = p
        by_name[p.name] = p
    return by_number, by_name


def _parse_field_line(line, packet, typedefs):
    decl, _, flagtext = line.partition(";")
    decl = decl.strip()
    if not decl:
        return
    flags = [f.strip() for f in flagtext.split(",") if f.strip()]

    typename, _, names = decl.partition(" ")
    dataio_type, public_type, float_factor = _resolve_type(typename.strip(),
                                                           typedefs)

    for entry in names.split(","):
        entry = entry.strip()
        if not entry:
            continue
        sizes = []
        base = entry
        for m in re.finditer(r"\[([^\]]*)\]", entry):
            inner = m.group(1)
            if ":" in inner:
                full, _, transfer = inner.partition(":")
                sizes.append((full.strip(), transfer.strip()))
            else:
                sizes.append((inner.strip(), None))
        base = re.sub(r"\[.*$", "", entry).strip()

        field = Field(base, dataio_type, public_type, float_factor, sizes)
        for f in flags:
            if f == "key":
                field.is_key = True
            elif f == "diff":
                field.diff = True
            else:
                m = re.match(r"^add-cap\((.*)\)$", f)
                if m:
                    field.add_cap = m.group(1)
                m = re.match(r"^remove-cap\((.*)\)$", f)
                if m:
                    field.remove_cap = m.group(1)
        packet.fields.append(field)
