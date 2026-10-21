from decimal import Decimal as D
import time
import pytest
from vela.paper import PaperAdapter, OrderlyAdapter
from vela.prices import choose, entries_allowed, quote, PriceFeed
from vela.db import connect
from vela.settings import Settings
from vela.agents import Agents, bars_for
from vela.api import create_app
from fastapi.testclient import TestClient


def sig(side="long", **kw):
    return dict(symbol="DOGE/USD", side=side, stop_bps="100", target_bps="150",
                hold=7200, signal_id="test-1", **kw)


def trigger_net(p, settings):
    q, e, stop = map(D, (p["qty"], p["entry"], p["stop"]))
    sign = D(1) if p["side"] == "long" else D(-1)
    return D(p["realized_gross"]) + (stop - e) * q * sign - D(p["paid_fees"]) - stop * q * settings.fee


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("budget", ["2", "1", "200"])
def test_fee_inclusive_budget_both_sides_and_overrides(setup_db, side, budget):
    db, settings, user = setup_db
    settings.loss_budget = D(budget)
    adapter = PaperAdapter(db, settings)
    p = adapter.open(user, sig(side))
    assert trigger_net(p, settings) >= -min(D(2), D(budget))
    assert D(p["stop"]) % D(".00000001") == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_partial_exit_and_existing_position_reconciliation(setup_db, side):
    db, settings, user = setup_db
    adapter = PaperAdapter(db, settings)
    p = adapter.open(user, sig(side))
    db.execute("UPDATE prices SET value=? WHERE symbol='DOGE/USD'", (".199" if side == "long" else ".201",))
    t = adapter.close(p["id"], "partial", D(".4"))
    assert t["pnl_micro"] < 0
    # Simulate a persisted loose stop from an earlier version.
    db.execute("UPDATE positions SET stop=? WHERE id=?", (".01" if side == "long" else "20", p["id"]))
    restarted = PaperAdapter(db, settings)
    restarted.reconcile()
    remaining = restarted.getPosition(user)[0]
    assert trigger_net(remaining, settings) >= -D(2)
    prior = remaining["stop"]
    restarted.reconcile()
    assert restarted.getPosition(user)[0]["stop"] == prior
    closed = restarted.close(p["id"], "stop")
    assert not restarted.getPosition(user)
    total = sum(D(r[0]) for r in db.execute("SELECT pnl FROM trades"))
    assert D(db.execute("SELECT balance FROM users").fetchone()[0]) == D(100) + total
    assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 2
    assert restarted.close(p["id"], "duplicate") is None


def test_fill_costs_and_equity(setup_db):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    assert D(p["entry"]) == D(".2") * D("1.0002")
    t = a.close(p["id"], "test")
    assert D(t["exit"]) == D(".2") * D(".9998")
    assert D(t["fees"]) > 0 and D(t["pnl"]) < 0
    assert D(t["pnl"]) == D(t["gross"]) - D(t["fees"])
    assert not a.getPosition(u)


def test_duplicate_signal_and_stopping_cannot_open(setup_db):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    with pytest.raises(ValueError):
        a.open(u, sig())
    a.close(p["id"], "test")
    with pytest.raises(ValueError):
        a.open(u, sig())
    db.execute("UPDATE users SET status='stopping'")
    with pytest.raises(ValueError):
        a.open(u, dict(sig(), signal_id="test-2"))


def test_stop_waits_for_trade_log_confirmation(setup_db):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    a.open(u, sig())
    db.execute("UPDATE users SET status='stopping'")
    a.tick(Agents(db))
    assert not a.getPosition(u)
    assert db.execute("SELECT count(*) FROM outbox WHERE kind='settle'").fetchone()[0] == 0
    db.execute("UPDATE trades SET chain_status='confirmed'")
    a.tick(Agents(db))
    a.tick(Agents(db))
    assert db.execute("SELECT count(*) FROM outbox WHERE kind='settle'").fetchone()[0] == 1


def test_stale_feed_never_invents_fill(setup_db):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    db.execute("UPDATE prices SET ts=0")
    with pytest.raises(ValueError):
        a.close(p["id"], "stop")
    assert a.getPosition(u)
    assert not entries_allowed(db, "DOGE/USD")


def test_preserve_stricter_stop(setup_db):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    db.execute("UPDATE positions SET stop=? WHERE id=?", (str(D(p["entry"]) - D(".00001")), p["id"]))
    a.reconcile()
    assert D(a.getPosition(u)[0]["stop"]) >= D(p["entry"]) - D(".00001")


def test_prices_primary_fallback_stale_divergence_future():
    now = time.time()
    p = dict(value="100", ts=now, source="pyth")
    b = dict(value="100.5", ts=now, source="binance")
    assert choose(p, b, now)["source"] == "pyth"
    assert choose(None, b, now)["source"] == "binance"
    assert not choose(p, dict(b, value="102"), now)["safe"]
    assert not choose(dict(p, ts=now - 100), None, now)["safe"]
    assert not choose(dict(p, ts=now + 100), None, now)["safe"]
    assert not choose(None, None, now)["safe"]
    assert not choose(dict(p, value="NaN"), None, now)["safe"]


def test_divergence_globally_pauses_entries(setup_db):
    db, s, u = setup_db
    db.execute("UPDATE prices SET safe=0,reason='sources_diverge_over_1pct' WHERE symbol='ETH/USD'")
    assert not entries_allowed(db, "DOGE/USD")


def test_no_live_adapter_or_mainnet_or_production_db(setup_db):
    _, s, _ = setup_db
    with pytest.raises(NotImplementedError):
        OrderlyAdapter()
    with pytest.raises(ValueError):
        Settings(chain_id=1)
    with pytest.raises(ValueError):
        Settings(db_path="/var/lib/why/anything.sqlite")
    with pytest.raises(ValueError):
        Settings(max_leverage=D(100))


def test_no_fake_agent_signals_without_warmup_or_model(setup_db, monkeypatch):
    db, s, u = setup_db
    monkeypatch.delenv("VELA_MODEL_KEY", raising=False)
    user = dict(db.execute("SELECT * FROM users").fetchone())
    assert Agents(db).signal(user) is None
    assert Agents(db).signal(dict(user, strategy=3)) is None
    assert not bars_for(db, "DOGE/USD", time.time())


def test_api_scope_and_no_write_endpoints(setup_db,api_factory):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    a.close(p["id"], "test")
    with TestClient(api_factory(s)) as client:
        assert client.get("/health").json()["mode"] == "testnet-paper"
        response = client.get("/api/users/" + u).json()
        assert response["scope"] == "current session"
        assert len(response["trades"]) == 1
        board = client.get("/api/leaderboard").json()
        assert board["rows"][0]["trades"] == 1
        assert board["rows"][0]["wins"] == 0
        assert "All-time" in board["scope"]
        assert client.post("/api/users/" + u, json={"balance": 100000}).status_code == 405
        assert client.get("/api/users/invalid").status_code == 400
        assert client.get("/.env").status_code == 404


def test_engineering_trade_not_reported_as_strategy_performance(setup_db,api_factory):
    db, s, u = setup_db
    a = PaperAdapter(db, s)
    p = a.open(u, sig())
    a.close(p["id"], "engineering_smoke")
    with TestClient(api_factory(s)) as client:
        assert client.get("/api/leaderboard").json()["rows"] == []
        assert len(client.get("/api/users/" + u).json()["trades"]) == 1


def test_ai_availability_comes_from_worker_not_api_credentials(setup_db,api_factory):
    from vela.db import put
    db, s, _ = setup_db
    with TestClient(api_factory(s)) as client:
        assert len(client.get("/api/strategies").json()) == 1
        assert not client.get("/api/strategies").json()[0]["available"]
        assert not client.get("/api/strategies").json()[0]["modelReady"]
        put(db, "ai_model_configured", True)
        assert client.get("/api/strategies").json()[0]["modelReady"]


@pytest.mark.parametrize("family", ["WY-REVERT", "WY-PULLBACK"])
def test_unified_portfolio_accepts_both_existing_signal_families(setup_db, monkeypatch, family):
    from types import SimpleNamespace
    from vela import agents
    db, _, _ = setup_db
    user = dict(db.execute("SELECT * FROM users").fetchone())
    monkeypatch.setattr(agents, "bars_for", lambda db, symbol, now: [[now, 1, 1, 1, 1]] if symbol == "ETH/USD" else [])
    monkeypatch.setattr(agents.signals, "wy_eval", lambda bars: dict(
        fired=("short", family), atr=10, close=3000, bar_ms=123))
    agents = Agents(db, SimpleNamespace(ready=False))
    result = agents.signal_candidates(time.time())[0]
    assert result["symbol"] == "ETH/USD"
    assert result["side"] == "short"
    assert family in result["signal_id"]
    assert agents.signal(user) is None
    db.execute("UPDATE prices SET safe=0,reason='stale' WHERE symbol='ETH/USD'")
    assert Agents(db, SimpleNamespace(ready=False)).signal(user) is None


def test_connected_model_never_bypasses_review_with_rule_fallback(setup_db, monkeypatch):
    from types import SimpleNamespace
    from vela.db import put
    db, _, user = setup_db
    agents = Agents(db, SimpleNamespace(ready=True))
    monkeypatch.setattr(agents, "signal_candidates", lambda now: [sig()])
    row = dict(db.execute("SELECT * FROM users").fetchone())
    assert agents.signal(row) is None
    put(db, "ai-approved:" + user, {"at":time.time()-301, "spec":{}})
    assert agents.signal(row) is None


def test_pyth_auth_is_never_sent_to_binance(setup_db, monkeypatch):
    import httpx
    db, s, _ = setup_db
    monkeypatch.setenv("VELA_PYTH_API_KEY", "fixture-not-a-real-secret")
    seen = []
    def handler(request):
        seen.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "hermes.pyth.network":
            return httpx.Response(200, json={"parsed":[]})
        return httpx.Response(200, json=[])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    feed = PriceFeed(db, s, client)
    from vela.prices import SYMBOLS
    feed.ids = {symbol:str(i) for i,symbol in enumerate(SYMBOLS)}
    feed.pyth()
    feed.binance()
    assert seen == [("hermes.pyth.network", "Bearer fixture-not-a-real-secret"),
                    ("api.binance.com", None)]
