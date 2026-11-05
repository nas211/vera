from decimal import Decimal as D
import pytest
from autodefi.repayment import Repayment
from autodefi.staking import DemoStaking
from vela.db import get,put,transaction
from conftest import funded,fill,order

@pytest.mark.parametrize('side',['long','short'])
def test_missing_funding_blocks_equity_movements_but_allows_partial_and_full_protection(env,side):
    funded(env);position=fill(env,side)
    db=env['db'];paper=env['paper'];owner=env['owner'];lending=env['lending']
    put(db,'paper-funding-incomplete:'+owner,True)
    for fn in [lambda:paper.submit(owner,order(side),'funding-gap-entry'),
               lambda:lending.repay_amount(owner,D('1')),lambda:lending.surplus(owner),
               lambda:DemoStaking(db,paper).mutate(owner,'deposit',{'confirm':True,'amount':'1'})]:
        with pytest.raises(ValueError,match='Funding history is incomplete'):
            with transaction(db):fn()
    assert Repayment(db,paper).free(owner)[0]==0
    with transaction(db):paper.submit(owner,dict(reduceOnly=True,positionId=position['id'],fraction='.5'),'gap-partial')
    db.execute('UPDATE paper_orders SET due=0');paper.tick()
    assert D(paper.getPosition(owner)[0]['qty'])<D(position['qty'])
    with transaction(db):paper.settle(owner,True)
    db.execute('UPDATE paper_orders SET due=0');paper.tick();paper.settle_tick()
    assert not paper.getPosition(owner)
    assert lending.loan(owner)['state']=='CLOSING'
    assert D(lending.loan(owner)['paid'])==0
    assert get(db,'paper-funding-incomplete:'+owner) is True
    assert not get(db,'paper-prefs:'+owner)['autonomous']
    with pytest.raises(ValueError,match='Funding history is incomplete'):
        with transaction(db):lending.repay_available(owner)
