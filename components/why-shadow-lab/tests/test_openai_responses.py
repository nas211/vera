"""Offline protocol tests; no paid requests and no production SQLite writes."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agents
import lab

class ResponsesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="why-luna-test-")
        self.old=lab.STATE;lab.STATE=Path(self.temp.name);self.db=lab.connect()
        with patch.dict(os.environ,{"WHY_LAB_LLM_KEY":"test-only-key","WHY_LAB_LLM_MODEL":"gpt-6-luna","WHY_LAB_LLM_ENDPOINT":"https://api.openai.com/v1/responses"},clear=True):self.model=agents.Model()
    def tearDown(self):
        self.db.close();lab.STATE=self.old;self.temp.cleanup()
    def response(self,output,status="completed"):
        owner=self
        class Reply:
            headers={"x-ratelimit-limit-tokens":"500000"}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps(dict(status=status,output=output,usage=dict(total_tokens=180))).encode()
        class Opener:
            def open(self,request,timeout):owner.sent=json.loads(request.data);return Reply()
        self.model.opener=Opener()
    def test_reasoning_tool_output_round_trip(self):
        output=[dict(type="reasoning",id="r1",summary=[],encrypted_content="opaque-state"),
                dict(type="function_call",id="fc1",call_id="c1",name="remember",arguments='{"note":"bounded hypothesis"}',status="completed")]
        self.response(output)
        messages=[dict(role="system",content="Shadow only."),dict(role="user",content="Evaluate evidence.")]
        reply=self.model.ask(self.db,messages,agents.tools_for("research"))
        self.assertEqual(reply["tool_calls"][0]["id"],"c1")
        self.assertEqual(self.sent["reasoning"]["effort"],"medium")
        self.assertFalse(self.sent["store"])
        self.assertNotIn("messages",self.sent)
        self.assertTrue(all(tool["strict"] for tool in self.sent["tools"]))
        self.assertEqual(lab.get(self.db,"model_usage")["tokens"],180)
        messages.extend([reply,dict(role="tool",tool_call_id="c1",content='{"saved":true}')])
        payload=self.model.payload(messages,agents.tools_for("research"))
        self.assertEqual(payload["input"][2:4],output)
        self.assertEqual(payload["input"][4],dict(type="function_call_output",call_id="c1",output='{"saved":true}'))
        self.assertNotIn("_response_output",lab.packed(payload))
    def test_text_and_multiple_calls_normalized(self):
        output=[dict(type="function_call",call_id="c1",name="remember",arguments='{"note":"one"}'),
                dict(type="function_call",call_id="c2",name="remember",arguments='{"note":"two"}'),
                dict(type="message",role="assistant",content=[dict(type="output_text",text="Two bounded notes.")])]
        self.response(output)
        result=self.model.ask(self.db,[dict(role="user",content="Remember evidence.")],agents.tools_for("research"))
        self.assertEqual(len(result["tool_calls"]),2)
        self.assertEqual(result["content"],"Two bounded notes.")
    def test_incomplete_response_never_authorizes_actions(self):
        self.response([dict(type="function_call",call_id="c1",name="authorize_paper",arguments="{}")],status="incomplete")
        with self.assertRaisesRegex(RuntimeError,"incomplete_model_response"):
            self.model.ask(self.db,[dict(role="user",content="Consider paper.")],agents.tools_for("paper"))
        self.assertIsNone(lab.get(self.db,"model_last_success"))
        self.assertIsNone(lab.get(self.db,"ai_decision:paper"))
    def test_refusal_never_marks_success(self):
        self.response([dict(type="message",role="assistant",content=[dict(type="refusal",refusal="Cannot act.")])])
        with self.assertRaisesRegex(RuntimeError,"incomplete_model_response"):
            self.model.ask(self.db,[dict(role="user",content="Consider paper.")],agents.tools_for("paper"))
        self.assertIsNone(lab.get(self.db,"model_last_success"))
    def test_unsupported_output_never_marks_success(self):
        self.response([dict(type="web_search_call",id="unsupported")])
        with self.assertRaisesRegex(ValueError,"unsupported model output"):
            self.model.ask(self.db,[dict(role="user",content="Consider evidence.")],agents.tools_for("research"))
        self.assertIsNone(lab.get(self.db,"model_last_success"))
    def test_scope_switch_preserves_books_and_memory_clears_old_quota(self):
        for key,value in {'model_scope':'old-provider','model_quota_events':[dict(ts=lab.clock_ms(),reserved=100,charged=999999)],'provider_cooldown':{'until_ms':lab.clock_ms()+86400000},'pending_turn:research':{'retry_ms':lab.clock_ms()+86400000},'memory:research':[{'note':'retain evidence'}],'model_last_success':{'model':'previous-model'}}.items():lab.put(self.db,key,value)
        self.db.execute("INSERT INTO trades(position_id,book,pnl) VALUES ('existing','research_paper',1.25)")
        agents.prepare_model_scope(self.db,self.model)
        self.assertIsNone(lab.get(self.db,"provider_cooldown"))
        self.assertIsNone(lab.get(self.db,"pending_turn:research"))
        self.assertIsNone(lab.get(self.db,"model_quota_events"))
        self.assertIsNone(lab.get(self.db,"model_last_success"))
        self.assertEqual(lab.get(self.db,"memory:research"),[{'note':'retain evidence'}])
        self.assertEqual(self.db.execute('SELECT SUM(pnl) FROM trades').fetchone()[0],1.25)
        self.assertEqual(len(lab.get(self.db,"model_usage_archive")),1)
        lab.put(self.db,"model_usage",dict(tokens=250));agents.prepare_model_scope(self.db,self.model)
        self.assertEqual(lab.get(self.db,"model_usage")["tokens"],250)
        self.assertNotIn(self.model.key,lab.packed(lab.get(self.db,"model_scope")))
    def test_resumed_turn_replays_reasoning_and_tool_outputs(self):
        outputs=[dict(type="reasoning",id="r1",summary=[],encrypted_content="opaque-state"),
                 dict(type="function_call",call_id="c1",name="remember",arguments='{"note":"retain the lesson"}')]
        self.response(outputs)
        original_ask=self.model.ask;attempts=[]
        def ask(db,messages,tools):
            attempts.append(copy.deepcopy(messages))
            if len(attempts)==2:raise agents.QuotaWait("minute_quota_pacing",lab.clock_ms()+1000)
            if len(attempts)==3:self.response([dict(type="message",role="assistant",content=[dict(type="output_text",text="Lesson recorded.")])])
            return original_ask(db,messages,tools)
        self.model.ask=ask
        self.assertFalse(agents.turn(self.db,"research",self.model))
        saved=lab.get(self.db,"pending_turn:research")
        self.assertEqual(saved["messages"][-2]["_response_output"],outputs)
        self.assertTrue(agents.turn(self.db,"research",self.model))
        self.assertEqual(self.sent["input"][-3:-1],outputs)
        self.assertEqual(self.sent["input"][-1]["call_id"],"c1")
        self.assertEqual(len(lab.get(self.db,"memory:research")),1)

if __name__=="__main__":unittest.main()
