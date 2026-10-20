import threading
import time
import unittest
from customer.paper_worker import PaperWorker
from customer.router import ExecutionRouter

class Paper:
    def __init__(self): self.events=[]; self.done=threading.Event()
    def on_marks(self, marks): self.events.extend(marks)
    def dispatch(self, sig): self.events.append(sig); return 'OPENED'
    def dispatch_close(self, sig): self.events.append(sig); return 'CLOSED'
    def on_disconnect(self, reason): self.events.append(reason); self.done.set()

class TestPaperWorker(unittest.TestCase):
    def test_slow_paper_does_not_block_live_and_marks_preserve_order(self):
        paper=Paper(); entered=threading.Event(); release=threading.Event()
        original=paper.on_marks
        def slow(marks):
            entered.set(); release.wait(2); original(marks)
        paper.on_marks=slow
        worker=PaperWorker(paper)
        class Live:
            def __init__(self): self.marks=[]
            def on_mark(self,*args): self.marks.append(args)
        live=Live();router=ExecutionRouter(live,worker)
        try:
            router.on_mark('BTC',100,1); self.assertTrue(entered.wait(1))
            router.on_mark('BTC',98,2); router.on_mark('BTC',100,3)
            self.assertEqual(len(live.marks),3)
            release.set()
            self.assertEqual(worker.dispatch('signal'),'OPENED')
            self.assertEqual(paper.events,[('BTC',100,1),('BTC',98,2),('BTC',100,3),'signal'])
            worker.on_disconnect('disconnect')
            self.assertTrue(paper.done.wait(1))
            self.assertEqual(paper.events[-1],'disconnect')
        finally:
            release.set();worker.shutdown()

    def test_db_failure_blocks_paper_signals_and_clears_freshness(self):
        p=Paper()
        def fail(_): raise RuntimeError('secret must not appear')
        p.on_marks=fail; worker=PaperWorker(p)
        try:
            worker.on_mark('BTC',1,1)
            self.assertTrue(p.done.wait(1))
            self.assertFalse(worker.status()['ready'])
            with self.assertRaises(RuntimeError):worker.dispatch('signal')
            self.assertNotIn('signal',p.events)
        finally:worker.shutdown()

    def test_overflow_is_visible_and_does_not_block_live(self):
        p=Paper();entered=threading.Event();release=threading.Event()
        def wait(_):entered.set();release.wait(2)
        p.on_marks=wait;worker=PaperWorker(p,max_queue=1)
        try:
            worker.on_mark('BTC',1,1);self.assertTrue(entered.wait(1))
            worker.on_mark('BTC',2,2);worker.on_mark('BTC',3,3)
            self.assertEqual(worker.status()['last_error'],'paper_queue_overflow')
            self.assertFalse(worker.status()['ready'])
        finally:release.set();worker.shutdown()

    def test_failed_paper_is_exposed_in_overall_readiness(self):
        from types import SimpleNamespace
        from customer.readiness import Readiness
        r = Readiness(SimpleNamespace(supabase_configured=False),
                      registry=SimpleNamespace(status=lambda:{'paper':{'ready':False}}),
                      yellow_probe=lambda:True,auth_probe=lambda:(True,'ok'))
        self.assertFalse(r.snapshot()['PAPER_READY'])
        self.assertFalse(r.snapshot()['ready'])
