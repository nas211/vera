"""Deterministic lending governor around an unchanged Vela paper engine."""
import hashlib,json,time,urllib.request,urllib.parse,os,threading
from decimal import Decimal as D
from vela.agents import VelaModel,Agents,model_module
from vela.lighter.engine import LighterPaperAdapter
from vela.db import get,put,transaction
from .lending import Lending,number
from .risk import RISK
from .config import model_env
from vela.strategy_profiles import profile,research_schema,validate as validate_strategy

class ProtocolModel(VelaModel):
    def __init__(self,model_values=None):
        # An explicit secure-loader result must never fall back to ambient keys.
        self.endpoint=model_env('MODEL_ENDPOINT','https://api.openai.com/v1/responses',values=model_values)
        self.model=model_env('MODEL',values=model_values);self.key=model_env('MODEL_KEY',values=model_values)
        self.last=0.;self.requests=[];self._model_lock=threading.Lock()
        self.opener=urllib.request.build_opener(model_module.NoRedirect())
        u=urllib.parse.urlsplit(self.endpoint)
        if u.scheme!='https' or u.username or u.password or u.query or u.fragment or not u.path.endswith(('/responses','/chat/completions')): raise ValueError('Invalid model endpoint')
        self.execution_policy='Paper depth fills, 4bp taker fees each side, 2bp additional slippage and observed hourly funding. Loan and permission checks are mandatory.'

    def bind_usage(self,path):
        from .model_usage import MeteredOpener,UsageLedger
        if isinstance(self.opener,MeteredOpener):
            if self.opener.ledger.path!=path.resolve():raise ValueError('Model already bound to another ledger')
            return
        self.opener=MeteredOpener(self.opener,UsageLedger(path))

    def ask(self,role,evidence,schema):
        with self._model_lock:return super().ask(role,evidence,schema)

class GovernedPaper(LighterPaperAdapter):
    def __init__(self,*args,require_session=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.require_session=require_session
    def account(self,*args): raise ValueError('Authenticate wallet; accounts start at zero')
    def pause_entries(self,owner,reason='New entries are stopped.',action='Review your limits, then select Start AI Trading.'):
        super().pause_entries(owner)
        self.db.execute('DELETE FROM kv WHERE key=?',('agent-authorization:'+owner,))
        put(self.db,'paper-stop-reason:'+owner,dict(reason=reason,action=action,at=time.time()))
    def snapshot(self,owner):
        result=super().snapshot(owner)
        result['entryStop']=get(self.db,'paper-stop-reason:'+owner) if not result['preferences'].get('autonomous') else None
        return result
    def strategy(self,owner):
        return profile(get(self.db,'paper-prefs:'+owner,{}).get('strategyStyle','swing'))
    def choose_strategy(self,owner,body):
        if body.get('confirm') is not True:raise ValueError('Confirm the strategy change')
        chosen=profile(body.get('strategyStyle'))
        if get(self.db,'paper-prefs:'+owner,{}).get('autonomous'):
            raise ValueError('Stop trading to change strategy.')
        self.pause_entries(owner,'Strategy changed. Select Start Trading to begin a new run.')
        prefs=get(self.db,'paper-prefs:'+owner,{})
        put(self.db,'paper-prefs:'+owner,dict(prefs,strategyStyle=chosen['id']))
        for key in ('ai-stage:','ai-approved:','ai-last-research:','ai-decision:'):
            self.db.execute('DELETE FROM kv WHERE key=?',(key+owner,))
        self.activity(owner,'strategy_changed','Selected '+chosen['name']+' for future entries; existing position limits remain unchanged')
        return chosen
    def setup(self,owner):
        saved=self.permission(owner)
        required=not saved or any(number(saved.get(k,'0'))<=0 for k in ('allocation','positionSize','maxLoss','leverage','markets'))
        stage=get(self.db,'ai-stage:'+owner,{})
        return dict(strategy=self.strategy(owner),
            paperRequired=required,renewalRequired=bool(saved and int(saved.get('expiresAt',0))<=time.time()),
            nativeRequired=False,nativeEnableRequired=False,permissionRevision=self.revision(owner),
            paperLimits=saved,phase=stage.get('phase','waiting'),
            approvedAt=(get(self.db,'ai-approved:'+owner) or {}).get('at'))
    def authorize_run(self,owner):
        # Called only by authenticated Start, after its network preflight and
        # final session/revision check. Browser cookies are not ongoing consent.
        if owner.startswith('paper:'):raise ValueError('Wallet authorization required')
        permission=self.permission(owner)
        prefs=get(self.db,'paper-prefs:'+owner,{})
        if not prefs.get('autonomous'):raise ValueError('Start trading before authorization')
        expires=int(permission.get('expiresAt',0))
        if expires<=time.time():raise ValueError('Active trading permission required')
        put(self.db,'agent-authorization:'+owner,dict(version=1,owner=owner,
            revision=self.revision(owner),generation=prefs.get('generation',0),
            expiresAt=expires,authorizedAt=time.time()))
    def run_authorized(self,owner):
        consent=get(self.db,'agent-authorization:'+owner)
        prefs=get(self.db,'paper-prefs:'+owner,{})
        return bool(consent and consent.get('version')==1 and consent.get('owner')==owner
            and not owner.startswith('paper:') and prefs.get('autonomous')
            and consent.get('revision')==self.revision(owner)
            and consent.get('generation')==prefs.get('generation',0)
            and int(consent.get('expiresAt',0))>time.time()
            and int(self.permission(owner).get('expiresAt',0))>time.time())
    def account_budget(self,owner):
        return min(D(2000000),number(get(self.db,'account-stop-budget:'+owner,'2000000')))
    def protect(self,p):
        # A newly tightened wallet permission also applies to existing exposure.
        # Persist it so later expiry, loan settlement or permission renewal can
        # never widen this position's budget again.
        meta=self.meta(p)
        budget=min(D(2),self.settings.loss_budget,number(meta.get('riskBudget','2')),self.account_budget(p['user'])/1000000)
        permissions=[get(self.db,'trading-permission:'+p['user'],{})]
        try:permissions.append(self.permission(p['user']))
        except ValueError:pass  # Missing collateral state cannot disable base protection.
        for permission in permissions:
            if permission.get('maxLoss') is not None:
                budget=min(budget,number(permission['maxLoss'])/1000000)
        meta['riskBudget']=str(budget)
        put(self.db,'paper-position:'+p['id'],meta)
        if budget<=D('.000001'):
            # An existing sub-precision permission cannot produce a valid
            # rounded stop. Trigger a reduction at the current observed market.
            return self.market(p['symbol']).mark
        return super().protect(p)
    def configure(self,owner,body):
        if body.get('confirm') is not True:raise ValueError('Explicit paper trading permission required')
        allocation=number(body.get('allocation','0'));position=number(body.get('positionSize','200'))
        budget=number(body.get('maxLoss','2'));leverage=number(body.get('leverage','1'))
        if not 0<allocation<=10000 or not 0<position<=200 or position>allocation:raise ValueError('Invalid trading allocation or position size')
        if not D('.000002')<=budget<=min(D(2),self.settings.loss_budget) or not 1<=leverage<=3:raise ValueError('Invalid risk limits; stop budget must be at least two micro-USDC and cannot exceed $2')
        old=get(self.db,'trading-permission:'+owner,{})
        # Preserve any stricter existing stop budget even when editing settings.
        budget=min(budget,number(old.get('maxLoss',budget*1000000))/1000000,number(self.authorization_budget(owner))/1000000)
        if budget<D('.000002'):raise ValueError('Existing stop limit cannot be raised; reconcile the stored limit first')
        self.pause_entries(owner)
        permission=dict(allocation=str(allocation*1000000),positionSize=str(position*1000000),maxLoss=str(budget*1000000),leverage=str(leverage),markets='7',expiresAt=str(int(time.time())+86400),revision=old.get('revision',0)+1)
        put(self.db,'trading-permission:'+owner,permission)
        self.activity(owner,'paper_permission','Owner authorized paper execution for 24 hours; bounded allocation and fee-inclusive stop budget')
        return permission
    def permission(self,owner):
        lending=Lending(self.db);l=lending.loan(owner);extra=get(self.db,'trading-permission:'+owner,{})
        p=extra
        if l and l['state'] not in ('REPAID','CANCELLED'):
            # Old on-chain limits may seed saved settings. Paper Start grants
            # execution consent; vault agent/enable transactions are unnecessary.
            p=extra or lending.vault(owner,False)['limits']
        return dict(p,maxLoss=str(min(number(p['maxLoss']),number(self.authorization_budget(owner))))) if p else p
    def start_permission(self,owner):
        p=self.permission(owner)
        if not p or any(number(p.get(k,'0'))<=0 for k in ('allocation','positionSize','maxLoss','leverage','markets')):
            raise ValueError('Save your trading settings before starting')
        if number(p['maxLoss'])<2:raise ValueError('Existing stop limit cannot be raised')
        allocation=min(number(p['allocation']),D(10000_000000))
        size=min(number(p['positionSize']),allocation,D(200_000000))
        return dict(p,allocation=str(allocation),positionSize=str(size),
            leverage=str(min(D(3),number(p['leverage']))),markets=str(int(p['markets'])&7),
            expiresAt=str(int(time.time())+86400))
    def authorization_budget(self,owner):
        """Review ceiling, distinct from the currently executable permission.

        A first borrow revokes the constructor's empty permission and increments
        revision to one. That provably unused permission is not a chosen $0
        budget. Unknown legacy revocation history remains blocked. Stored paper
        limits (including zero) and rewarded-vault lifetime limits still apply.
        This method does not authorize entries or alter execution gates.
        """
        budget=min(min(D(2),self.settings.loss_budget)*1000000,self.account_budget(owner))
        extra=get(self.db,'trading-permission:'+owner,{})
        if extra.get('maxLoss') is not None:budget=min(budget,number(extra['maxLoss']))
        lending=Lending(self.db);loan=lending.loan(owner)
        if loan and loan['state'] not in ('REPAID','CANCELLED'):
            v=lending.vault(owner,False);p=v['limits']
            if v.get('kind')=='rewarded':
                budget=min(budget,number(v.get('stopBudgetMicro','0')))
                if number(p['maxLoss'])>0:budget=min(budget,number(p['maxLoss']))
            else:
                unused=(v.get('loanNonce')==1 and v.get('permissionRevision')==1
                    and v.get('loanState') in (1,2) and not v.get('trading')
                    and v.get('agent','').lower()=='0x'+'00'*20
                    and all(str(p.get(k))=='0' for k in ('allocation','positionSize','expiresAt','maxLoss','leverage','markets'))
                    and p.get('autoReduce') is False)
                if not unused:budget=min(budget,number(p['maxLoss']))
        return str(int(max(D(0),budget)))
    def revision(self,owner):
        v=self.db.execute('SELECT data FROM vaults WHERE owner=?',(owner,)).fetchone()
        return [get(self.db,'trading-permission:'+owner,{}).get('revision',0),json.loads(v[0])['permissionRevision'] if v else 0]
    def gate(self,owner,order=None,require_running=True, filling=False, manual=False,starting=False):
        if starting and (require_running or order or filling or manual):raise ValueError('Start review only')
        if get(self.db,'paper-funding-incomplete:'+owner,False):
            raise ValueError('Funding history is incomplete; reconcile before opening positions')
        if get(self.db,'direct-settlement:'+owner,False):raise ValueError('Wait for position settlement to finish')
        if require_running or filling or manual:
            binding=get(self.db,'agent-session:'+owner)
            consent=get(self.db,'agent-authorization:'+owner)
            if consent is not None:
                if not self.run_authorized(owner):raise ValueError('Active server trading authorization required')
            elif self.require_session or binding:
                session=self.db.execute('SELECT address,expires FROM auth_sessions WHERE hash=?',(binding or '',)).fetchone()
                if not session or session['address']!=owner or session['expires']<=time.time():
                    raise ValueError('Active authenticated trading session required')
        lending=Lending(self.db);l=lending.loan(owner);prefs=get(self.db,'paper-prefs:'+owner,{})
        financed=l and l['state'] not in ('REPAID','CANCELLED')
        hf=None
        if financed:
            v=lending.vault(owner);lending.price(owner)
            if l['state']!='ACTIVE' or v['loanState']!=2 or l['id']!=v['loanId']: raise ValueError('Active confirmed demo loan required')
            if v['paused']:raise ValueError('Vault is paused; new paper positions are blocked')
            hf=D(lending.health(owner)['healthFactor'])
            if hf<D(RISK['blockHealthBelow']): raise ValueError('Loan health blocks new positions')
        p=self.start_permission(owner) if starting else self.permission(owner)
        if int(p.get('expiresAt',0))<=time.time():raise ValueError('Active paper trading permission required')
        if require_running and not prefs.get('autonomous'): raise ValueError('AI entries stopped')
        snap=self.snapshot(owner)
        if snap['equity'] is None or D(snap['equity'])<=0: raise ValueError('Fresh positive trading equity required')
        # Configured capital drawdown ceiling, on top of every-position $2 cap.
        # External cashflows change initial capital, so repayment is not a loss.
        capital=max(D(0),D(snap['initial']))
        if D(snap['equity'])<capital*(1-D(RISK['maxCapitalDrawdown'])): raise ValueError('Trading capital drawdown limit reached')
        scale=D('.5') if hf is not None and hf<D(RISK['reduceHealthBelow']) else D(1)
        allocation=min(D(p['allocation'])/1000000,D(snap['equity']))*scale
        position=D(p['positionSize'])/1000000*scale
        leverage=min(D(3),D(p['leverage']),D(1) if scale<1 else D(3))
        budget=min(D(2),self.settings.loss_budget,D(p['maxLoss'])/1000000,self.account_budget(owner)/1000000)
        if order:
            if order.get('agent'):
                connection=get(self.db,'ai-connection',{})
                if not connection.get('ok') or not 0<=time.time()-connection.get('at',0)<=300:raise ValueError('Fresh successful model connection required')
            bits={'BTC/USD':1,'ETH/USD':2,'SOL/USD':4}
            if not int(p['markets'])&bits.get(order['symbol'],0): raise ValueError('Market not authorized or unsupported')
            if D(order['leverage'])>leverage or D(order['notional'])>position: raise ValueError('Permission position or leverage limit')
            exposure=sum(D(x['qty'])*D(x['entry'])/D(x['leverage']) for x in snap['positions'])
            for row in self.db.execute("SELECT payload FROM paper_orders WHERE user=? AND status='pending'",(owner,)):
                pending=json.loads(row[0])
                if not pending.get('reduceOnly') and not (filling and pending==order): exposure+=D(pending['notional'])/D(pending['leverage'])
            if exposure+D(order['notional'])/D(order['leverage'])>allocation: raise ValueError('Agent allocation limit')
            if D(order.get('riskBudget',budget))>budget: raise ValueError('Fee-inclusive stop budget exceeded')
        return dict(allocation=allocation,position=position,leverage=leverage,budget=budget)
    def submit(self,user,payload,client_id):
        if not payload.get('reduceOnly'):
            manual=user.startswith('paper:') and not bool(payload.get('agent'))
            self.gate(user,payload,require_running=not manual,manual=manual)
        return super().submit(user,payload,client_id)
    def fill_open(self,user,order):
        # Exclude this order from the pending reservation during its fill check.
        manual=user['address'].startswith('paper:') and not bool(order.get('agent'))
        limits=self.gate(user['address'],order,require_running=not manual,filling=True)
        p=super().fill_open(user,order)
        if D(p['qty'])*D(p['entry'])>limits['position']: raise ValueError('Fill price exceeds notional authorization')
        exposure=sum(D(x['qty'])*D(x['entry'])/D(x['leverage'])+D(x['entry_fee_left']) for x in self.getPosition(user['address']))
        if exposure>limits['allocation']:raise ValueError('Filled margin and fees exceed allocation')
        return p
    def open(self,user,signal):
        limits=self.gate(user);prefs=get(self.db,'paper-prefs:'+user,{})
        notional=min(D(200),limits['position']*D('.99'),limits['allocation']*D('.5'))
        return self.submit(user,dict(symbol=signal['symbol'],side=signal['side'],type='market',leverage=str(limits['leverage']),
            notional=str(notional),stopBps=signal['stop_bps'],targetBps=signal['target_bps'],hold=signal['hold'],riskBudget=str(limits['budget']),
            agent=True,agentGeneration=prefs.get('generation',0)),'ai:'+hashlib.sha256(signal['signal_id'].encode()).hexdigest())
    def start(self,owner,model):
        if not model.ready: raise ValueError('Dedicated AI model is not configured')
        self.gate(owner,require_running=False,starting=True)
        for symbol in ('BTC/USD','ETH/USD','SOL/USD'): self.market(symbol)
        permission=self.start_permission(owner)
        permission['revision']=get(self.db,'trading-permission:'+owner,{}).get('revision',0)+1
        put(self.db,'trading-permission:'+owner,permission)
        prefs=get(self.db,'paper-prefs:'+owner,{})
        put(self.db,'paper-prefs:'+owner,dict(prefs,autonomous=True,generation=prefs.get('generation',0)+1))
        self.db.execute('DELETE FROM kv WHERE key=?',('paper-stop-reason:'+owner,))
        self.activity(owner,'agent_started','Paper entries enabled subject to live loan, model and market checks')
    def settle(self,owner,confirmed):
        if confirmed is not True: raise ValueError('Confirm closure of all paper positions')
        l=Lending(self.db).loan(owner)
        if l and l['state']=='AWAITING_CHAIN' and not self.getPosition(owner) and not self.db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchone():
            raise ValueError('No active settlement; confirm the reconciled vault settlement')
        if not l or l['state'] in ('REPAID','CANCELLED','AWAITING_CHAIN'):
            self.pause_entries(owner);put(self.db,'direct-settlement:'+owner,True);self.close_for_settlement(owner);return
        if l['state'] not in ('ACTIVE','CLOSING','SHORTFALL'): raise ValueError('No active settlement')
        v=Lending(self.db).vault(owner,False)
        if v['loanState']!=2 or v['loanId']!=l['id']:raise ValueError('Confirm the issued loan on chain before settlement')
        self.pause_entries(owner)
        self.db.execute("UPDATE loans SET state='CLOSING' WHERE id=?",(l['id'],))
        self.close_for_settlement(owner)
    def close_for_settlement(self,owner):
        for p in self.getPosition(owner):
            pending=self.db.execute("SELECT payload FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchall()
            if any(json.loads(r[0]).get('positionId')==p['id'] for r in pending): continue
            try: self.submit(owner,dict(reduceOnly=True,positionId=p['id'],fraction='1',reason='owner_settlement'),'settle:'+p['id']+':'+str(time.time_ns()))
            except ValueError: pass # Stale depth: remain CLOSING; never fabricate a fill.
    def settle_tick(self):
        for row in self.db.execute("SELECT key FROM kv WHERE key LIKE 'direct-settlement:%'").fetchall():
            if not get(self.db,row[0]):continue
            owner=row[0].split(':',1)[1]
            with transaction(self.db):
                self.close_for_settlement(owner)
                if not self.getPosition(owner) and not self.db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchone():put(self.db,row[0],False)
        for row in self.db.execute("SELECT owner FROM loans WHERE state='CLOSING'").fetchall():
            owner=row[0]
            with transaction(self.db):
                self.close_for_settlement(owner)
                if (not get(self.db,'paper-funding-incomplete:'+owner,False)
                        and not self.getPosition(owner) and not self.db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'",(owner,)).fetchone()):
                    Lending(self.db).repay_available(owner)
    def risk_governor(self):
        for row in self.db.execute('SELECT address FROM users').fetchall():
            owner=row[0];prefs=get(self.db,'paper-prefs:'+owner,{})
            if not prefs.get('autonomous'): continue
            try: self.gate(owner)
            except ValueError as exc:
                reason=str(exc)
                action=('Refresh the collateral quote and review loan health; repay debt or add collateral before restarting.' if 'health' in reason.lower() else
                    'Reconcile funding history before restarting.' if 'Funding history' in reason else
                    'Refresh market and collateral data, resolve the reported condition, then select Start AI Trading.' if 'stale' in reason.lower() or 'fresh' in reason.lower() else
                    'Review the vault and trading limits, then select Start AI Trading to authorize a new run.')
                with transaction(self.db): self.pause_entries(owner,reason,action);self.activity(owner,'loan_risk_block',reason)
                # Optional reduction explicitly authorized in the on-chain permission.
                try:
                    v=Lending(self.db).vault(owner)
                    if v['limits']['autoReduce'] and D(Lending(self.db).health(owner)['healthFactor'])<D(RISK['blockHealthBelow']):
                        with transaction(self.db): self.settle(owner,True)
                except ValueError: pass

def agents_for(db,model,paper):
    class FinancialContext:
        owner=None
        @property
        def ready(self):return model.ready
        @property
        def model(self):return getattr(model,'model','configured')
        def ask(self,role,evidence,schema):
            from .repayment import Repayment
            lending=Lending(db);snap=paper.snapshot(self.owner)
            has_vault=db.execute('SELECT 1 FROM vaults WHERE owner=?',(self.owner,)).fetchone()
            context=dict(loanHealth=lending.health(self.owner) if has_vault and lending.quote(self.owner) else None,
                trading={k:snap[k] for k in ('equity','available','unrealized','totals','positions','costs')},
                repayment=Repayment(db,paper).status(self.owner),limits={k:str(v) for k,v in paper.gate(self.owner).items()})
            style=paper.strategy(self.owner)
            return model.ask(role,dict(evidence,financialPosition=context,tradingStyle=style),research_schema(style['id']) if role=='research' else schema)
    contextual=FinancialContext()
    def eligible(u):
        try: paper.gate(u['address']);contextual.owner=u['address'];return True
        except ValueError:return False
    def markets(u):
        if u['address'].startswith('paper:'):
            return [get(db,'paper-prefs:'+u['address'],{}).get('symbol','BTC/USD')]
        return ['BTC/USD','ETH/USD','SOL/USD']
    return Agents(db,contextual,eligible=eligible,markets=markets,
        entry_gate=lambda db,s:s in paper.feed.markets and paper.feed.markets[s].fresh(),
        revision=lambda u:[get(db,'paper-prefs:'+u['address'],{}).get('generation',0),paper.revision(u['address'])],
        strategy_profile=lambda u:paper.strategy(u['address']),
        validate_spec=lambda u,s,markets:validate_strategy(s,markets,paper.strategy(u['address'])['id']))
