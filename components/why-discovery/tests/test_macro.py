import copy,datetime as dt,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from discovery import macro
from discovery.agents import Agents,context
from discovery.context_feed import Feed,parse_news
from discovery.engine import Engine
from discovery.store import Store
from discovery.model import MACRO_RESEARCH
from test_discovery import Clock,MARKET,LIMITS,SPEC

NOW=dt.datetime.fromisoformat('2026-10-14T20:00:00+00:00').timestamp()
def stamp(day):return dt.datetime.fromisoformat(day+'T16:00:00+00:00').timestamp()
def quote(benchmark='WTI'):
 return dict(chart=dict(error=None,result=[dict(meta=dict(symbol=macro.OIL[benchmark]['symbol'],currency='USD',instrumentType='FUTURE',regularMarketTime=NOW-900,regularMarketPrice=110,exchangeTimezoneName='America/New_York'),timestamp=[stamp(d) for d in ['2026-10-07','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14']],indicators=dict(quote=[dict(close=[80,85,90,95,100,999])]))]))
def analysis(ids):return dict(regime='unclear',evidence_ids=ids,geopolitical_assessment='Reported shipping risk; not independently confirmed.',oil_assessment='Oil is a dated futures proxy.',hypothesis='Test only if crypto momentum confirms.',alternative='The oil move may reflect contract roll or demand.',invalidation='No continuation in observed crypto momentum.',horizon_fit='Macro is background, not a 90-second timing signal.',limitations='Sparse headline coverage and delayed quotes.')
def fixture(now=NOW):
 row=dict(title='Ceasefire talks after tanker attack',source='https://example.test/news',published_at=now-60,sentiment=0)
 sources={'geo_BBC World':dict(data=[row]),'oil_WTI':dict(data=macro.parse_futures(json.dumps(quote()).encode(),'WTI',NOW))}
 return macro.research_view(macro.build(sources,{},now),now)

class OilTests(unittest.TestCase):
 def test_future_returns_use_completed_prior_days_not_in_progress_close(self):
  row=macro.parse_futures(json.dumps(quote()).encode(),'WTI',NOW)
  self.assertAlmostEqual(row['change_from_previous_close_pct'],10)
  self.assertEqual(row['previous_close_date'],'2026-10-13')
  self.assertEqual(len(row['history']),5)
  self.assertAlmostEqual(row['change_from_five_observations_ago_pct'],37.5)
 def test_mismatched_nonfinite_future_and_stale_quotes_are_rejected(self):
  for field,value in [('symbol','BTC-USD'),('currency','EUR'),('regularMarketPrice',float('nan')),('regularMarketTime',NOW+1),('regularMarketTime',NOW-5401)]:
   data=quote();data['chart']['result'][0]['meta'][field]=value
   with self.subTest(field=field,value=value),self.assertRaises(ValueError):macro.parse_futures(json.dumps(data).encode(),'WTI',NOW)
 def test_misaligned_history_rejected(self):
  data=quote();data['chart']['result'][0]['timestamp'].pop()
  with self.assertRaisesRegex(ValueError,'OIL_HISTORY_SHAPE'):macro.parse_futures(json.dumps(data).encode(),'WTI',NOW)
 def spot(self,label,prices):
  return ("Cushing, OK WTI Spot Price FOB (Dollars per Barrel)<tr><td class='B6'>"+label+"</td>"+''.join("<td class='B3'>"+str(p)+"</td>" for p in prices)+"</tr>").encode()
 def test_spot_dates_and_release_lag_are_preserved(self):
  row=macro.parse_spot(self.spot('2026 Oct- 5 to Oct- 9',[90,91,'','','']),'WTI',NOW)
  self.assertEqual(row['observation_date'],'2026-10-06');self.assertEqual(row['age_days'],8)
  self.assertEqual(row['status'],'delayed_daily_background')
  with self.assertRaisesRegex(ValueError,'OIL_SPOT_TOO_OLD'):macro.parse_spot(self.spot('2026 Sep-21 to Sep-25',[90,91,'','','']),'WTI',NOW)
 def test_spot_year_rollover_and_missing_values(self):
  now=dt.datetime.fromisoformat('2026-01-05T12:00:00+00:00').timestamp()
  row=macro.parse_spot(self.spot('2025 Dec-29 to Jan- 2',[90,'',92,'NA',94]),'WTI',now)
  self.assertEqual(row['observation_date'],'2026-01-02');self.assertEqual(row['previous_observation_date'],'2025-12-31')
 def test_macro_does_not_relabel_stale_quote_or_keep_invalid_spread(self):
  value=fixture();value['as_of']=NOW+5400
  value['oil']['brent_wti_spread']=dict(value=1,evidence_ids=[value['oil']['futures'][0]['id']])
  result=macro.research_view(value,NOW+5400)
  self.assertEqual(result['oil']['futures'],[]);self.assertIsNone(result['oil']['brent_wti_spread']);self.assertEqual(result['status'],'partial')
 def test_missing_or_bad_context_is_unknown_not_neutral(self):
  for value in [None,{},dict(as_of=NOW-301),dict(as_of=NOW+1)]:self.assertEqual(macro.research_view(value,NOW)['status'],'unavailable')
 def test_failed_oil_source_obeys_retry_backoff(self):
  calls=[];clock=[NOW]
  def fail(url):calls.append(url);raise OSError('unavailable')
  feed=Feed('.',fetcher=fail,clock=lambda:clock[0])
  with self.assertRaises(OSError):feed.cached('oil_WTI','url',900,json.loads)
  clock[0]+=60
  with self.assertRaisesRegex(ValueError,'SOURCE_RETRY_BACKOFF'):feed.cached('oil_WTI','url',900,json.loads)
  self.assertEqual(len(calls),1)

class MacroResearchTests(unittest.TestCase):
 def test_topics_are_cited_reports_not_directional_scores(self):
  m=fixture();r=m['geopolitics']['reports'][0]
  self.assertIn('diplomacy',r['topics']);self.assertIn('energy_shipping',r['topics']);self.assertEqual(r['status'],'source_report_not_independently_verified');self.assertNotIn('risk_score',m)
 def test_analysis_requires_real_ids_and_both_available_subjects(self):
  m=fixture();macro.validate_analysis(analysis(m['evidence_ids']),m)
  with self.assertRaisesRegex(ValueError,'MACRO_ANALYSIS_CITATION'):macro.validate_analysis(analysis(['invented']),m)
  with self.assertRaisesRegex(ValueError,'MACRO_ANALYSIS_COVERAGE'):macro.validate_analysis(analysis([m['geopolitics']['reports'][0]['id']]),m)
 def test_creator_receives_context_and_analysis_is_audited_even_without_proposal(self):
  store=Store(':memory:');self.addCleanup(store.db.close);clock=Clock();clock.t=NOW
  e=Engine(store,clock);e.markets={MARKET:LIMITS};e.action('alice','start');e.propose('alice',SPEC,{})
  e.books.books[MARKET]=dict(bids={100:1.},asks={100.02:1.},source=NOW,received=NOW,seq=1)
  m=fixture()
  class Model:
   model='fixture';requests=[];ready=True
   def ask(self,role,evidence,schema):
    self.input=evidence;self.schema=schema
    return dict(strategies=[],reason='No supported setup.',macro_analysis=analysis(evidence['macro_context']['evidence_ids']))
  model=Model();a=Agents(e,model,threading.Event())
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'context.json';path.write_text(json.dumps(dict(as_of=NOW,news=[],calendar=[],markets={MARKET:dict(funding_shock=False,oi_shock=False,sentiment=0)},macro=m)));a.context_path=str(path)
   with patch('discovery.agents.time.time',clock):a.once()
  self.assertEqual(model.schema,MACRO_RESEARCH);self.assertEqual(model.input['macro_context']['oil']['futures'][0]['price'],110)
  events=[x for x in store.events('alice') if x['stage']=='research_context'];self.assertEqual(len(events),1);self.assertEqual(events[0]['data']['analysis']['regime'],'unclear')
  self.assertEqual(store.events('bob'),[])
 def test_revision_receives_macro_and_child_keeps_evidence_reference(self):
  store=Store(':memory:');self.addCleanup(store.db.close);clock=Clock();clock.t=NOW
  e=Engine(store,clock);e.markets={MARKET:LIMITS};e.action('alice','start');cid=e.propose('alice',SPEC,{})
  e.books.books[MARKET]=dict(bids={100:1.},asks={100.02:1.},source=NOW,received=NOW,seq=1)
  c=e.accounts['alice']['candidates'][cid];e.retire('alice',c,{})
  m=fixture()
  class Model:
   model='fixture';requests=[];ready=True
   def ask(self,role,evidence,schema):
    self.input=evidence
    return dict(strategies=[dict(SPEC,max_hold_seconds=7260)],reason='Test changed hold only with crypto confirmation.',macro_analysis=analysis(evidence['macro_context']['evidence_ids']))
  model=Model();a=Agents(e,model,threading.Event())
  with patch.object(a,'research_environment',return_value=m),patch('discovery.agents.time.time',clock):a.revise('alice',cid)
  self.assertEqual(c['revision']['status'],'CREATED');self.assertEqual(c['revision']['macro_context_id'],m['context_id'])
  event=next(x for x in store.events('alice') if x['stage']=='strategy' and x['data']['origin']=='AI_REVISED')
  self.assertEqual(event['data']['evidence']['macro_analysis']['regime'],'unclear')
 def test_invalid_macro_citation_cannot_create_a_strategy(self):
  class Model:
   ready=True;model='fixture';requests=[]
   def ask(self,*args):return dict(strategies=[SPEC],reason='unsupported',macro_analysis=analysis(['fake']))
  store=Store(':memory:');self.addCleanup(store.db.close);e=Engine(store);a=Agents(e,Model(),threading.Event())
  with self.assertRaisesRegex(ValueError,'MACRO_ANALYSIS_CITATION'):a.research_call('strategy',dict(macro_context=fixture()))
if __name__=='__main__':unittest.main()
