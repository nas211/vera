from decimal import Decimal as D
import pytest
from autodefi.auth import Auth,digest
from autodefi.trading import GovernedPaper
from eth_account.messages import encode_defunct
from vela.db import transaction,put,get
from conftest import ALICE,funded,fill,order


@pytest.mark.parametrize('state',['expired','revoked','missing'])
@pytest.mark.parametrize('side',['long','short'])
def test_expired_session_pauses_entries_without_abandoning_open_positions(env,state,side):
    funded(env);position=fill(env,side)
    auth=Auth(env['db'],env['config'])
    challenge=auth.challenge(ALICE.address,6281971)
    signature=ALICE.sign_message(encode_defunct(text=challenge['message'])).signature.hex()
    token,owner=auth.verify(challenge['nonce'],signature,ALICE.address)
    put(env['db'],'agent-session:'+owner,digest(token))
    paper=GovernedPaper(env['db'],env['config'].paper(),env['feed'],require_session=True)
    if state=='expired':env['db'].execute('UPDATE auth_sessions SET expires=0')
    elif state=='revoked':auth.logout(token)
    else:env['db'].execute("DELETE FROM kv WHERE key=?",('agent-session:'+owner,))
    paper.risk_governor()
    assert not get(env['db'],'paper-prefs:'+owner)['autonomous']
    assert paper.getPosition(owner)
    with pytest.raises(ValueError,match='authenticated trading session'):
        paper.submit(owner,{**order(side), 'symbol':'ETH/USD'},'no-entry-after-expiry')
    with transaction(env['db']):
        paper.submit(owner,dict(reduceOnly=True,positionId=position['id'],fraction='1'),'owner-exit-after-expiry')
    env['db'].execute('UPDATE paper_orders SET due=0');paper.tick()
    assert not paper.getPosition(owner)
