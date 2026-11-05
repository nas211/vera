"""DEMO stablecoin ledger. No mint, bridge, custody or real financial obligation."""
import time,json
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR, InvalidOperation
from vela.db import get,put
from .store import cashflow,event
from .risk import RISK
from .adapters import pool_key
MICRO=D('.000001'); WEI=D(10)**18

def number(x):
    try:
        if len(str(x))>100:raise ValueError('Invalid amount')
        x=D(str(x))
    except (InvalidOperation,TypeError):raise ValueError('Invalid amount') from None
    if not x.is_finite() or abs(x)>D('1e30'): raise ValueError('Invalid amount')
    return x

def up(x): return number(x).quantize(MICRO,rounding=ROUND_CEILING)
def down(x): return number(x).quantize(MICRO,rounding=ROUND_FLOOR)

class Lending:
    def __init__(self,db,clock=None): self.db,self.clock=db,clock or time.time
    def vault(self,owner,fresh=True):
        r=self.db.execute('SELECT * FROM vaults WHERE owner=?',(owner,)).fetchone()
        if not r: raise ValueError('Confirmed vault required')
        if fresh and not json.loads(r['data']).get('simulated') and not 0<=self.clock()-r['checked']<=RISK['chainFreshSeconds']: raise ValueError('Refresh confirmed vault state')
        return json.loads(r['data'])
    def loan(self,owner):
        r=self.db.execute("SELECT * FROM loans WHERE owner=? ORDER BY rowid DESC LIMIT 1",(owner,)).fetchone()
        return dict(r) if r else None
    def quote(self,owner,drop=None):
        if drop is not None:
            drop=number(drop)
            if drop not in (D(0),D('.10'),D('.25'),D('.50')): raise ValueError('Unsupported stress scenario')
            v=self.vault(owner,False)
            base=v.get('illustrativePrice',RISK['dogePrice'])
            put(self.db,'quote:'+owner,dict(price=str(D(base)*(1-drop)),drop=str(drop),at=self.clock(),source='ILLUSTRATIVE — not a live collateral oracle'))
        return get(self.db,'quote:'+owner)
    def price(self,owner,fresh=True):
        q=self.quote(owner)
        if not q or (fresh and not 0<=self.clock()-q['at']<=RISK['quoteFreshSeconds']): raise ValueError('Refresh illustrative DOGE quote')
        return number(q['price'])
    def debt(self,l,now=None):
        if not l: return D(0),D(0)
        now=self.clock() if now is None else now
        interest=number(l['interest'])
        if l['state'] in ('ACTIVE','CLOSING','SHORTFALL'):
            interest+=number(l['principal'])*number(l['apr'])*D(str(max(0,now-l['last'])))/D(31536000)
        return number(l['principal'])+up(interest),interest
    def accrue(self,l,now=None):
        now=self.clock() if now is None else now
        debt,interest=self.debt(l,now)
        self.db.execute('UPDATE loans SET interest=?,last=? WHERE id=?',(str(interest),now,l['id']))
        l.update(interest=str(interest),last=now)
        return debt
    def health(self,owner):
        v=self.vault(owner,False); l=self.loan(owner); debt,_=self.debt(l)
        price=self.price(owner,False); collateral=D(v['collateral'])/WEI; value=collateral*price
        pool=get(self.db,pool_key(owner)); reserve=D(RISK['initialDemoLiquidity'])*D(RISK['reserveRatio'])
        return dict(collateral=str(collateral),price=str(price),collateralValue=str(value),debt=str(debt),
            interest=str(self.debt(l)[0]-D(l['principal'])) if l else '0',principal=l['principal'] if l else '0',
            ltv=str(debt/value) if value else None,healthFactor=str(value*D(RISK['liquidationThreshold'])/debt) if debt else None,
            liquidationThreshold=RISK['liquidationThreshold'],maxLtv=RISK['maxLtv'],apr=l['apr'] if l else RISK['annualInterest'],
            borrowCapacity=str(max(D(0),min(value*D(RISK['maxLtv'])-debt,D(pool['cash'])-reserve))),
            withdrawableDoge=str(D(v['withdrawable'])/WEI),
            theoreticalUnlockedDoge=str(max(D(0),collateral-debt/(price*D(RISK['maxLtv'])))),
            quote=self.quote(owner),loan=l,simulated=True,
            scenarios=[dict(drop=str(d),collateralValue=str(value*(1-d)),healthFactor=str(value*(1-d)*D(RISK['liquidationThreshold'])/debt) if debt else None) for d in (D('.10'),D('.25'),D('.50'))])
    def borrow(self,owner):
        v=self.vault(owner); price=self.price(owner); old=self.loan(owner)
        prior=self.db.execute('SELECT * FROM loans WHERE vault=? AND nonce=?',(v['address'],v['loanNonce'])).fetchone()
        if prior:
            if prior['owner']!=owner or prior['state']=='CANCELLED': raise ValueError('Borrow request cancelled or foreign')
            return dict(prior)
        if old and old['state'] not in ('REPAID','CANCELLED'): raise ValueError('Previous loan must settle first')
        if v['loanState']!=1 or v['paused']: raise ValueError('Confirmed BorrowRequested required')
        amount=D(v['principalMicro'])/1000000
        if amount<=0 or amount>D(v['collateral'])/WEI*price*D(RISK['maxLtv']): raise ValueError('Maximum illustrative LTV exceeded')
        pool=get(self.db,pool_key(owner))
        if amount>D(pool['cash'])-D(RISK['initialDemoLiquidity'])*D(RISK['reserveRatio']): raise ValueError('Demo liquidity reserve required')
        pool['cash']=str(D(pool['cash'])-amount);put(self.db,pool_key(owner),pool)
        self.db.execute("INSERT INTO loans(id,owner,vault,nonce,principal,interest,original,paid,last,apr,state) VALUES (?,?,?,?,?,'0',?,'0',?,?,'ACTIVE')",(v['loanId'],owner,v['address'],v['loanNonce'],str(amount),str(amount),self.clock(),RISK['annualInterest']))
        cashflow(self.db,owner,amount); event(self.db,owner,v['loanId'],'DEMO_LOAN_ISSUED',amount,'Simulated USDC credit; no stablecoin transferred')
        return self.loan(owner)
    def repay_amount(self,owner,paid,as_of=None):
        """Apply an already capacity-checked repayment, interest first, atomically."""
        if get(self.db,'paper-funding-incomplete:'+owner,False):
            raise ValueError('Funding history is incomplete; reconcile before moving equity')
        l=self.loan(owner)
        if not l or l['state'] not in ('ACTIVE','CLOSING','SHORTFALL'): raise ValueError('No repayable loan')
        debt=self.accrue(l,as_of);paid=number(paid)
        balance=D(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0])
        if paid<0 or paid>debt or paid>max(D(0),balance) or paid!=down(paid):raise ValueError('Invalid repayment amount')
        interest_paid=min(paid,up(l['interest']));principal_paid=paid-interest_paid
        pool=get(self.db,pool_key(owner));pool['cash']=str(D(pool['cash'])+paid);pool['interestIncome']=str(D(pool['interestIncome'])+interest_paid);put(self.db,pool_key(owner),pool)
        cashflow(self.db,owner,-paid)
        state='AWAITING_CHAIN' if debt==paid and balance>=0 else l['state']
        self.db.execute('UPDATE loans SET principal=?,interest=?,paid=?,state=? WHERE id=?',(str(D(l['principal'])-principal_paid),str(max(D(0),D(l['interest'])-interest_paid)),str(D(l['paid'])+paid),state,l['id']))
        event(self.db,owner,l['id'],'DEMO_REPAYMENT',paid,'Simulated repayment; interest first; no stablecoin transferred')
        self.reconcile_simulated(owner)
        return self.loan(owner)
    def reconcile_simulated(self,owner):
        v=self.vault(owner,False);loan=self.loan(owner)
        if v.get('simulated') and loan and loan['state']=='AWAITING_CHAIN':
            if self.debt(loan)[0]!=0:raise ValueError('Unsettled simulated debt')
            remaining=int(v['collateral'])-int(D(loan['seized']))
            if remaining<0:raise ValueError('Invalid simulated collateral settlement')
            v.update(collateral=str(remaining),withdrawable=str(remaining),loanState=0,trading=False)
            self.db.execute("UPDATE loans SET state='REPAID' WHERE id=?",(loan['id'],))
            self.db.execute('UPDATE vaults SET data=?,checked=? WHERE owner=?',(json.dumps(v),self.clock(),owner))
            event(self.db,owner,loan['id'],'SIMULATED_SETTLEMENT','0','Demo collateral unlocked; no chain receipt or transfer')
    def repay_available(self,owner,as_of=None):
        l=self.loan(owner)
        if not l or l['state'] not in ('ACTIVE','CLOSING','SHORTFALL'): raise ValueError('No repayable loan')
        if self.db.execute('SELECT 1 FROM positions WHERE user=?',(owner,)).fetchone() or self.db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchone(): raise ValueError('Close and reconcile positions first')
        now=self.clock() if as_of is None else as_of
        debt=self.debt(l,now)[0]; balance=D(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0])
        result=self.repay_amount(owner,min(debt,max(D(0),down(balance))),now)
        if result['state'] in ('AWAITING_CHAIN','REPAID'): self.surplus(owner)
        else:self.db.execute("UPDATE loans SET state='SHORTFALL' WHERE id=?",(l['id'],))
        return self.loan(owner)
    def surplus(self,owner):
        if get(self.db,'paper-funding-incomplete:'+owner,False):
            raise ValueError('Funding history is incomplete; reconcile before moving equity')
        balance=D(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0])
        if balance>0:
            cashflow(self.db,owner,-balance)
            put(self.db,'demo-surplus:'+owner,str(D(get(self.db,'demo-surplus:'+owner,'0'))+balance))
    def liquidate(self,owner,confirmed):
        if confirmed is not True: raise ValueError('Explicit simulated liquidation confirmation required')
        if get(self.db,'paper-funding-incomplete:'+owner,False):
            raise ValueError('Funding history is incomplete; reconcile before liquidation')
        l=self.loan(owner); v=self.vault(owner); price=self.price(owner)
        if l and l['state']=='DEFAULT_PENDING' and D(v['collateral'])>D(l['seized']):
            self.db.execute("UPDATE loans SET state='SHORTFALL',bad_debt='0',last=? WHERE id=?",(self.clock(),l['id']))
            l=self.loan(owner)
        if not l or l['state']!='SHORTFALL': raise ValueError('Reconcile trading and repayment first')
        now=self.clock();debt=self.accrue(l,now); pool=get(self.db,pool_key(owner))
        balance=D(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0]); deficit=max(D(0),-balance)
        remaining_wei=D(v['collateral'])-D(l['seized']); sale_price=price*(1-D(RISK['liquidationDiscount']))
        want=((debt+deficit)/sale_price*WEI).to_integral_value(rounding=ROUND_CEILING)
        seize=min(remaining_wei,want,(D(pool['buyerCash'])/sale_price*WEI).to_integral_value(rounding=ROUND_FLOOR))
        proceeds=min(debt+deficit,down(seize/WEI*sale_price))
        if proceeds<=0: raise ValueError('No simulated liquidation liquidity')
        pool['buyerCash']=str(D(pool['buyerCash'])-proceeds);pool['seizedDoge']=str(D(pool['seizedDoge'])+seize/WEI);put(self.db,pool_key(owner),pool)
        cashflow(self.db,owner,proceeds)
        total_seized=D(l['seized'])+seize
        self.db.execute('UPDATE loans SET seized=? WHERE id=?',(str(total_seized),l['id']))
        event(self.db,owner,l['id'],'DEMO_COLLATERAL_SALE',proceeds,
              'Simulated collateral sale; no tokens move' if v.get('simulated') else 'Test DOGE is not moved until owner confirms the settlement transaction')
        result=self.repay_available(owner,now); remaining=self.debt(result)[0]
        new_balance=D(self.db.execute('SELECT balance FROM users WHERE address=?',(owner,)).fetchone()[0])
        bad=remaining+max(D(0),-new_balance)
        if total_seized==D(v['collateral']) and bad>0:
            self.db.execute("UPDATE loans SET state='DEFAULT_PENDING',bad_debt=? WHERE id=?",(str(bad),l['id']))
            if v.get('simulated'):
                # Preserve principal, interest and bad debt. Exhausted collateral
                # cannot be withdrawn and a defaulted account cannot borrow again.
                v.update(collateral='0',withdrawable='0',loanState=0,trading=False)
                self.db.execute('UPDATE vaults SET data=?,checked=? WHERE owner=?',(json.dumps(v),self.clock(),owner))
                self.db.execute("UPDATE loans SET state='DEFAULTED' WHERE id=?",(l['id'],))
                event(self.db,owner,l['id'],'SIMULATED_DEFAULT',bad,'Exhausted demo collateral; residual bad debt remains recorded; no blockchain receipt')
        return self.loan(owner)

    def cancel_unissued(self,owner):
        v=self.vault(owner)
        if v['loanState']!=1: raise ValueError('No pending request')
        row=self.db.execute('SELECT state FROM loans WHERE vault=? AND nonce=?',(v['address'],v['loanNonce'])).fetchone()
        if row:
            if row[0]!='CANCELLED':raise ValueError('Credited loan cannot be cancelled')
        else:
            self.db.execute("INSERT INTO loans(id,owner,vault,nonce,principal,interest,original,paid,last,apr,state) VALUES (?,?,?,?,'0','0','0','0',?,'0','CANCELLED')",(v['loanId'],owner,v['address'],v['loanNonce'],self.clock()))
            event(self.db,owner,v['loanId'],'UNISSUED_REQUEST_CANCELLED')
        return dict(cancelled=True)
