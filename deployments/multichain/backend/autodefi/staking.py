"""Opt-in DEMO staking accounting. No token, signer, payout or on-chain claim exists."""
import time
from decimal import Decimal as D
from vela.db import get,put
from .lending import Lending,number,down
from .store import cashflow,event

APR=D('.05')
YEAR=D(31536000)
POOL='demo-staking:reward-pool'


class DemoStaking:
    def __init__(self,db,paper,clock=time.time):
        self.db,self.paper,self.clock=db,paper,clock

    def state(self,owner):
        return get(self.db,'demo-staking:'+owner,dict(principal='0',pending='0',claimed='0',at=self.clock()))

    def pool(self):
        return get(self.db,POOL,dict(available='100000',allocated='0',depositsPaused=False))

    def project(self,owner):
        state=self.state(owner);pool=self.pool()
        elapsed=D(str(max(0,self.clock()-state['at'])))
        reward=min(number(pool['available']),number(state['principal'])*APR*elapsed/YEAR)
        return state,pool,reward

    def status(self,owner):
        state,pool,reward=self.project(owner)
        return dict(principal=state['principal'],claimable=str(down(number(state['pending'])+reward)),
                    claimed=state['claimed'],illustrativeAnnualRate=str(APR),lockSeconds=0,
                    remainingDemoRewardPool=pool['available'],depositsPaused=pool['depositsPaused'],
                    mode='DEMO',realFunding=False,onchain=False,redeemable=False,
                    disclosure='Simulated USDC accounting only. Assumed simple annual rate, limited by a shared demo reward pool. No real yield or guaranteed return.')

    def mutate(self,owner,action,body):
        if body.get('confirm') is not True:raise ValueError('Confirm simulated staking; no real funds move')
        if set(body)-{'confirm','amount'}:raise ValueError('Unsupported staking input')
        state,pool,reward=self.project(owner)
        state['pending']=str(number(state['pending'])+reward)
        pool['available']=str(number(pool['available'])-reward)
        pool['allocated']=str(number(pool['allocated'])+reward)
        state['at']=max(state['at'],self.clock())
        if action in ('deposit','withdraw'):
            amount=number(body.get('amount'))
            if amount<=0 or amount!=down(amount):raise ValueError('Positive micro-USDC precision required')
        if action=='deposit':
            if get(self.db,'paper-funding-incomplete:'+owner,False):
                raise ValueError('Funding history is incomplete; reconcile before moving equity')
            loan=Lending(self.db).loan(owner)
            if loan and loan['state'] not in ('REPAID','CANCELLED'):raise ValueError('Settle existing debt before demo staking')
            if pool['depositsPaused']:raise ValueError('Demo staking deposits are paused')
            if get(self.db,'paper-prefs:'+owner,{}).get('autonomous') or self.paper.getPosition(owner) or self.db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchone():
                raise ValueError('Stop entries and close exposure before staking demo capital')
            balance=number(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0])
            if amount>balance or number(state['principal'])+amount>10000:raise ValueError('Insufficient demo capital or staking limit exceeded')
            cashflow(self.db,owner,-amount)
            state['principal']=str(number(state['principal'])+amount)
        elif action=='withdraw':
            if amount>number(state['principal']):raise ValueError('Insufficient staked demo principal')
            state['principal']=str(number(state['principal'])-amount)
            cashflow(self.db,owner,amount)
        elif action=='claim':
            amount=down(number(state['pending']))
            if amount<=0:raise ValueError('No accrued demo reward to claim')
            state['pending']=str(number(state['pending'])-amount)
            state['claimed']=str(number(state['claimed'])+amount)
            # Yield is its own income stream, never counted as trading P&L.
            cashflow(self.db,owner,amount)
        else:raise ValueError('Unsupported staking action')
        put(self.db,POOL,pool);put(self.db,'demo-staking:'+owner,state)
        event(self.db,owner,None,'DEMO_STAKING_'+action.upper(),amount,'Simulated accounting; no token transfer or real yield')
        return dict(action=action,amount=str(amount),staking=self.status(owner),mode='DEMO',realFunding=False)
