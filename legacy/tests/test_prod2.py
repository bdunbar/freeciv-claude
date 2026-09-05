import secfile
from fcrun import Runner
W='/tmp/claude-1000/-home-bdunbar-Documents/e71ab1cc-c3af-44b4-83d0-894707821011/scratchpad/agentwork5'
r=Runner(W)
s=r.new_game(players=3,size=1,topology='WRAPX',seed=7)
sf=secfile.SecFile(s); me=sf.get('player0','name')
import fcorders
ut=sf.table('player0','u')
idx=next(i for i,u in enumerate(ut.rows) if u['type_by_name']=='Settlers')
fcorders.set_orders(sf,ut,idx,[fcorders.found_city()])
s=r.step(sf.write(s.replace('.sav','_x.sav')),turns=1,human_player=me)
want=['Warriors','Warriors','Workers','Workers']
for i,w in enumerate(want):
    sf=secfile.SecFile(s); ct=sf.table('player0','c')
    sf.update_row(ct,0,currently_building_kind='UnitType',currently_building_name=w)
    s=r.step(sf.write(s.replace('.sav','_p%d.sav'%i)),turns=1,human_player=me)
    sf2=secfile.SecFile(s); c=sf2.table('player0','c').rows[0]
    print('turn %s: asked=%s got=%s shields=%s ai.control=%s'
          % (sf2.get('game','turn'),w,c['currently_building_name'],c['shield_stock'],sf2.get('player0','ai.control')))
