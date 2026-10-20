import json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from customer.discovery_gateway import DiscoveryGateway

class DiscoveryReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'paper.sqlite3'
        self.db=sqlite3.connect(self.path);self.db.execute('create table paper_accounts(user_id text primary key,payload text)')
        self.g=DiscoveryGateway(self.path);self.contexts=[SimpleNamespace(account_id='JP',user_id='user-jp')]
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def save(self,data,user='user-jp'):
        self.db.execute('insert or replace into paper_accounts values (?,?)',(user,json.dumps(data)));self.db.commit()
    def reason(self):
        self.assertEqual(self.g.poll(self.contexts),[])
        return self.g.status()['by_account']['JP']['reason']
    def test_missing_is_visible_and_never_uses_another_customers_evidence(self):
        self.save({'running':True,'candidates':{}},'another-user')
        self.assertEqual(self.reason(),'paper_account_missing')
    def test_paused_risk_locked_and_waiting_are_distinct(self):
        for data,reason in [({'running':False},'paper_research_paused'),({'running':True,'kill':True},'paper_risk_locked'),({'running':True},'awaiting_qualified_fresh_setup')]:
            self.save(data);self.assertEqual(self.reason(),reason)
    def test_missing_database_is_unavailable_not_healthy(self):
        self.g=DiscoveryGateway(self.path.with_name('missing.sqlite3'))
        self.assertEqual(self.reason(),'discovery_evidence_unavailable')
    def test_malformed_account_fails_closed(self):
        self.save([]);self.assertEqual(self.reason(),'discovery_evidence_unavailable')
