import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab
import agents

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="why-shadow-test-")
        self.old=lab.STATE;lab.STATE=Path(self.temp.name);self.db=lab.connect()
        self.spec=dict(family="momentum",lookback=6,threshold=.1,side="long",regime="long",hold_bars=6,tp_bps=20,sl_bps=20)
        self.sid=lab.identity(self.spec)
    def tearDown(self):
        self.db.close();lab.STATE=self.old;self.temp.cleanup()
    def strategy(self,stage="PAPER",spec=None,metrics=None,age_days=0):
        spec=spec or self.spec;sid=lab.identity(spec);ts=lab.clock_ms()-int(age_days*86400000)
        self.db.execute("insert into strategies values(?,?,?,?,?,?)",(sid,lab.packed(spec),stage,ts,lab.packed(metrics or {"validation":{"t":3},"forward_start_ms":ts}),ts))
        self.db.commit();return sid
    def bars(self,n=300):
        last=lab.clock_ms()//lab.BAR*lab.BAR-lab.BAR
        times=last-np.arange(n-1,-1,-1)*lab.BAR
        close=100*np.exp(np.arange(n)*.0001)
        return np.column_stack([times,close,close*1.001,close*.999,close,np.ones(n)])
    def cached(self,b):
        return dict(usable=np.ones(len(b),dtype=bool),regime=np.zeros(len(b),dtype=int),signals={("momentum",6):np.ones(len(b))})
    def authorize(self):
        now=lab.clock_ms();lab.put(self.db,"ai_decision:direction",{"ts":now})
        lab.put(self.db,"ai_decision:paper",dict(ts=now,pause=False,strategy_ids=[self.sid],books=["research_paper"]))
    def direction(self):return dict(ts=lab.clock_ms(),regime="long",confidence=.8,stale=False)
    def test_features_are_causal(self):
        b=self.bars();full=lab.features(b);prefix=lab.features(b[:250])
        for key,value in prefix["signals"].items():np.testing.assert_allclose(value,full["signals"][key][:250],equal_nan=True)
        np.testing.assert_array_equal(prefix["regime"],full["regime"][:250])
    def test_learned_direction_serializes_after_sufficient_history(self):
        b=self.bars(480);data={"A":b,"B":b.copy(),"C":b.copy()}
        evidence=lab.fit_direction(data,{m:lab.features(bars) for m,bars in data.items()})
        self.assertGreater(evidence["training_labels"],120)
        self.assertIsInstance(evidence["bar_ms"],int)
        self.assertEqual(json.loads(lab.packed(evidence))["bar_ms"],int(b[-1,0]))
    def test_gap_and_partial_invalidated(self):
        b=self.bars();b[220,5]=0;f=lab.features(b)
        self.assertTrue(f["usable"][210]);self.assertFalse(f["usable"][220:].any())
        b=self.bars();b[220:,0]+=lab.BAR;f=lab.features(b)
        self.assertFalse(f["usable"][220:].any())
    def test_backtest_ambiguous_stop_first(self):
        b=self.bars();b[:,1:5]=[100,101,99,100]
        m=lab.backtest(self.spec,{"TEST":b},{"TEST":self.cached(b)},0,1)
        self.assertGreater(m["trades"],10);self.assertEqual(m["win_rate"],0);self.assertLess(m["pnl"],0)
    def test_no_ai_no_entry(self):
        self.strategy();b=self.bars();lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},self.direction(),{"TEST":(float(b[-1,4]),lab.clock_ms())})
        self.assertEqual(self.db.execute("select count(*) from positions").fetchone()[0],0)
    def test_low_confidence_blocks_entry(self):
        self.strategy();self.authorize();b=self.bars();d=self.direction();d["confidence"]=.1
        lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},d,{"TEST":(float(b[-1,4]),lab.clock_ms())})
        self.assertEqual(self.db.execute("select count(*) from positions").fetchone()[0],0)
    def test_restart_and_cost_accounting(self):
        self.strategy();self.authorize();b=self.bars();d=self.direction();q={"TEST":(float(b[-1,4]),lab.clock_ms())}
        lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},d,q);self.db.commit()
        self.assertEqual(self.db.execute("select count(*) from positions").fetchone()[0],1)
        self.db.close();self.db=lab.connect()
        lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},d,q)
        self.assertEqual(self.db.execute("select count(*) from positions").fetchone()[0],1)
        p=self.db.execute("select * from positions").fetchone();expected=lab.mark_net(p,(p["entry"],p["opened_ms"]+1000))
        lab.close_paper(self.db,p,p["entry"],p["opened_ms"]+1000,"TEST");lab.close_paper(self.db,p,p["entry"],p["opened_ms"]+1000,"TEST")
        trades=self.db.execute("select * from trades").fetchall();self.assertEqual(len(trades),1)
        self.assertAlmostEqual(trades[0]["pnl"],expected);self.assertGreater(trades[0]["fees"],0)
    def test_role_boundaries(self):
        self.strategy()
        with self.assertRaises(ValueError):agents.do_tool(self.db,"research","approve_live_paper",{"strategy_ids":[self.sid],"rationale":"unsafe role crossing"})
        with self.assertRaises(ValueError):agents.do_tool(self.db,"critic","review_candidate",{"strategy_id":self.sid,"approve":True,"rationale":"not hard validated"})
        with self.assertRaises(ValueError):agents.checked_spec(dict(self.spec,sl_bps=-5))
    def test_peer_inbox_and_memory(self):
        agents.do_tool(self.db,"feedback","send_advice",{"recipient":"research","advice":"Explore a different family; forward momentum losses warrant scrutiny."})
        self.assertEqual(agents.inbox(self.db,"research")[0]["sender"],"feedback")
        agents.do_tool(self.db,"research","remember",{"note":"Challenge momentum, retain an exploration floor."})
        self.assertEqual(len(lab.get(self.db,"memory:research")),1)
    def test_holdout_not_visible_to_research_feedback(self):
        self.strategy(metrics={"holdout":{"pnl":9999},"validation":{"t":2}})
        self.assertNotIn("holdout",agents.strategy_info(self.db,self.sid,"research")["metrics"])
        self.assertNotIn("holdout",agents.strategy_info(self.db,self.sid,"feedback")["metrics"])
    def test_direction_cannot_invent_confidence(self):
        lab.put(self.db,"direction_evidence",dict(ts=lab.clock_ms(),probabilities={"long":.3,"sideways":.6,"short":.1}))
        result=agents.do_tool(self.db,"direction","publish_regime",{"regime":"long","rationale":"Peer feedback suggests upside, but statistical support is thin."})
        self.assertFalse(result["entries_possible"]);self.assertEqual(result["evidence_bounded_confidence"],.3)
    def test_promotion_needs_forward_data(self):
        self.strategy();lab.put(self.db,"ai_decision:promotion",dict(ts=lab.clock_ms(),strategy_ids=[self.sid]))
        lab.promotion_cycle(self.db)
        self.assertEqual(self.db.execute("select stage from strategies").fetchone()[0],"PAPER")
    def test_valid_promotion_is_paper_only(self):
        self.strategy(age_days=16);now=lab.clock_ms()
        for i in range(112):
            ts=now-(14-i//8)*86400000
            pnl=-.2 if i%8==0 else 1.0
            self.db.execute("insert into trades(position_id,strategy_id,book,market,side,opened_ms,closed_ms,entry,exit,gross,fees,funding_reserve,pnl,reason) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(i),self.sid,"research_paper","TEST","long",ts-1000,ts,100,101,pnl,0,0,pnl,"TEST"))
        lab.promotion_cycle(self.db)
        self.assertEqual(self.db.execute("select stage from strategies").fetchone()[0],"PAPER")
        agents.do_tool(self.db,"promotion","approve_live_paper",{"strategy_ids":[self.sid],"rationale":"Fourteen observed days and adequate independent trades passed all gates."})
        lab.promotion_cycle(self.db)
        self.assertEqual(self.db.execute("select stage from strategies").fetchone()[0],"LIVE_PAPER")
        self.assertFalse(lab.status(self.db)["real_order_capability"])
    def test_model_turn_selects_tools_and_reads_peer(self):
        agents.do_tool(self.db,"feedback","send_advice",{"recipient":"research","advice":"Diversify into range rejection."})
        class FakeModel:
            count=0
            def ask(inner,db,messages,tools):
                inner.count+=1
                if inner.count==1:
                    supplied=json.loads(messages[1]["content"])
                    if supplied["inbox"][0]["sender"]!="feedback":raise AssertionError("peer advice missing")
                    return dict(role="assistant",content=None,tool_calls=[dict(id="t1",type="function",function=dict(name="submit_strategy",arguments=json.dumps({"spec":self.spec,"rationale":"Test a bounded momentum hypothesis against chronological costs."}))),dict(id="t2",type="function",function=dict(name="remember",arguments=json.dumps({"note":"Compare range rejection to momentum next."})))])
                return dict(role="assistant",content="Submitted a hypothesis, retained the peer's advice; no trading authorized.")
        agents.turn(self.db,"research",FakeModel())
        self.assertEqual(self.db.execute("select stage from strategies").fetchone()[0],"RESEARCH")
        self.assertEqual(agents.inbox(self.db,"research"),[])
        self.assertEqual(lab.get(self.db,"last_turn:research")["requests"],2)
    def test_model_endpoint_and_missing_model(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{},clear=True):self.assertFalse(agents.Model().ready)
        with patch.dict(os.environ,{"WHY_LAB_LLM_ENDPOINT":"http://unsafe.example/v1/chat/completions","WHY_LAB_LLM_MODEL":"test"},clear=True):
            with self.assertRaises(ValueError):agents.Model()
    def test_stale_snapshot_gates_quotes(self):
        from unittest.mock import patch
        stale=dict(ts=lab.clock_ms()-20000,feed={"connected":True},forming={"TEST":{"last_ms":lab.clock_ms(),"c":100}})
        with patch.object(Path,"read_text",return_value=json.dumps(stale)):
            quotes,reason=lab.read_quotes();self.assertEqual(quotes,{});self.assertEqual(reason,"stale_or_disconnected_snapshot")
    def test_missing_barrier_recovered_stop_first(self):
        self.strategy();b=self.bars();entry_ts=int(b[-3,0])+1000
        lab.open_paper(self.db,self.sid,self.spec,"research_paper","TEST",(100.,entry_ts))
        b[-2,1:5]=[100,101,99,100]
        lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},self.direction(),{"TEST":(100.,lab.clock_ms())})
        row=self.db.execute("select * from trades").fetchone()
        self.assertIsNotNone(row);self.assertEqual(row["reason"],"STOP_CANDLE_RECONCILIATION");self.assertLess(row["pnl"],0)
    def test_partial_entry_candle_not_replayed(self):
        self.strategy();b=self.bars();entry_ts=int(b[-1,0])+1000
        b[-1,1:5]=[100,101,99,100]
        lab.open_paper(self.db,self.sid,self.spec,"research_paper","TEST",(100.,entry_ts))
        lab.paper_cycle(self.db,{"TEST":b},{"TEST":self.cached(b)},self.direction(),{"TEST":(100.,lab.clock_ms())})
        self.assertEqual(self.db.execute("select count(*) from trades").fetchone()[0],0)
        self.assertEqual(self.db.execute("select count(*) from positions").fetchone()[0],1)
    def test_proxy_skipped_samples_not_counted(self):
        fn=lab.load_screen();hist=np.zeros((2,65));hist[1,40]=30;counts=np.array([0.,30.]);weights=np.ones(2)
        ids=np.full(3,-1,dtype=np.int64);scores=np.zeros(3)
        n=fn(hist,counts,2,0,12800,65,15.,weights,3,ids,scores)
        self.assertEqual(n,6400);self.assertTrue((ids>=6400).all())
    def test_luna_selected_but_key_required(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{},clear=True):
            model=agents.Model()
            self.assertEqual(model.model,"gpt-6-luna")
            self.assertEqual(model.endpoint,"https://api.openai.com/v1/responses")
            self.assertTrue(model.responses)
            self.assertFalse(model.ready)
    def test_minute_pacing_before_network(self):
        budget=lab.CFG["ai"]["max_tokens_per_minute"]
        first=agents.reserve_request(self.db,budget-1000)
        with self.assertRaises(agents.QuotaWait) as caught:agents.reserve_request(self.db,2000)
        self.assertEqual(caught.exception.reason,"minute_quota_pacing")
        self.assertEqual(lab.get(self.db,"model_usage")["requests"],1)
        agents.settle_request(self.db,first,500)
        self.assertEqual(lab.get(self.db,"model_usage")["tokens"],500)
        # Minute pacing keeps the requested reserve, even if actual usage was lower.
        with self.assertRaises(agents.QuotaWait):agents.reserve_request(self.db,2000)
    def test_daily_token_limit_is_rolling(self):
        now=lab.clock_ms()
        lab.put(self.db,"model_quota_events",[dict(ts=now-120000,reserved=100,charged=lab.CFG["ai"]["max_tokens_per_day"]-10,estimated=False)])
        with self.assertRaises(agents.QuotaWait) as caught:agents.reserve_request(self.db,100)
        self.assertEqual(caught.exception.reason,"rolling_daily_token_limit")
        self.assertEqual(caught.exception.retry_ms,now-120000+86400000)
    def test_provider_retry_and_remaining_tokens(self):
        agents.provider_headers(self.db,{"retry-after":"12"},429)
        with self.assertRaises(agents.QuotaWait) as caught:agents.reserve_request(self.db,100)
        self.assertEqual(caught.exception.reason,"provider_rate_limit")
        self.assertEqual(agents.duration_ms("2m59.56s"),179560)
        self.db.execute("delete from kv where key='provider_cooldown'")
        agents.provider_headers(self.db,{"x-ratelimit-remaining-tokens":"50","x-ratelimit-reset-tokens":"10s"})
        with self.assertRaises(agents.QuotaWait) as caught:agents.reserve_request(self.db,100)
        self.assertEqual(caught.exception.reason,"provider_token_pacing")
    def test_groq_payload_and_usage_mock_only(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{"WHY_LAB_LLM_KEY":"test-only-not-a-real-key","WHY_LAB_LLM_MODEL":"openai/gpt-oss-120b","WHY_LAB_LLM_ENDPOINT":"https://api.groq.com/openai/v1/chat/completions"},clear=True):model=agents.Model()
        class Response:
            headers={"x-ratelimit-limit-tokens":"8000"}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"Shadow only."}}],"usage":{"total_tokens":180}}).encode()
        class Opener:
            payload=None
            def open(inner,request,timeout):
                self.assertEqual(request.get_header("User-agent"),"why-shadow-lab/1.0")
                inner.payload=json.loads(request.data);return Response()
        opener=Opener();model.opener=opener
        model.ask(self.db,[{"role":"user","content":"Identify your shadow-only constraint."}],agents.tools_for("paper"))
        self.assertEqual(opener.payload["model"],"openai/gpt-oss-120b")
        self.assertEqual(opener.payload["reasoning_effort"],lab.CFG["ai"]["reasoning_effort"])
        self.assertFalse(opener.payload["include_reasoning"])
        self.assertNotIn("reasoning_format",opener.payload)
        self.assertEqual(lab.get(self.db,"model_usage")["tokens"],180)
    def test_provider_error_is_redacted_and_backoff_preserved(self):
        from unittest.mock import patch
        import io
        import urllib.error
        test_key="test-only-secret"
        with patch.dict(os.environ,{"WHY_LAB_LLM_KEY":test_key},clear=True):model=agents.Model()
        class Opener:
            def open(inner,request,timeout):
                content=json.dumps({"error":{"message":"Quota exhausted. "+test_key,"code":"rate_limit_exceeded"}}).encode()
                raise urllib.error.HTTPError(model.endpoint,429,"rate limit",{"retry-after":"465"},io.BytesIO(content))
        model.opener=Opener()
        with self.assertRaises(agents.QuotaWait):model.ask(self.db,[{"role":"user","content":"Shadow only."}],agents.tools_for("paper"))
        saved=lab.get(self.db,"model_last_error")
        self.assertNotIn(test_key,lab.packed(saved));self.assertIn("[REDACTED]",saved["detail"]["message"])
        self.assertGreater(lab.get(self.db,"provider_cooldown")["until_ms"]-lab.clock_ms(),460000)
    def test_resume_agent_turn_after_quota_pause(self):
        class PacedModel:
            count=0
            def ask(inner,db,messages,tools):
                inner.count+=1
                if inner.count==1:return dict(role="assistant",content=None,tool_calls=[dict(id="p1",type="function",function=dict(name="submit_strategy",arguments=json.dumps({"spec":self.spec,"rationale":"A bounded hypothesis to validate, without real orders."})))])
                if inner.count==2:raise agents.QuotaWait("minute_quota_pacing",lab.clock_ms()+60000)
                return dict(role="assistant",content="Hypothesis recorded; awaiting exact validation.")
        model=PacedModel()
        self.assertFalse(agents.turn(self.db,"research",model))
        self.assertIsNotNone(lab.get(self.db,"pending_turn:research"))
        self.assertTrue(agents.turn(self.db,"research",model))
        self.assertIsNone(lab.get(self.db,"pending_turn:research"))
        self.assertEqual(self.db.execute("select count(*) from strategies").fetchone()[0],1)

if __name__=="__main__":unittest.main()
