import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab
import agents

class GithubShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.previous=lab.STATE;lab.STATE=Path(self.temp.name)
        self.db=lab.connect()
    def tearDown(self):
        self.db.close();lab.STATE=self.previous;self.temp.cleanup()
    def report(self,**changes):
        data=dict(ts=lab.clock_ms(),mode='EXTERNAL_STRATEGY_DRY_RUN',live_promotion_allowed=False,
                  strategies=[dict(name='benchmark',state='running',closed_trades=0,open_trades=0,realized_pnl=0)])
        data.update(changes);(lab.STATE/'github_shadow.json').write_text(json.dumps(data))
    def test_missing_and_stale_reports(self):
        self.assertEqual(lab.github_shadow_status()['state'],'unavailable')
        self.report(ts=0);self.assertEqual(lab.github_shadow_status()['state'],'stale')
    def test_external_report_cannot_enable_promotion(self):
        self.report(live_promotion_allowed=True)
        self.assertEqual(lab.github_shadow_status()['state'],'invalid_report')
        self.assertEqual(self.db.execute('select count(*) from strategies').fetchone()[0],0)
    def test_visible_to_research_and_feedback_without_registry_mutation(self):
        self.report()
        for role in ('research','feedback'):
            context=agents.context(self.db,role)
            benchmark=context['evidence']['external_shadow_benchmarks']
            self.assertEqual(benchmark['state'],'fresh')
            self.assertFalse(benchmark['live_promotion_allowed'])
        self.assertEqual(self.db.execute('select count(*) from strategies').fetchone()[0],0)
