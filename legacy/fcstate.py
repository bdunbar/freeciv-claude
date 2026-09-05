"""Build a fog-respecting, player-scoped view of a savegame.

The agent must never see more than its player legitimately knows. Terrain comes
from that player's own `map_tNNNN` knowledge rows, not from ground truth, and
foreign units/cities are reported only when they stand within vision range of
one of our own units or cities.
"""

import fcmap
import rules

VISION_RADIUS = 2          # approximation of unit/city sight range
UNKNOWN = '?'


def _bitstring_names(bits, names):
    if not bits:
        return []
    return [n for i, n in enumerate(names) if i < len(bits) and bits[i] == '1']


def _vector(sf, key):
    raw = sf.sections.get('savefile', {}).get(key)
    if not raw:
        return []
    import secfile
    return [secfile.unquote(v) for v in secfile.split_values(raw[1])]


class PlayerView:
    def __init__(self, sf, section='player0'):
        self.sf = sf
        self.section = section
        self.map = fcmap.Map(sf)
        self.turn = sf.get('game', 'turn', 0)
        self.name = sf.get(section, 'name')
        self.nation = sf.get(section, 'nation')
        self.government = sf.get(section, 'government_name')
        self.gold = sf.get(section, 'gold', 0)
        self.tax = sf.get(section, 'rates.tax', 0)
        self.science = sf.get(section, 'rates.science', 0)
        self.luxury = sf.get(section, 'rates.luxury', 0)
        self.alive = sf.get(section, 'is_alive', True)
        self.improvement_names = _vector(sf, 'improvement_vector')
        self.tech_names = _vector(sf, 'technology_vector')
        self.known = self._known_rows()
        self.res_rows = self._prefixed_rows('map_res')
        self.resource_idents = rules.resource_identifiers()
        self.cities = self._cities()
        self.units = self._units()
        self.research = self._research()

    def extra_index(self, name):
        """Index into the savegame's extras_vector, as used by tgt_list."""
        if not hasattr(self, '_extras'):
            self._extras = _vector(self.sf, 'extras_vector')
        try:
            return self._extras.index(name)
        except ValueError:
            raise ValueError('unknown extra %r; known: %s' % (name, self._extras))

    # ---- our own assets ----

    def _known_rows(self):
        return self._prefixed_rows('map_t')

    def _prefixed_rows(self, prefix):
        rows, i = [], 0
        while True:
            v = self.sf.get(self.section, '%s%04d' % (prefix, i))
            if v is None:
                break
            rows.append(v)
            i += 1
        return rows

    def known_resource(self, nx, ny):
        """Resource this player has seen on a tile, or None."""
        if 0 <= ny < len(self.res_rows) and 0 <= nx < len(self.res_rows[ny]):
            return self.resource_idents.get(self.res_rows[ny][nx])
        return None

    def known_terrain(self, nx, ny):
        if 0 <= ny < len(self.known) and 0 <= nx < len(self.known[ny]):
            ch = self.known[ny][nx]
            return self.map.ident2terrain.get(ch, UNKNOWN)
        return UNKNOWN

    def _cities(self):
        t = self.sf.table(self.section, 'c')
        out = []
        if not t:
            return out
        for i, c in enumerate(t.rows):
            out.append({
                'row': i, 'id': c.get('id'), 'name': c.get('name'),
                'pos': (c.get('x'), c.get('y')), 'size': c.get('size'),
                'food_stock': c.get('food_stock'),
                'shield_stock': c.get('shield_stock'),
                'building_kind': c.get('currently_building_kind'),
                'building': c.get('currently_building_name'),
                'turn_founded': c.get('turn_founded'),
                'last_shield_surplus': c.get('last_turns_shield_surplus'),
                'improvements': _bitstring_names(c.get('improvements') or '',
                                                 self.improvement_names),
            })
        return out

    def _units(self):
        t = self.sf.table(self.section, 'u')
        out = []
        if not t:
            return out
        for i, u in enumerate(t.rows):
            out.append({
                'row': i, 'id': u.get('id'), 'type': u.get('type_by_name'),
                'pos': (u.get('x'), u.get('y')), 'hp': u.get('hp'),
                'veteran': u.get('veteran'), 'moves': u.get('moves'),
                'activity': u.get('activity'),
                'homecity': u.get('homecity'),
                'has_orders': (u.get('orders_length') or 0) > 0,
                'orders_left': (u.get('orders_length') or 0) - (u.get('orders_index') or 0),
            })
        return out

    def _research(self):
        t = self.sf.table('research', 'r')
        if not t:
            return {}
        num = self.sf.get(self.section, 'team_no', 0)
        for r in t.rows:
            if r.get('number') == num:
                return {
                    'researching': r.get('now_name'),
                    'goal': r.get('goal_name'),
                    'bulbs': r.get('bulbs'),
                    'techs_known': _bitstring_names(r.get('done') or '', self.tech_names),
                }
        return {}

    # ---- fog-limited foreign intel ----

    def _visible_tiles(self):
        seen = set()
        anchors = [u['pos'] for u in self.units] + [c['pos'] for c in self.cities]
        for pos in anchors:
            if pos[0] is None:
                continue
            mx, my = self.map.native_to_map(*pos)
            for dx in range(-VISION_RADIUS, VISION_RADIUS + 1):
                for dy in range(-VISION_RADIUS, VISION_RADIUS + 1):
                    n = self.map.map_to_native(mx + dx, my + dy)
                    n = self.map.normalize(*n)
                    if n:
                        seen.add(n)
        return seen

    def foreign_sightings(self):
        """Foreign units and cities standing on tiles we can currently see."""
        vis = self._visible_tiles()
        out = {'units': [], 'cities': []}
        for sec in self.sf.player_sections():
            if sec == self.section:
                continue
            owner = self.sf.get(sec, 'name')
            nation = self.sf.get(sec, 'nation')
            ut = self.sf.table(sec, 'u')
            if ut:
                for u in ut.rows:
                    if (u.get('x'), u.get('y')) in vis:
                        out['units'].append({
                            'owner': owner, 'nation': nation,
                            'type': u.get('type_by_name'),
                            'pos': (u.get('x'), u.get('y')), 'hp': u.get('hp')})
            ct = self.sf.table(sec, 'c')
            if ct:
                for c in ct.rows:
                    if (c.get('x'), c.get('y')) in vis:
                        out['cities'].append({
                            'owner': owner, 'nation': nation,
                            'name': c.get('name'), 'size': c.get('size'),
                            'pos': (c.get('x'), c.get('y'))})
        return out

    # ---- rendering ----

    def local_map(self, centre, radius=4):
        """ASCII patch of *known* terrain around a native position."""
        cx, cy = self.map.native_to_map(*centre)
        occupied = {u['pos']: u['type'][0] for u in self.units}
        for c in self.cities:
            occupied[c['pos']] = '#'
        lines = []
        for dy in range(-radius, radius + 1):
            row = []
            for dx in range(-radius, radius + 1):
                n = self.map.normalize(*self.map.map_to_native(cx + dx, cy + dy))
                if n is None:
                    row.append(' ')
                    continue
                if n in occupied:
                    row.append(occupied[n])
                    continue
                terr = self.known_terrain(*n)
                row.append('.' if terr == UNKNOWN else terr[0].lower())
            lines.append(''.join(row))
        return '\n'.join(lines)

    def summary(self):
        r = self.research
        out = ['=== Turn %s | %s of the %s ===' % (self.turn, self.name, self.nation),
               'gov=%s gold=%s rates(tax/sci/lux)=%s/%s/%s' %
               (self.government, self.gold, self.tax, self.science, self.luxury),
               'researching=%s (goal %s, %s bulbs, %d techs known)' %
               (r.get('researching'), r.get('goal'), r.get('bulbs'),
                len(r.get('techs_known', [])))]
        out.append('cities (%d):' % len(self.cities))
        for c in self.cities:
            out.append('  [%s] %s at %s size=%s food=%s shields=%s building=%s%s'
                       % (c['id'], c['name'], c['pos'], c['size'], c['food_stock'],
                          c['shield_stock'], c['building'],
                          ' improvements=%s' % ','.join(c['improvements'])
                          if c['improvements'] else ''))
        out.append('units (%d):' % len(self.units))
        for u in self.units:
            out.append('  [%s] %s at %s hp=%s moves=%s%s'
                       % (u['id'], u['type'], u['pos'], u['hp'], u['moves'],
                          ' orders_left=%d' % u['orders_left'] if u['has_orders'] else ''))
        f = self.foreign_sightings()
        if f['units'] or f['cities']:
            out.append('in sight:')
            for c in f['cities']:
                out.append('  city %s (%s) at %s size=%s' % (c['name'], c['nation'], c['pos'], c['size']))
            for u in f['units']:
                out.append('  unit %s (%s) at %s' % (u['type'], u['nation'], u['pos']))
        return '\n'.join(out)


TERRAIN_GLYPH = {
    'Ocean': '~', 'Deep Ocean': '~', 'Lake': '~', 'Inaccessible': '#',
    'Glacier': 'A', 'Desert': 'd', 'Forest': 'f', 'Grassland': 'g',
    'Hills': 'h', 'Jungle': 'j', 'Mountains': '^', 'Plains': 'p',
    'Swamp': 's', 'Tundra': 't',
}


def board(view, centre=None, radius=7):
    """Labelled map of what this player knows, plus the legend that reads it."""
    m = view.map
    if centre is None:
        pts = [u['pos'] for u in view.units] + [c['pos'] for c in view.cities]
        pts = [p for p in pts if p[0] is not None]
        centre = (sum(p[0] for p in pts) // len(pts),
                  sum(p[1] for p in pts) // len(pts)) if pts else (0, 0)

    marks = {}
    for u in view.units:
        marks.setdefault(u['pos'], u['type'][0].upper())
    for c in view.cities:
        marks[c['pos']] = '@'
    foreign = view.foreign_sightings()
    for u in foreign['units']:
        marks.setdefault(tuple(u['pos']), '!')
    for c in foreign['cities']:
        marks[tuple(c['pos'])] = '%'

    cx, cy = centre
    xs = [cx + dx for dx in range(-radius, radius + 1)]
    ys = [cy + dy for dy in range(-radius, radius + 1)]

    lines = []
    lines.append('      ' + ''.join(str((x // 10) % 10) for x in xs))
    lines.append('      ' + ''.join(str(x % 10) for x in xs))
    resources = []
    for y in ys:
        row = []
        for x in xs:
            n = m.normalize(x, y)
            if n is None:
                row.append(' ')
                continue
            if n in marks:
                row.append(marks[n])
                continue
            terr = view.known_terrain(*n)
            if terr == UNKNOWN:
                row.append('.')
                continue
            row.append(TERRAIN_GLYPH.get(terr, terr[0].lower()))
        lines.append('  %3d %s' % (y, ''.join(row)))
        for x in xs:
            n = m.normalize(x, y)
            if n and view.known_terrain(*n) != UNKNOWN:
                r = view.known_resource(*n)
                if r:
                    resources.append('%s at %s (%s)' % (r, n, view.known_terrain(*n)))

    lines.append('')
    lines.append('  terrain  g grass  p plains  h hills  ^ mountains  f forest'
                 '  j jungle  s swamp')
    lines.append('           d desert  t tundra  A glacier  ~ water  . unexplored')
    lines.append('  ours     @ city   S settlers  W workers  E explorer  L legion')
    lines.append('  theirs   % city   ! unit')
    if resources:
        lines.append('')
        lines.append('  known resources: ' + '; '.join(resources))
    return '\n'.join(lines)
