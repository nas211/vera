import asyncio,time
from types import SimpleNamespace
from autodefi.runtime import Runtime

def make():
    return Runtime(SimpleNamespace(),None,None,None,None,None)

def test_model_error_clears_after_successful_step(monkeypatch):
    r=make();calls=iter([RuntimeError('model down'),None])
    def tick():
        e=next(calls)
        if e:raise e
    r.model_tick=tick
    async def sleep(_):
        if sleep.n:raise asyncio.CancelledError
        sleep.n+=1;assert r.error=='Model step unavailable; no unapproved entries'
    sleep.n=0
    monkeypatch.setattr(asyncio,'sleep',sleep)
    try:asyncio.run(r.model_loop())
    except asyncio.CancelledError:pass
    assert r.error==''

def test_errors_from_independent_loops_are_reported_together():
    r=make();r.errors['model']='Model step unavailable; no unapproved entries';r.errors['chain']='Chain refresh unavailable; entries require fresh confirmations'
    assert r.error=='Model step unavailable; no unapproved entries; Chain refresh unavailable; entries require fresh confirmations'
    r.errors.pop('model');assert r.error.startswith('Chain refresh')

def test_model_failure_backoff_is_bounded_and_resets_after_recovery(monkeypatch):
    r=make();delays=[];records=[]
    outcomes=iter([RuntimeError('private provider detail')]*5+[None,RuntimeError('private provider detail')])
    def tick():
        error=next(outcomes)
        if error:raise error
    r.model_tick=tick
    monkeypatch.setattr('autodefi.runtime.log',lambda *args,**kwargs:records.append(kwargs))
    async def sleep(delay):
        delays.append(delay)
        if len(delays)==7:raise asyncio.CancelledError
    monkeypatch.setattr(asyncio,'sleep',sleep)
    try:asyncio.run(r.model_loop())
    except asyncio.CancelledError:pass
    assert delays==[10,20,40,60,60,5,10]
    assert len(records)==6
    assert all(record['error']=='RuntimeError' for record in records)
    assert 'private provider detail' not in str(records)+r.error
