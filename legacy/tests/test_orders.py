import sys, secfile, fcmap, fcorders
from fcrun import Runner

W = '/tmp/claude-1000/-home-bdunbar-Documents/e71ab1cc-c3af-44b4-83d0-894707821011/scratchpad/agentwork'
r = Runner(W, verbose=False)

print('creating game...')
s0 = r.new_game(players=3, size=1, topology='WRAPX', seed=42)
sf = secfile.SecFile(s0)
print('  save:', s0.split('/')[-1], 'turn', sf.get('game','turn'))
me = sf.get('player0','name')
print('  our player:', me, 'ai.control =', sf.get('player0','ai.control'))

m = fcmap.Map(sf)
print('  map %dx%d topo=%s iso=%s' % (m.xsize, m.ysize, m.topology, m.is_iso))

ut = sf.table('player0','u')
print('  units:', [(u['id'], u['type_by_name'], u['x'], u['y'], u['moves']) for u in ut.rows])

# Pick a unit with movement and order it two steps EAST.
idx = next(i for i,u in enumerate(ut.rows) if u['moves'] > 0)
u = ut.rows[idx]
start = (u['x'], u['y'])
E = fcmap.DIR_BY_NAME['E']
expected = m.neighbour(*start, E)
expected2 = m.neighbour(*expected, E) if expected else None
print('  ordering unit %d (%s) at %s: E, E  -> expect %s then %s'
      % (u['id'], u['type_by_name'], start, expected, expected2))
print('  terrain at targets:', m.terrain_at(*expected), m.terrain_at(*expected2))

fcorders.set_orders(sf, ut, idx, [fcorders.move(E), fcorders.move(E)])
print('  encoded row:', ut.serialize_row(ut.rows[idx])[:110])
s1 = sf.write(s0.replace('.sav','_ORD.sav'))

print('stepping 1 turn...')
s2 = r.step(s1, turns=1, human_player=me)
sf2 = secfile.SecFile(s2)
print('  save:', s2.split('/')[-1], 'turn', sf2.get('game','turn'))
ut2 = sf2.table('player0','u')
row = next((x for x in ut2.rows if x['id']==u['id']), None)
if row is None:
    print('  RESULT: unit gone')
else:
    print('  RESULT: unit %d now at (%d,%d), orders_length=%s, orders_index=%s'
          % (row['id'], row['x'], row['y'], row['orders_length'], row['orders_index']))
    print('  MATCH expected-after-1-step:', (row['x'],row['y'])==expected)
    print('  MATCH expected-after-2-steps:', (row['x'],row['y'])==expected2)
