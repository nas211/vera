"""Bounded AI exit review; the existing risk engine always runs independently."""
import time
from ..agents import bars_for, features
from ..db import get, put
from ..prices import quote
from ..reuse import model_module


def review_position(db,model):
    """Run at most one real model request, outside the execution event loop."""
    now=time.time()
    if not model.ready or now-get(db,'ai-last-call',0)<5:return False
    for row in db.execute('SELECT * FROM positions ORDER BY opened').fetchall():
        p=dict(row)
        # Only positions opened by AI opt into model management; manual positions
        # retain the same stop/target/liquidation/timeout engine.
        if not get(db,'paper-position:'+p['id'],{}).get('agent'):continue
        if now-p['opened']<30 or now-get(db,'paper-exit-reviewed:'+p['id'],0)<120:continue
        bars=bars_for(db,p['symbol'],now)
        if not bars:continue
        try:price=quote(db,p['symbol'],15)
        except ValueError:continue
        calls=[t for t in get(db,'ai-calls',[]) if now-t<86400]
        if len(calls)>=200:return False
        put(db,'ai-calls',calls+[now]);put(db,'ai-last-call',now)
        put(db,'paper-exit-reviewed:'+p['id'],now)
        try:
            answer=model.ask('direction',dict(purpose='Review an existing paper position. Only hold or exit is permitted; never increase or flip.',
                markets={p['symbol']:features(bars)},position={k:p[k] for k in ('symbol','side','qty','entry','stop','target','opened')},mark=str(price)),model_module.DIRECTION)
            current=db.execute('SELECT * FROM positions WHERE id=?',(p['id'],)).fetchone()
            if not current or any(current[k]!=p[k] for k in ('session','qty','entry')):return True
            # Ignore open/flip and any unexpected action. A model cannot adjust
            # the position, stop, size or risk budget through this path.
            if answer.get('action')=='exit':
                put(db,'paper-ai-exit:'+p['id'],dict(at=time.time(),session=p['session'],qty=p['qty']))
            put(db,'ai-connection',dict(ok=True,at=time.time(),model=getattr(model,'model','unknown')))
            put(db,'ai-decision:'+p['user'],dict(at=time.time(),session=p['session'],phase='monitor',answer=answer))
        except Exception:
            put(db,'ai-connection',dict(ok=False,at=time.time(),model=getattr(model,'model','unknown')))
        return True
    return False


def consume_exits(adapter):
    """Queue reductions on the execution thread; fills still need fresh depth."""
    db=adapter.db
    for row in db.execute('SELECT * FROM positions').fetchall():
        p=dict(row);key='paper-ai-exit:'+p['id'];decision=get(db,key)
        if not decision:continue
        if time.time()-decision['at']>60 or decision['session']!=p['session'] or decision['qty']!=p['qty']:
            put(db,key,None);continue
        try:
            adapter.submit(p['user'],dict(reduceOnly=True,positionId=p['id'],fraction='1',reason='ai_exit'),'ai-exit:'+p['id'])
            put(db,key,None)
        except ValueError:
            # Existing risk orders take priority. Never invent an exit fill.
            continue
