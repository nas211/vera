"""Pure deterministic risk gate. No network, clocks, state writes or AI."""
from dataclasses import dataclass
import math
from .rules import identity
from dataclasses import asdict
from .fees import FEE_RATE

@dataclass(frozen=True)
class Intent:
    id: str
    chain_id: str
    market: str
    side: str
    qty: float
    price: float
    stop: float
    leverage: int=1
    reduce_only: bool=False
    mode: str='paper'

@dataclass(frozen=True)
class Account:
    equity: float=25000
    total: float=0
    coin: float=0
    side: float=0
    open_orders: int=0
    daily_net: float=0
    kill: bool=False
    global_kill: bool=False
    recent_orders: int=0
    position_qty: float=0

@dataclass(frozen=True)
class Decision:
    intent_id: str
    approved: bool
    checks: tuple
    reject_reason: str
    trip_kill: bool=False
    intent_hash: str=''

def decide(i, a, *, markets, mid, age, min_qty, step, min_notional):
    numbers=(i.qty,i.price,i.stop,a.equity,a.total,a.coin,a.side,a.daily_net,a.position_qty,a.open_orders,a.recent_orders,mid,age,min_qty,step,min_notional)
    valid=all(type(x) in (int,float) and math.isfinite(x) for x in numbers)
    if not valid: return Decision(i.id,False,(('finite',False),),'non_finite')
    notional=i.qty*i.price
    # Reserve 10bp for adverse simulated stop execution plus entry and exit fees.
    planned=i.qty*(abs(i.price-i.stop)+i.price*.001+(i.price+i.stop)*FEE_RATE)
    trip=a.daily_net<=-20
    checks=(
        ('paper_only',i.mode=='paper'),
        ('identity',bool(i.id and i.chain_id)),
        ('symbol',i.market in markets),
        ('side',i.side in ('long','short')),
        ('positive',i.qty>0 and i.price>0 and mid>0 and a.equity>0),
        ('leverage',type(i.leverage)==int and 1<=i.leverage<=10),
        ('fresh',0<=age<=5),
        ('price_sanity',mid>0 and abs(i.price/mid-1)<=.02),
        ('lot',step>0 and (i.reduce_only or i.qty>=min_qty) and abs(i.qty/step-round(i.qty/step))<1e-6),
        ('minimum',i.reduce_only or notional>=min_notional),
        ('stop',i.reduce_only or (i.stop>0 and (i.stop<i.price if i.side=='long' else i.stop>i.price))),
        ('planned_loss',i.reduce_only or planned<=2+1e-9),
        ('kill',i.reduce_only or not(a.kill or a.global_kill or trip)),
        ('coin_exposure',i.reduce_only or a.coin+notional<=500),
        ('side_exposure',i.reduce_only or a.side+notional<=1000),
        ('total_exposure',i.reduce_only or a.total+notional<=min(2000,a.equity*2)),
        ('open_orders',i.reduce_only or a.open_orders<8),
        ('order_rate',i.reduce_only or a.recent_orders<20),
        ('reduce_only_size',not i.reduce_only or i.qty<=a.position_qty+1e-9),
    )
    failed=next((name for name,ok in checks if not ok),'')
    return Decision(i.id,not failed,checks,failed,trip,identity(asdict(i)))

def accepts(i, decision):
    return decision.approved and decision.intent_id==i.id and decision.intent_hash==identity(asdict(i))
