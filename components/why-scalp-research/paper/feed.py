"""Strict public L2 reconstruction and independent book/trade sequence checks."""
import math
from datetime import datetime

def timestamp(value):
    if isinstance(value,(int,float)):return float(value)/1000 if value>1e12 else float(value)
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

class FeedError(ValueError):pass
class Feed:
    def __init__(self,coins):
        self.coins=set(coins);self.books={};self.seq={};self.epoch=0
    def reset(self):
        self.books.clear();self.seq.clear();self.epoch+=1
    def receive(self,d,now):
        m=d.get('market');typ=d.get('header',{}).get('type','')
        if m not in self.coins:return None
        if not typ.startswith(('orderbook.','trades.')):return None
        kind='book' if typ.startswith('orderbook.') else 'trades';key=(m,kind)
        snap=typ.endswith('snapshot');seq=d.get('sequence_num')
        if not isinstance(seq,int):raise FeedError('missing sequence')
        if key in self.seq and seq<=self.seq[key]:return None
        if not snap:
            if key not in self.seq:raise FeedError('increment without snapshot')
            if seq!=self.seq[key]+1:raise FeedError('sequence gap')
        if kind=='book':
            b={'bids':{},'asks':{}} if snap else {s:dict(self.books[m][s]) for s in ('bids','asks')}
            changed=[]
            for side in ('bids','asks'):
                for p,q in d.get(side,[]):
                    p,q=float(p),float(q)
                    if not math.isfinite(p+q) or p<=0 or q<0:raise FeedError('invalid depth')
                    old=b[side].get(p,0)
                    if q:b[side][p]=q
                    else:b[side].pop(p,None)
                    changed.append((side,p,old,q))
            if not b['bids'] or not b['asks'] or max(b['bids'])>=min(b['asks']):raise FeedError('empty or crossed book')
            b['received']=now
            source=d.get('timestamp') or d.get('updated_at') or d.get('header',{}).get('created_at')
            b['source_time']=timestamp(source) if source else None
            self.books[m]=b
            self.seq[key]=seq
            return ('book',m,{'snapshot':snap,'changed':changed})
        trades=[]
        if not snap:
            for t in d.get('trades',[]):
                p,q=float(t['price']),float(t['amount']);ts=timestamp(t['executed_at'])
                if not math.isfinite(p+q+ts) or min(p,q)<=0 or t['direction'] not in ('buy','sell'):raise FeedError('invalid trade')
                trades.append(dict(t,price=p,amount=q,ts=ts))
        self.seq[key]=seq
        return ('trades',m,sorted(trades,key=lambda t:t['ts']))
