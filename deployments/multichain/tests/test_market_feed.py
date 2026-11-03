"""Official adapter exercised with synthetic protocol frames, never live evidence."""
import time
from decimal import Decimal as D
import pytest
from vela.lighter.feed import LighterFeed


def feed():
    f=LighterFeed()
    f.metadata({'order_book_details':[
        dict(symbol=symbol,market_id=i,market_type='perp',status='active',
             supported_price_decimals=2,supported_size_decimals=5,
             min_initial_margin_fraction=1000,maintenance_margin_fraction=500,
             min_base_amount='.00001',min_quote_amount='1')
        for i,symbol in enumerate(('BTC','ETH','SOL'))]})
    return f


def frame(kind,channel,now,**body):
    return dict(type=kind,channel=channel,timestamp=now*1000,**body)


def book(nonce=10,**extra):
    return dict(nonce=nonce,bids=[dict(price='59999',size='2')],
                asks=[dict(price='60001',size='3')],**extra)


def test_public_metadata_book_funding_and_freshness():
    f=feed();now=time.time()
    assert set(f.markets)=={'BTC/USD','ETH/USD','SOL/USD'}
    f.message(frame('subscribed/order_book','order_book/0',now,order_book=book()),now)
    f.message(frame('update/market_stats','market_stats/0',now,market_stats=dict(
        mark_price='60000',index_price='60000',current_funding_rate='.01',
        funding_rate='.02',funding_timestamp=now*1000,daily_price_change='1.25')),now)
    m=f.markets['BTC/USD']
    assert m.fresh(now) and m.funding_rate==D('.0001') and m.last_funding_rate==D('.0002')
    assert m.public()['bids']==[['59999','2']] and m.public()['asks']==[['60001','3']]
    assert not m.fresh(now+6)
    f.message(frame('update/order_book','order_book:0',now+1,
        order_book=dict(nonce=11,begin_nonce=10,bids=[dict(price='59999',size='1')],asks=[])),now+1)
    assert m.nonce==11 and m.bids[D(59999)]==1
    with pytest.raises(ValueError,match='sequence gap'):
        f.message(frame('update/order_book','order_book:0',now+2,order_book=book(13,begin_nonce=12)),now+2)
    assert m.nonce is None and not m.fresh(now+2)
    f.message(frame('subscribed/order_book','order_book:0',now+3,order_book=book(20)),now+3)
    assert m.nonce==20 and m.fresh(now+3)


@pytest.mark.parametrize('problem',['stale','crossed','missing_market'])
def test_unusable_market_frames_block_freshness(problem):
    f=feed();now=time.time();m=f.markets['BTC/USD']
    with pytest.raises(ValueError):
        if problem=='stale':
            f.message(frame('subscribed/order_book','order_book:0',now-20,order_book=book()),now)
        elif problem=='crossed':
            b=book();b['bids'][0]['price']='60002'
            f.message(frame('subscribed/order_book','order_book:0',now,order_book=b),now)
        else:f.metadata({'order_book_details':[]})
    assert not m.fresh(now)


def test_stream_reconnect_invalidates_quotes_and_resubscribes(monkeypatch):
    import asyncio,json
    import vela.lighter.feed as module
    f=feed();now=time.time();sent=[];attempts=[]
    for market in f.markets.values():
        market.book_at=market.stats_at=now;market.nonce=10
    class Socket:
        async def __aenter__(self):
            attempts.append(True)
            if len(attempts)==1:raise OSError('synthetic connection loss')
            return self
        async def __aexit__(self,*args):pass
        async def send(self,data):sent.append(json.loads(data))
        async def recv(self):raise asyncio.CancelledError
    async def backoff(seconds):
        assert 1<=seconds<=2
        assert not f.connected and f.reconnects==1
        assert all(m.nonce is None and m.book_at==m.stats_at==0 for m in f.markets.values())
    monkeypatch.setattr(module,'connect',lambda *args,**kwargs:Socket())
    monkeypatch.setattr(module.asyncio,'sleep',backoff)
    with pytest.raises(asyncio.CancelledError):asyncio.run(f.stream())
    assert len(attempts)==2
    assert {message['channel'] for message in sent}=={f'{channel}/{i}' for channel in ('order_book','market_stats') for i in range(3)}
    assert all(message['type']=='subscribe' for message in sent)
    assert all(not m.fresh(now) for m in f.markets.values())
