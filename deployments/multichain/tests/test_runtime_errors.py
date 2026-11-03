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
