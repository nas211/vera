"""Runner control over mocked HTTP; never calls the deployed app or uses funds."""
import json

import httpx
import pytest

from tools import verify_deployed_paper as runner


def attach_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(runner.httpx, 'Client', lambda **kw: client(
        transport=httpx.MockTransport(handler), **kw))


def position_snapshot():
    return {'paper': dict(positions=[dict(id='test-position', qty='1', mark='100', riskBudget='1')],
                          orders=[], trades=[], unrealized='-1')}


@pytest.mark.parametrize('command', ['marker', 'signal'])
def test_manual_close_overrides_loss_and_sends_only_once(tmp_path, monkeypatch, command):
    calls = []
    attach_transport(monkeypatch, lambda req: calls.append(req.url.path) or httpx.Response(200, json={}))
    control = runner.Control(tmp_path, 30)
    probe = runner.Probe('https://fixture.invalid', tmp_path/'financed.json', control)
    if command == 'marker':
        control.close_file.touch()
    else:
        control.stop.set()
    requested = set()
    runner.manage_financed_close(probe, position_snapshot(), requested)
    runner.manage_financed_close(probe, position_snapshot(), requested)
    assert calls == ['/api/trading/stop', '/api/trading/positions/test-position/close']
    assert probe.report['closeRequests'][0]['reason'] == 'manual'
    assert probe.report['closeRequests'][0]['status'] == 'ACCEPTED'
    probe.finish()


def test_existing_reduction_is_observed_without_duplicate(tmp_path, monkeypatch):
    calls = []
    attach_transport(monkeypatch, lambda req: calls.append(req.url.path) or httpx.Response(200, json={}))
    control = runner.Control(tmp_path, 30)
    control.close_file.touch()
    probe = runner.Probe('https://fixture.invalid', tmp_path/'financed.json', control)
    snapshot = position_snapshot()
    snapshot['paper']['orders'] = [dict(status='pending', payload=json.dumps(
        dict(reduceOnly=True, positionId='test-position')))]
    runner.manage_financed_close(probe, snapshot, set())
    assert calls == ['/api/trading/stop']
    probe.finish()


def test_uncertain_close_is_recorded_and_never_blindly_replayed(tmp_path, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/close'):
            raise httpx.ReadTimeout('reply lost', request=request)
        return httpx.Response(200, json={})
    attach_transport(monkeypatch, respond)
    control = runner.Control(tmp_path, 30)
    control.stop.set()
    probe = runner.Probe('https://fixture.invalid', tmp_path/'financed.json', control)
    requested = set()
    with pytest.raises(httpx.ReadTimeout):
        runner.manage_financed_close(probe, position_snapshot(), requested)
    runner.manage_financed_close(probe, position_snapshot(), requested)
    assert calls.count('/api/trading/positions/test-position/close') == 1
    assert json.loads(probe.output.read_text())['closeRequests'][0]['status'] == 'UNCONFIRMED'
    probe.finish()


def test_wall_clock_expiry_blocks_new_actions_but_allows_cleanup(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runner.time, 'time', lambda: clock[0])
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 10.0)
    calls = []
    attach_transport(monkeypatch, lambda req: calls.append(req.url.path) or httpx.Response(200, json={}))
    control = runner.Control(tmp_path, 30)
    probe = runner.Probe('https://fixture.invalid', tmp_path/'financed.json', control)
    clock[0] += 31  # Host was suspended; monotonic time did not advance.
    with pytest.raises(RuntimeError, match='wall-clock deadline'):
        probe.post('trading/start')
    probe.post('trading/stop')
    probe.post('settle', {'confirm': True})
    assert calls == ['/api/trading/stop', '/api/settle']
    probe.finish()


def test_preexisting_close_marker_creates_no_authenticated_account(tmp_path, monkeypatch):
    calls = []
    attach_transport(monkeypatch, lambda req: calls.append(req.url.path) or httpx.Response(500))
    (tmp_path/'close-now').touch()
    report = runner.financed_probe('https://fixture.invalid', tmp_path/'financed.json', 30)
    assert not calls
    assert report['status'] == 'INCOMPLETE'
    assert 'close requested' in report['error']


def test_interrupted_stop_probe_preserves_pending_protective_exit(tmp_path, monkeypatch):
    calls, state = [], dict(queued=False, reads=0)
    def respond(request):
        path = request.url.path.removeprefix('/api/')
        calls.append((request.method, path))
        if path == 'health':
            data = dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False,
                        engineReady=True, marketData=True, runtimeError='')
        elif path == 'paper/session':
            data = {'balance': '10000'}
        elif path == 'paper/orders':
            state['queued'] = True
            (tmp_path/'close-now').touch()
            data = {'id': 'entry'}
        elif path == 'paper/account':
            if state['queued']: state['reads'] += 1
            pending = state['queued'] and state['reads'] <= 2
            data = dict(positions=[{'id': 'test-position'}] if pending else [], trades=[],
                        orders=[dict(id='protective', status='pending', payload=dict(
                            reduceOnly=True, positionId='test-position', reason='stop'))] if pending else [],
                        preferences={'autonomous': False})
        else:
            pytest.fail('Unexpected mutation during pending protective close: '+path)
        return httpx.Response(200, json=data)
    attach_transport(monkeypatch, respond)
    report = runner.stop_probe('https://fixture.invalid', tmp_path/'stops.json', 30)
    assert report['status'] == 'INCOMPLETE'
    assert report['cleanup'] == dict(positions=0, pendingOrders=0, autonomous=False)
    assert not any(path.endswith(('/cancel', '/close')) for _, path in calls)


def test_losing_manual_close_finishes_cleanup_without_claiming_profit(tmp_path, monkeypatch):
    calls, state = [], dict(funded=False, started=False, closed=False)
    def respond(request):
        path = request.url.path.removeprefix('/api/')
        calls.append((request.method, path))
        if path == 'health':
            data = dict(mode='PAPER', lending='DEMO', liveEnabled=False, realFunding=False,
                        engineReady=True, marketData=True, runtimeError='')
        elif path == 'auth/nonce':
            data = dict(message='isolated HTTP fixture', nonce='fixture')
        elif path == 'account':
            paper = position_snapshot()['paper']
            paper.update(balance='199' if state['closed'] else '200' if state['funded'] else '0',
                         preferences={'autonomous': False}, totals={})
            if not state['started'] or state['closed']:
                paper['positions'] = []
            if state['closed']:
                paper['trades'] = [dict(pnl='-1', closed=runner.time.time()-20)]
            data = dict(paper=paper, directAllocation='0', events=[],
                        vault={'address': 'sim:fixture'} if state['funded'] else None,
                        agent={'loan': {'id': 'sim-loan:fixture', 'principal': '200'} if state['funded'] else None},
                        repayment={'debt': '200'})
        else:
            data = {}
            if path == 'simulated-vault/borrow': state['funded'] = True
            if path == 'trading/start':
                state['started'] = True
                (tmp_path/'close-now').touch()
            if path.endswith('/close'): state['closed'] = True
        return httpx.Response(200, json=data)
    attach_transport(monkeypatch, respond)
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    report = runner.financed_probe('https://fixture.invalid', tmp_path/'financed.json', 30)
    assert report['status'] == 'INCOMPLETE'
    assert 'without an eligible realized profit sweep' in report['error']
    assert report['closedWithoutSweep']['paper']['trades'][0]['pnl'] == '-1'
    assert not report['cleanup']['paper']['positions']
    assert ('POST', 'settle') in calls and calls[-1] == ('POST', 'logout')
    assert not report.get('profitSweepReceipts')
