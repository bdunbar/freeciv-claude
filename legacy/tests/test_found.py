import secfile, fcmap, fcorders
from fcrun import Runner
W='/tmp/claude-1000/-home-bdunbar-Documents/e71ab1cc-c3af-44b4-83d0-894707821011/scratchpad/agentwork4'
r=Runner(W)
s=r.new_game(players=3,size=1,topology='WRAPX',seed=7)
sf=secfile.SecFile(s); me=sf.get('player0','name')
ut=sf.table('player0','u')
idx=next(i for i,u in enumerate(ut.rows) if u['type_by_name']=='Settlers')
u=ut.rows[idx]; uid=u['id']
print('Settlers %d at (%d,%d); cities before: %d' % (uid,u['x'],u['y'],len(sf.table('player0','c') or [])))
fcorders.set_orders(sf,ut,idx,[fcorders.found_city()])
s=r.step(sf.write(s.replace('.sav','_F.sav')),turns=1,human_player=me)
sf=secfile.SecFile(s)
ct=sf.table('player0','c')
print('cities after: %d' % len(ct))
for c in ct.rows:
    print('  %s at (%d,%d) size=%d building=%s' % (c['name'],c['x'],c['y'],c['size'],c['currently_building_name']))
# Now change production and verify it sticks through a turn.
if len(ct):
    sf.update_row(ct,0,currently_building_kind='UnitType',currently_building_name='Warriors',
                  changed_from_kind='UnitType',changed_from_name='Warriors')
    s=r.step(sf.write(s.replace('.sav','_P.sav')),turns=1,human_player=me)
    sf=secfile.SecFile(s); ct=sf.table('player0','c')
    print('after production change -> %s (shield_stock=%s)'
          % (ct.rows[0]['currently_building_name'], ct.rows[0]['shield_stock']))
