import secfile, fcmap, fcorders
from fcrun import Runner
W='/tmp/claude-1000/-home-bdunbar-Documents/e71ab1cc-c3af-44b4-83d0-894707821011/scratchpad/agentwork3'
r=Runner(W)
s=r.new_game(players=3,size=1,topology='WRAPX',seed=42)
sf=secfile.SecFile(s); me=sf.get('player0','name'); m=fcmap.Map(sf)
ut=sf.table('player0','u')
idx=next(i for i,u in enumerate(ut.rows) if u['type_by_name']=='Workers')
uid=ut.rows[idx]['id']; start=(ut.rows[idx]['x'],ut.rows[idx]['y'])
W_=fcmap.DIR_BY_NAME['W']
path=[W_,W_,W_]
tgt=start
for d in path: tgt=m.neighbour(*tgt,d)
print('unit %d: %s --W,W,W--> %s' % (uid,start,tgt))
fcorders.set_orders(sf,ut,idx,[fcorders.move(d) for d in path])
s=sf.write(s.replace('.sav','_M.sav'))
for t in range(4):
    s=r.step(s,turns=1,human_player=me)
    sf2=secfile.SecFile(s)
    row=next(x for x in sf2.table('player0','u').rows if x['id']==uid)
    print('  turn %s: pos=(%d,%d) orders_index=%s orders_len=%s'
          % (sf2.get('game','turn'),row['x'],row['y'],row['orders_index'],row['orders_length']))
    if (row['x'],row['y'])==tgt:
        print('  ARRIVED at commanded destination'); break
