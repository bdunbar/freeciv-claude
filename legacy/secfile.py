"""
Reader/surgical-writer for Freeciv 2.6 savegame files ("secfile" registry format).

Design note: we parse fully for reading, but write *surgically* -- only the
lines we actually change get re-serialized. A full round-trip re-emit would
risk corrupting fields we don't understand, and the server is unforgiving.
"""

import bz2
import gzip
import re


def _read_bytes(path):
    with open(path, 'rb') as f:
        magic = f.read(3)
    if magic[:3] == b'BZh':
        return bz2.open(path, 'rt', encoding='utf-8', errors='replace').read()
    if magic[:2] == b'\x1f\x8b':
        return gzip.open(path, 'rt', encoding='utf-8', errors='replace').read()
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def split_values(line):
    """Split a comma-separated value list, respecting quotes and escapes."""
    out, buf, in_str, esc = [], [], False, False
    for ch in line:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == '\\':
            buf.append(ch)
            esc = True
        elif ch == '"':
            in_str = not in_str
            buf.append(ch)
        elif ch == ',' and not in_str:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return out


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
    return v


def quote(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def to_py(v):
    """Convert a raw savegame token to a Python value."""
    v = v.strip()
    if v == 'TRUE':
        return True
    if v == 'FALSE':
        return False
    if v.startswith('"'):
        return unquote(v)
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def from_py(v):
    """Serialize a Python value back to a savegame token."""
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, str):
        return quote(v)
    return str(v)


class Table:
    """A savegame table: `key={"col","col"  <newline> row <newline> ... }`."""

    def __init__(self, section, key, cols, rows, row_lines, header_line, end_line):
        self.section = section
        self.key = key
        self.cols = cols
        self.rows = rows              # list of dicts (col -> python value)
        self.row_lines = row_lines    # parallel list of line indices in the file
        self.header_line = header_line
        self.end_line = end_line

    def __len__(self):
        return len(self.rows)

    def serialize_row(self, row):
        return ','.join(from_py(row[c]) for c in self.cols)


class SecFile:
    def __init__(self, path):
        self.path = path
        self.lines = _read_bytes(path).split('\n')
        self.sections = {}   # name -> {key: (line_idx, raw_value)}
        self.tables = {}     # (section, key) -> Table
        self._parse()

    # ---------- parsing ----------

    def _parse(self):
        section = None
        i = 0
        n = len(self.lines)
        while i < n:
            line = self.lines[i]
            s = line.strip()
            if not s or s.startswith('#') or s.startswith(';'):
                i += 1
                continue
            if s.startswith('[') and s.endswith(']'):
                section = s[1:-1]
                self.sections.setdefault(section, {})
                i += 1
                continue
            m = re.match(r'^([A-Za-z0-9_.\-]+)\s*=\s*(.*)$', s)
            if m and section is not None:
                key, val = m.group(1), m.group(2)
                if val.startswith('{'):
                    i = self._parse_table(section, key, val[1:], i)
                    continue
                self.sections[section][key] = (i, val)
            i += 1

    def _parse_table(self, section, key, header_rest, header_idx):
        cols = [unquote(c) for c in split_values(header_rest)]
        rows, row_lines = [], []
        i = header_idx + 1
        while i < len(self.lines):
            s = self.lines[i].strip()
            if s == '}':
                break
            if s:
                vals = [to_py(v) for v in split_values(s)]
                # Rows are allowed to be short; pad so lookups never KeyError.
                if len(vals) < len(cols):
                    vals += [None] * (len(cols) - len(vals))
                rows.append(dict(zip(cols, vals[:len(cols)])))
                row_lines.append(i)
            i += 1
        self.tables[(section, key)] = Table(
            section, key, cols, rows, row_lines, header_idx, i)
        return i + 1

    # ---------- reading ----------

    def get(self, section, key, default=None):
        ent = self.sections.get(section, {}).get(key)
        if ent is None:
            return default
        return to_py(ent[1])

    def get_str(self, section, key, default=None):
        v = self.get(section, key, default)
        return v if v is not None else default

    def table(self, section, key):
        return self.tables.get((section, key))

    def has_section(self, name):
        return name in self.sections

    def player_sections(self):
        out = []
        for name in self.sections:
            m = re.match(r'^player(\d+)$', name)
            if m:
                out.append((int(m.group(1)), name))
        return [n for _, n in sorted(out)]

    # ---------- surgical writing ----------

    def set(self, section, key, value):
        """Replace a scalar entry in place. The key must already exist."""
        ent = self.sections.get(section, {}).get(key)
        if ent is None:
            raise KeyError('%s.%s not present in %s' % (section, key, self.path))
        idx = ent[0]
        raw = from_py(value)
        indent = re.match(r'^(\s*)', self.lines[idx]).group(1)
        self.lines[idx] = '%s%s=%s' % (indent, key, raw)
        self.sections[section][key] = (idx, raw)

    def update_row(self, table, row_index, **changes):
        """Apply changes to one table row and rewrite exactly that line."""
        row = table.rows[row_index]
        for k, v in changes.items():
            if k not in table.cols:
                raise KeyError('column %r not in table %s.%s'
                               % (k, table.section, table.key))
            row[k] = v
        self.lines[table.row_lines[row_index]] = table.serialize_row(row)

    def write(self, path):
        data = '\n'.join(self.lines)
        if path.endswith('.bz2'):
            with bz2.open(path, 'wt', encoding='utf-8') as f:
                f.write(data)
        elif path.endswith('.gz'):
            with gzip.open(path, 'wt', encoding='utf-8') as f:
                f.write(data)
        else:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(data)
        return path
