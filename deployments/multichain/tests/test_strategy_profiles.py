import asyncio
import time
import pytest
import httpx
from decimal import Decimal as D
from vela.db import get,put,transaction
from vela.strategy_profiles import validate,research_schema
from autodefi.trading import agents_for
from autodefi.api import create_app
from conftest import funded,fill,save_vault,ALICE,BOB
from test_api_ai import FixtureModel
from test_persistent_trading import login

@pytest.mark.parametrize('style,hold',[('scalp',300),('swing',7200)])
@pytest.mark.parametrize('side',['long','short'])
def test_chosen_strategy_reaches_model_schema_validator_signal_and_order(env,style,hold,side):
    funded(env);db=env['db'];paper=env['paper'];owner=env['owner']
    class Model(FixtureModel):
        def ask(self,role,evidence,schema):
            assert evidence['tradingStyle']['id']==style
            result=super().ask(role,evidence,schema)
            if role=='research':
                assert schema['properties']['strategies']['items']['properties']['mode']['enum']==[style]
                result['strategies'][0].update(mode=style,side=side,max_hold_seconds=hold)
            if role=='direction':result['side']=side
            return result
    with transaction(db):paper.pause_entries(owner);paper.choose_strategy(owner,dict(confirm=True,strategyStyle=style));paper.start(owner,Model())
    for m in env['feed'].markets.values():
        for c in m.candles:db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,c['ts'],c['o'],c['h'],c['l'],c['c']))
    model=Model();agents=agents_for(db,model,paper)
    for _ in range(3):put(db,'ai-last-call',0);agents.tick_ai()
    assert model.calls==['research','review','direction']
    user=db.execute('SELECT * FROM users WHERE address=?',(owner,)).fetchone();signal=agents.signal(user)
    assert signal['hold']==hold and signal['side']==side
    with transaction(db):paper.open(owner,signal)
    db.execute('UPDATE paper_orders SET due=0');paper.tick()
    p=paper.getPosition(owner)[0];meta=paper.meta(p)
    assert D(meta['riskBudget'])<=2
    assert p['expires']-p['opened']==hold

@pytest.mark.parametrize('style',['scalp','swing'])
def test_profile_rejects_wrong_horizon_and_other_mode(style):
    spec=FixtureModel().ask('research',{},None)['strategies'][0]
    spec.update(mode=style,max_hold_seconds=300 if style=='scalp' else 7200)
    assert validate(spec,{'BTC/USD':{}},style)['mode']==style
    with pytest.raises(ValueError):validate(dict(spec,max_hold_seconds=1),{'BTC/USD':{}},style)
    with pytest.raises(ValueError):validate(dict(spec,mode='swing' if style=='scalp' else 'scalp'),{'BTC/USD':{}},style)
    assert research_schema('swing')['properties']['strategies']['items']['properties']['mode']['enum']==['swing']

@pytest.mark.parametrize('side',['long','short'])
def test_switch_preserves_existing_position_and_tighter_risk_cancels_pending_approval(env,side):
    funded(env);paper=env['paper'];db=env['db'];owner=env['owner']
    p=fill(env,side)
    paper.configure(owner,dict(confirm=True,allocation='800',positionSize='200',maxLoss='.5'))
    paper.protect(p);before=paper.meta(p).copy()
    put(db,'ai-approved:'+owner,dict(spec={},at=time.time()));generation=get(db,'paper-prefs:'+owner)['generation']
    with transaction(db):paper.choose_strategy(owner,dict(confirm=True,strategyStyle='scalp',maxLoss='100'))
    assert get(db,'paper-prefs:'+owner)['generation']>generation
    assert get(db,'ai-approved:'+owner) is None and not paper.run_authorized(owner)
    assert paper.meta(p)==before
    paper.protect(p);assert D(paper.meta(p)['riskBudget'])<=D('.5')
    assert paper.strategy(owner)['id']=='scalp'

def test_expired_paper_permission_visible_and_renewed_without_raising_limits(env):
    funded(env);paper=env['paper'];owner=env['owner'];db=env['db']
    paper.configure(owner,dict(confirm=True,allocation='500',positionSize='100',maxLoss='.5'))
    old=get(db,'trading-permission:'+owner);old['expiresAt']='1';put(db,'trading-permission:'+owner,old)
    assert not paper.setup(owner)['paperRequired'] and paper.setup(owner)['renewalRequired']
    with pytest.raises(ValueError,match='paper trading permission'):paper.gate(owner,require_running=False)
    paper.configure(owner,dict(confirm=True,allocation='500',positionSize='100',maxLoss='2'))
    assert not paper.setup(owner)['paperRequired']
    assert paper.gate(owner,require_running=False)['budget']==D('.5')
    assert not get(db,'paper-prefs:'+owner)['autonomous']

def test_strategy_api_requires_consent_persists_and_is_wallet_scoped(env):
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as c:
            assert (await c.post('/api/trading/strategy',json={'confirm':True,'strategyStyle':'scalp'})).status_code==401
            await login(c,ALICE)
            assert (await c.post('/api/trading/strategy',json={'strategyStyle':'scalp'},headers={'Idempotency-Key':'no-confirm-profile'})).status_code==400
            r=await c.post('/api/trading/strategy',json={'confirm':True,'strategyStyle':'scalp'},headers={'Idempotency-Key':'confirm-profile-123'})
            assert r.status_code==200,r.text
            a=(await c.get('/api/account')).json();assert a['tradingSetup']['strategy']['id']=='scalp' and a['tradingSetup']['paperRequired']
            await login(c,BOB);assert (await c.get('/api/account')).json()['tradingSetup']['strategy']['id']=='swing'
    try:asyncio.run(run())
    finally:app.state.db.close()

def test_strategy_switch_during_model_call_cannot_publish_previous_profile(env):
    funded(env);db=env['db'];paper=env['paper'];owner=env['owner']
    for m in env['feed'].markets.values():
        for c in m.candles:db.execute('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',(m.symbol,c['ts'],c['o'],c['h'],c['l'],c['c']))
    class SwitchDuringResearch(FixtureModel):
        def ask(self,role,evidence,schema):
            result=super().ask(role,evidence,schema)
            paper.pause_entries(owner)
            paper.choose_strategy(owner,dict(confirm=True,strategyStyle='scalp'))
            return result
    agents=agents_for(db,SwitchDuringResearch(),paper);agents.tick_ai()
    assert get(db,'ai-stage:'+owner) is None
    assert get(db,'ai-approved:'+owner) is None
    assert not get(db,'paper-prefs:'+owner)['autonomous']
    assert paper.strategy(owner)['id']=='scalp'

@pytest.mark.parametrize('side',['long','short'])
def test_running_strategy_change_is_rejected_without_changing_positions_or_consent(env,side):
    funded(env);paper=env['paper'];db=env['db'];owner=env['owner'];p=fill(env,side)
    prefs=get(db,'paper-prefs:'+owner).copy();position=paper.getPosition(owner);meta=paper.meta(p).copy()
    changes=db.total_changes
    with pytest.raises(ValueError,match='Stop trading to change strategy'):
        paper.choose_strategy(owner,dict(confirm=True,strategyStyle='scalp'))
    assert db.total_changes==changes
    assert get(db,'paper-prefs:'+owner)==prefs
    assert paper.getPosition(owner)==position and paper.meta(p)==meta
    paper.pause_entries(owner)
    paper.choose_strategy(owner,dict(confirm=True,strategyStyle='scalp'))
    assert paper.strategy(owner)['id']=='scalp' and paper.meta(p)==meta

def test_stale_tab_cannot_change_strategy_after_another_tab_starts(env):
    funded(env)
    app=create_app(env['config'],feed=env['feed'],model=FixtureModel())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url=env['config'].origin,headers={'Origin':env['config'].origin,'X-ADP-Client':'web'}) as c:
            await login(c,ALICE)
            r=await c.post('/api/trading/strategy',json={'confirm':True,'strategyStyle':'scalp'},headers={'Idempotency-Key':'stale-tab-switch-123'})
            assert r.status_code==400 and 'Stop trading to change strategy' in r.text
            a=(await c.get('/api/account')).json()
            assert a['paper']['preferences']['autonomous'] and a['tradingSetup']['strategy']['id']=='swing'
            assert (await c.post('/api/trading/stop',json={})).status_code==200
            r=await c.post('/api/trading/strategy',json={'confirm':True,'strategyStyle':'scalp'},headers={'Idempotency-Key':'stopped-switch-123'})
            assert r.status_code==200,r.text
    try:asyncio.run(run())
    finally:app.state.db.close()
