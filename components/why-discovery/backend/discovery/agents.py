"""Slow AI research/review runs outside the fill/exit loop.

Discovery is not restricted to named strategy families. The bounded DSL is the
only executable representation. Context files are observations, never prompts.
"""
import copy
import json
import os
import time
from pathlib import Path
from . import gate, rules, macro
from .engine import uid, round_to, plan_entry, RISK_PER_TRADE, PAPER_TRIAL_LIMIT
from .model import RESEARCH, MACRO_RESEARCH, ASSESSMENT, DIRECTION

def context(path,market,now):
    if not path:raise ValueError('NEWS_CALENDAR_NOT_CONFIGURED')
    p=Path(path)
    try:
        if p.stat().st_size>262144:raise ValueError('CONTEXT_TOO_LARGE')
        data=json.loads(p.read_text())
    except FileNotFoundError:raise ValueError('CONTEXT_FILE_MISSING') from None
    except PermissionError:raise ValueError('CONTEXT_FILE_UNREADABLE') from None
    except OSError:raise ValueError('CONTEXT_READ_FAILED') from None
    except (json.JSONDecodeError,UnicodeError):raise ValueError('CONTEXT_JSON_INVALID') from None
    if not isinstance(data,dict) or not {'as_of','news','calendar','markets'}<=set(data) or set(data)-{'as_of','news','calendar','markets','sources','coverage','macro'} or not rules.finite(data['as_of']) or not 0<=now-data['as_of']<=300:raise ValueError('CONTEXT_STALE_OR_INVALID')
    if not isinstance(data['markets'],dict):raise ValueError('CONTEXT_SCHEMA')
    if not isinstance(data['news'],list) or not isinstance(data['calendar'],list) or len(data['news'])>100 or len(data['calendar'])>500:raise ValueError('CONTEXT_SCHEMA')
    for item in data['news']:
        if not isinstance(item,dict) or set(item)!={'title','source','published_at','sentiment'}:raise ValueError('NEWS_SCHEMA')
        if not all(isinstance(item[k],str) and 1<=len(item[k])<=1000 for k in ('title','source')) or not rules.finite(item['published_at']) or not rules.finite(item['sentiment']) or not -1<=item['sentiment']<=1:raise ValueError('NEWS_SCHEMA')
        if not 0<=now-item['published_at']<=86400:raise ValueError('NEWS_STALE')
    for item in data['calendar']:
        if not isinstance(item,dict) or set(item)!={'title','time','impact'} or not isinstance(item['title'],str) or not rules.finite(item['time']) or item['impact'] not in ('low','medium','high'):raise ValueError('CALENDAR_SCHEMA')
    market_context=data['markets'].get(market)
    if not isinstance(market_context,dict) or not {'funding_shock','oi_shock','sentiment'}<=set(market_context) or set(market_context)-{'funding_shock','oi_shock','sentiment','evidence'} or any(type(market_context[k]) is not bool for k in ('funding_shock','oi_shock')) or not rules.finite(market_context['sentiment']) or not -1<=market_context['sentiment']<=1:raise ValueError('MARKET_CONTEXT_MISSING')
    evidence=market_context.get('evidence')
    if evidence is not None:
        if not isinstance(evidence,dict) or not rules.finite(evidence.get('funding_at')) or not rules.finite(evidence.get('oi_at')) or not 0<=now-evidence['funding_at']<=300 or not 0<=now-evidence['oi_at']<=900:raise ValueError('MARKET_CONTEXT_STALE')
    return data

def news_rules(data,market,now):
    row=data['markets'][market]
    if any(e['impact']=='high' and abs(now-e['time'])<900 for e in data['calendar']):return dict(verdict='delay',reason='High-impact calendar window',sentiment=row['sentiment'])
    if row['funding_shock'] or row['oi_shock']:return dict(verdict='delay',reason='Funding or open-interest shock',sentiment=row['sentiment'])
    return dict(verdict='confirm',reason='Calendar and shock rules passed',sentiment=row['sentiment'])

class Agents:
    def __init__(self,engine,model,stop):
        self.e=engine;self.model=model;self.stop=stop;self.cursor=0;self.last_research=0
        self.context_path=os.environ.get('WHY_DISCOVERY_CONTEXT_FILE','')
        self.last_context_check=0
        with engine.lock:model.requests=engine.store.meta('model_requests',[])
    def ask(self,*args):
        try:return self.model.ask(*args)
        finally:
            with self.e.lock:self.e.store.put_meta('model_requests',self.model.requests)
    def research_environment(self,market,now):
        try:
            data=context(self.context_path,market,now)
            result=macro.research_view(data.get('macro'),now)
            result['crypto_news']=data['news'][:12]
            result['upcoming_calendar']=[event for event in data['calendar'] if now-900<=event['time']<=now+86400 and event['impact'] in ('high','medium')][:30]
            return result
        except (ValueError,TypeError,KeyError):return macro.research_view(None,now)
    def research_call(self,role,evidence):
        environment=evidence['macro_context'];has_macro=bool(environment.get('evidence_ids'))
        evidence['macro_task']=macro.GUIDANCE+(' Return macro_analysis with a concise scenario assessment, evidence IDs from macro_context, alternative explanation, invalidation, holding-horizon fit and limitations. An unclear or irrelevant macro environment is a valid assessment; do not force a directional call.' if has_macro else ' Macro evidence is unavailable. Do not claim a geopolitical or oil-price justification. Research may use the supplied crypto measurements alone.')
        output=self.ask(role,evidence,MACRO_RESEARCH if has_macro else RESEARCH)
        if has_macro:macro.validate_analysis(output.get('macro_analysis'),environment)
        return output
    def run(self):
        while not self.stop.wait(2):
            try:self.once()
            except Exception:
                with self.e.lock:self.e.ai_error='AGENT_ERROR_RETRYING'
    def once(self):
        now=time.time();e=self.e
        if now-self.last_context_check>=30:
            self.last_context_check=now
            with e.lock:market=next(iter(sorted(e.markets)),'')
            try:
                evidence=context(self.context_path,market,now)
                status=dict(ready=True,as_of=evidence['as_of'],markets=len(evidence['markets']),coverage=evidence.get('coverage','Configured context source'))
                view=macro.research_view(evidence.get('macro'),now)
                status['macro']=dict(status=view['status'],geopolitical_reports=len(view.get('geopolitics',{}).get('reports',[])),oil_futures=len(view.get('oil',{}).get('futures',[])),oil_spot=len(view.get('oil',{}).get('spot',[])))
            except Exception as ex:status=dict(ready=False,reason=str(ex) if isinstance(ex,ValueError) else 'CONTEXT_CHECK_FAILED')
            with e.lock:e.context_status=status
        with e.lock:
            e.ai_ready=self.model.ready;e.ai_model=self.model.model
            if not self.model.ready:e.ai_error='AI_NOT_CONFIGURED';return
            users=[u for u,a in e.accounts.items() if a['running'] and not a['kill']]
            for u in users:e.expire_inactive(u,now)
            e.commit()
            todo=sorted([(c['review'].get('at',0),u,c['id']) for u in users for c in e.accounts[u]['candidates'].values() if c['state'] in ('REVIEW_PENDING','REVIEW_DELAYED') and now>=c['review'].get('next_retry_at',c['review'].get('at',0)+60)])
            markets=[m for m in sorted(e.markets) if e.books.fresh(m,now)]
        if not users:return
        if now-getattr(self,'last_bulk_check',0)>=30:
            self.last_bulk_check=now
            from .bulk_bridge import import_shortlist
            with e.lock:
                for u in users:import_shortlist(e,u,now,set(markets))
                e.commit()
        # Operator-selected hybrid users need a fresh review before each later entry.
        # This remains paper research; no live account is activated here.
        try:
            review_users=json.loads(Path('/var/lib/why-discovery/hybrid-review-users.json').read_text())
            if not isinstance(review_users,list):review_users=[]
        except (OSError,ValueError):review_users=[]
        if not todo:
            from .learning import validation
            with e.lock:
                todo=sorted((c.get('review',{}).get('at',0),u,c['id'])
                    for u in users if u in review_users
                    for c in e.accounts[u]['candidates'].values()
                    if c['state']=='APPROVED_PAPER' and not c.get('position') and not c.get('entry')
                    and validation(c['trades'])['qualified']
                    and now-c.get('review',{}).get('at',0)>=60)
        if todo:self.review(*todo[0][1:]);return
        with e.lock:
            queued=[(u,c['id']) for u in users
                if sum(c['state']=='TESTING' for c in e.accounts[u]['candidates'].values())<PAPER_TRIAL_LIMIT
                for c in e.accounts[u]['candidates'].values() if c['state']=='RETIRED'
                and c.get('revision',{}).get('status')=='WAITING_SLOT' and c['spec']['market'] in markets]
            revisions=sorted([(c.get('revision',{}).get('last_attempt',0),u,c['id']) for u in users
                for c in e.accounts[u]['candidates'].values() if c['state']=='RETIRED'
                and not c.get('revision',{}).get('child') and c.get('revision',{}).get('status')!='WAITING_SLOT' and c['spec']['market'] in markets
                and now-c.get('revision',{}).get('last_attempt',0)>=1800])
        for item in queued:
            if self.retest(*item):return
        if revisions and now-self.last_research>=180:
            self.last_research=now;self.revise(revisions[0][1],revisions[0][2]);return
        if not markets or now-self.last_research<180:return
        # Round-robin across fresh subscribed markets, not a top-N liquidity ranking.
        batch=markets # PublicFeed rotates idle subscriptions through the full universe.
        with e.lock:
            evidence={m:e.books.features(m,time.time()) for m in batch};evidence={m:f for m,f in evidence.items() if f}
            users=[u for u in users if sum(c['state']=='TESTING' for c in e.accounts[u]['candidates'].values())<PAPER_TRIAL_LIMIT]
        if not evidence or not users:return
        # Empirical memory belongs to one account. Never combine customer
        # histories into a shared model prompt or distribute its response.
        with e.lock:
            u=min(users,key=lambda user:e.accounts[user].get('last_research_at',0))
            history=e.research_memory(u,set(evidence));e.accounts[u]['last_research_at']=now;e.dirty=True;e.commit()
        users=[u]
        self.last_research=now
        try:
            environment=self.research_environment(next(iter(evidence)),now)
            output=self.research_call('strategy',dict(as_of=now,markets=evidence,empirical_memory=history,macro_context=environment,task='Create up to three original testable setups from these market observations and prior valid trial outcomes. Do not repeat failed executable settings under a new name. Explain how proposed changes relate to measured outcomes, distinguish hypotheses from evidence. No setup is acceptable when evidence is weak. Rule thresholds are numbers; book/trade imbalance range -1..1, range position 0..1, volume ratio positive. Stop and target are basis points. Ten fresh screening trades precede independent forward paper validation.'))
            if not isinstance(output['reason'],str) or not isinstance(output['strategies'],list) or len(output['strategies'])>3:raise ValueError('AI_SCHEMA')
            specs=[rules.validate(s,evidence) for s in output['strategies']]
            for s in specs:
                if any(condition['feature'] not in evidence[s['market']] for condition in s['conditions']):raise ValueError('AI_MISSING_FEATURE')
            with e.lock:
                e.ai_error='';e.last_scan=now;e.scanned.update(evidence)
                e.log(u,uid(),'research_context',dict(as_of=now,context=environment,analysis=output.get('macro_analysis'),model=self.model.model,proposals=len(specs)))
                for u in users:
                    if not e.accounts[u]['running'] or e.accounts[u]['kill']:continue
                    for spec in specs:e.propose(u,spec,dict(as_of=now,model=self.model.model,features=evidence[spec['market']],reason=output['reason'][:1000],macro_context_id=environment.get('context_id'),macro_analysis=output.get('macro_analysis')))
                e.commit()
        except Exception:
            with e.lock:e.ai_error='AI_RESEARCH_FAILED_OR_RATE_LIMITED'
    def revise(self,u,cid):
        e=self.e;now=time.time()
        with e.lock:
            a=e.accounts[u];original=a['candidates'][cid]
            if not a['running'] or a['kill'] or original['state']!='RETIRED' or original.get('revision',{}).get('child'):return
            f=e.books.features(original['spec']['market'],now)
            if not f:return
            original['revision'].update(status='ANALYZING',last_attempt=now)
            e.dirty=True;e.commit();c=copy.deepcopy(original);history=e.research_memory(u,{original['spec']['market']})
        # Feed-invalidated and owner-closed trials cannot diagnose a strategy.
        eligible=[t for t in c['trades'] if t.get('valid',True) and t.get('reason')!='OWNER_CLOSE']
        if c.get('retirement',{}).get('phase')=='FORWARD_PAPER':
            eligible=[t for t in eligible if t.get('phase')=='FORWARD_PAPER'][-10:]
        else:eligible=eligible[:10]
        reasons={reason:sum(t.get('reason')==reason for t in eligible) for reason in {t.get('reason') for t in eligible}}
        try:
            environment=self.research_environment(c['spec']['market'],now)
            output=self.research_call('strategy_revision',dict(as_of=now,strategy=c['spec'],phase=c.get('retirement',{}).get('phase','PAPER_SCREEN'),retirement_reason=c.get('retirement',{}).get('reason'),score=rules.metrics(eligible),trades=eligible,macro_context=environment,
                exit_reasons=reasons,features=f,market=c['spec']['market'],empirical_memory=history,
                task='Analyze why this retired paper version failed using ONLY these valid trials. Current macro context informs the next hypothesis, not retrospective causes of past losses. Distinguish evidence from hypotheses. Propose at most one version with changed executable settings for the SAME market and mode. Explain the failure hypothesis, changed settings and uncertainty in reason. Return no strategy if a defensible change is unavailable. The original version remains retired; a child must earn ten entirely new paper trades. Do not claim the change will be profitable.'))
            if not isinstance(output['reason'],str) or not isinstance(output['strategies'],list) or len(output['strategies'])>1:raise ValueError('AI_REVISION_SCHEMA')
            proposal=rules.validate(output['strategies'][0],{c['spec']['market']}) if output['strategies'] else None
            if proposal and any(condition['feature'] not in f for condition in proposal['conditions']):raise ValueError('AI_MISSING_FEATURE')
            with e.lock:
                a=e.accounts[u];original=a['candidates'][cid]
                revision=original['revision'];revision.update(reason=output['reason'][:2000],model=self.model.model,analyzed_at=now,valid_trials=len(eligible))
                revision.update(macro_context_id=environment.get('context_id'),macro_analysis=output.get('macro_analysis'))
                e.log(u,c['chain'],'research_context',dict(as_of=now,context=environment,analysis=output.get('macro_analysis'),model=self.model.model,revision_of=c['version']))
                if proposal:
                    if (proposal['market'],proposal['mode'])!=(c['spec']['market'],c['spec']['mode']):raise ValueError('REVISION_MARKET_OR_MODE_CHANGED')
                    if rules.same_settings(proposal,c['spec']):raise ValueError('REVISION_SETTINGS_UNCHANGED')
                    revision.update(status='WAITING_SLOT',proposal=proposal,features=f)
                    self.retest(u,cid)
                else:revision['status']='NO_CHANGE'
                e.log(u,c['chain'],'failure_analysis',dict(version=c['version'],revision=copy.deepcopy(revision),mode='paper'));e.ai_error='';e.commit()
        except Exception as ex:
            known={'REVISION_SETTINGS_UNCHANGED','REVISION_MARKET_OR_MODE_CHANGED','AI_MISSING_FEATURE','AI_REVISION_SCHEMA'}
            with e.lock:
                original=e.accounts[u]['candidates'][cid]
                original['revision'].update(status='RETRY',error=str(ex) if str(ex) in known else 'ANALYSIS_UNAVAILABLE_OR_INVALID')
                e.log(u,c['chain'],'failure_analysis_retry',dict(version=c['version'],reason=original['revision']['error'],mode='paper'));e.commit()

    def retest(self,u,cid):
        e=self.e
        with e.lock:
            a=e.accounts[u];c=a['candidates'][cid];revision=c['revision']
            if not a['running'] or a['kill'] or c['state']!='RETIRED' or revision.get('child') or revision.get('status')!='WAITING_SLOT':return
            proposal=revision['proposal'];f=e.books.features(proposal['market'],time.time())
            if not f or any(condition['feature'] not in f for condition in proposal['conditions']):return
            child=e.propose(u,proposal,dict(as_of=revision['analyzed_at'],model=revision['model'],features=revision['features'],reason=revision['reason'],parent_version=c['version'],macro_context_id=revision.get('macro_context_id'),macro_analysis=revision.get('macro_analysis')),parent=cid)
            if child:
                e.log(u,c['chain'],'retest_queued',dict(parent_version=c['version'],new_version=child[:12],mode='paper'))
            elif e.known_spec(u,proposal):
                revision.update(status='NO_NEW_VERSION',reason=revision['reason']+' Proposed settings already exist.')
                e.dirty=True
            e.commit();return bool(child) or revision['status']=='NO_NEW_VERSION'

    def save_review(self,u,cid,review,state):
        with self.e.lock:
            c=self.e.accounts[u]['candidates'][cid]
            # A slow review may finish after the owner pauses or the risk gate
            # locks the account. Save evidence but do not grant a new approval.
            a=self.e.accounts[u]
            if state=='APPROVED_PAPER' and (not a['running'] or a['kill']):
                state='REVIEW_DELAYED';review=dict(review,reason='ACCOUNT_PAUSED_OR_LOCKED')
            if state=='REVIEW_DELAYED':
                delay=300 if review.get('reason','').startswith(('CONTEXT_','NEWS_CALENDAR_')) else 60
                review=dict(review,next_retry_at=time.time()+delay)
            c['review']=dict(review,at=time.time(),model=self.model.model);c['state']=state
            self.e.log(u,c['chain'],'review',dict(version=c['version'],state=state,review=c['review'],live_enabled=False));self.e.commit()
    def review(self,u,cid):
        e=self.e;now=time.time()
        with e.lock:
            c=copy.deepcopy(e.accounts[u]['candidates'][cid]);score=rules.metrics(c['trades']);s=c['spec'];f=e.books.features(s['market'],now)
        if not score['qualified']:return
        review=dict(news=None,direction=None,sizing=None,gate=None,reason='')
        try:
            data=context(self.context_path,s['market'],now)
            assessment=news_rules(data,s['market'],now)
            if not f:raise ValueError('MARKET_DATA_STALE')
            if s['mode']=='swing' and assessment['verdict']=='confirm':
                assessment=self.ask('news',dict(strategy=s,context=data,features=f,score=score),ASSESSMENT)
                if assessment.get('verdict') not in ('confirm','delay','veto') or not isinstance(assessment.get('reason'),str) or not rules.finite(assessment.get('sentiment')) or not -1<=assessment['sentiment']<=1:raise ValueError('NEWS_PARSE_DELAY')
            review['news']=assessment
            if assessment['verdict']!='confirm':
                review['reason']=assessment['reason'];self.save_review(u,cid,review,'REVIEW_REJECTED' if assessment['verdict']=='veto' else 'REVIEW_DELAYED');return
            if s['mode']=='scalp':
                aligned=data['markets'][s['market']]['sentiment']*(1 if s['side']=='long' else -1)>=-.25
                call=dict(side=s['side'] if aligned and rules.triggered(s,f) else 'flat',action='open' if aligned and rules.triggered(s,f) else 'hold',reason='Rule-based scalp direction recheck; no LLM on scalp path')
            else:
                # Calls are spaced outside the execution loop.
                if self.stop.wait(2.1):return
                call=self.ask('direction',dict(strategy=s,news=assessment,features=f,score=score,trades=c['trades'],task='Decide whether this frozen strategy side is appropriate now. Return flat/hold when not appropriate. This review cannot change its version or approve live.'),DIRECTION)
            if call.get('side') not in ('long','short','flat') or call.get('action') not in ('open','hold','flip','exit') or not isinstance(call.get('reason'),str):raise ValueError('DIRECTION_PARSE_DELAY')
            review['direction']=call
            if call['side']!=s['side'] or call['action']!='open':raise ValueError('DIRECTION_NOT_ALIGNED_NEW_VERSION_REQUIRED')
            with e.lock:
                now=time.time()
                if not e.books.fresh(s['market'],now):raise ValueError('MARKET_DATA_STALE')
                b=e.books.books[s['market']];plan=plan_entry(s,max(b['bids']),min(b['asks']),e.markets[s['market']])
                review['sizing']=dict(qty=plan['qty'],planned_loss=plan['planned_loss'],per_trade_cap=RISK_PER_TRADE,entry_style=plan['entry_style'])
                intent=gate.Intent(uid(),c['chain'],s['market'],s['side'],plan['qty'],plan['price'],plan['stop'])
                d=e.check(u,c,intent,now);review['gate']=dict(approved=d.approved,checks=d.checks,reason=d.reject_reason)
                review['reason']='All review stages passed. Continue in the independent forward paper phase.' if d.approved else d.reject_reason
            self.save_review(u,cid,review,'APPROVED_PAPER' if d.approved else 'REVIEW_DELAYED')
        except Exception as ex:
            known={'MARKET_CONTEXT_STALE','CONTEXT_FILE_MISSING','CONTEXT_FILE_UNREADABLE','CONTEXT_READ_FAILED','CONTEXT_JSON_INVALID','AI_RATE_LIMIT','AI_REQUEST_FAILED','AI_NOT_CONFIGURED','NEWS_CALENDAR_NOT_CONFIGURED','CONTEXT_STALE_OR_INVALID','CONTEXT_TOO_LARGE','CONTEXT_SCHEMA','NEWS_SCHEMA','NEWS_STALE','CALENDAR_SCHEMA','MARKET_CONTEXT_MISSING','MARKET_DATA_STALE','NEWS_PARSE_DELAY','DIRECTION_PARSE_DELAY','DIRECTION_NOT_ALIGNED_NEW_VERSION_REQUIRED'}
            review['reason']=str(ex) if str(ex) in known else 'REVIEW_EVIDENCE_OR_AI_UNAVAILABLE'
            self.save_review(u,cid,review,'REVIEW_DELAYED')
