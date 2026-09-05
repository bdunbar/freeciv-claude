import secfile, fcmap, fcorders
from fcrun import Runner
W='/tmp/claude-1000/-home-bdunbar-Documents/e71ab1cc-c3af-44b4-83d0-894707821011/scratchpad/agentwork2'
r=Runner(W)
s0=r.new_game(players=3,size=1,topology='WRAPX',seed=42)
sf=secfile.SecFile(s0); me=sf.get('player0','name')
m=fcmap.Map(sf); ut=sf.table('player0','u')
idx=next(i for i,u in enumerate(ut.rows) if u['type_by_name']=='Workers')
u=ut.rows[idx]; start=(u['x'],u['y'])

# Find a direction whose first TWO steps are both land.
good=None
for d in range(8):
    a=m.neighbour(*start,d)
    if not a: continue
    b=m.neighbour(*a,d)
    if not b: continue
    if m.terrain_at(*a) not in ('Ocean','Deep Ocean','Lake','Inaccessible') and \
       m.terrain_at(*b) not in ('Ocean','Deep Ocean','Lake','Inaccessible'):
        good=(d,a,b); break
print('two-step land route:', fcmap.DIR_NAMES[good[0]], start,'->',good[1],'->',good[2])

# A: with orders
sfA=secfile.SecFile(s0); utA=sfA.table('player0','u')
fcorders.set_orders(sfA,utA,idx,[fcorders.move(good[0]),fcorders.move(good[0])])
sA=r.step(sfA.write(s0.replace('.sav','_A.sav')),turns=1,human_player=me)
rA=next(x for x in secfile.SecFile(sA).table('player0','u').rows if x['id']==u['id'])

# B: control -- identical save, no orders at all
sfB=secfile.SecFile(s0)
sB=r.step(sfB.write(s0.replace('.sav','_B.sav')),turns=1,human_player=me)
sfB2=secfile.SecFile(sB)
rB=next(x for x in sfB2.table('player0','u').rows if x['id']==u['id'])

print('A (ordered) : unit at (%d,%d) orders_index=%s' % (rA['x'],rA['y'],rA['orders_index']))
print('B (control) : unit at (%d,%d)' % (rB['x'],rB['y']))
print('ai.control after aitoggle:', sfB2.get('player0','ai.control'))
print('ordered unit reached commanded tile:', (rA['x'],rA['y'])==good[2])
print('control unit stayed put        :', (rB['x'],rB['y'])==start)
