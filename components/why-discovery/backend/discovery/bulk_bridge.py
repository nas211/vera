"""Small read-only handoff; broad research never approves live execution."""
import json,os,time
from pathlib import Path
from . import rules

def root():return Path(os.environ.get('WHY_DISCOVERY_BULK_DIR','/var/lib/why-discovery/bulk'))
def read(path,limit=262144):
    if path.stat().st_size>limit:raise ValueError('bulk_snapshot_too_large')
    return json.loads(path.read_text())
def status():
    try:result=read(root()/'status.json')
    except (OSError,ValueError):result=dict(status='NOT_STARTED',evaluated=0,planned=0)
    try:result['forward_simulations']=read(root()/'shadow/status.json')
    except (OSError,ValueError):result['forward_simulations']=dict(status='NOT_STARTED',configurations=0)
    return result

def import_shortlist(engine,user,now,markets,limit=2):
    account=engine.accounts.get(user)
    if not account or not account.get('running') or account.get('kill'):return 0
    try:data=read(root()/'latest.json')
    except (OSError,ValueError):return 0
    if data.get('version')!=1 or data.get('status')!='COMPLETED' or not 0<=now-data.get('completed',0)<=86400 or data.get('live_enabled') is not False:return 0
    imported=0
    for item in data.get('shortlist',[]):
        if imported>=limit:break
        try:
            if item.get('passed') is not True or item['spec']['market'] not in markets:continue
            spec=rules.validate(item['spec'],markets)
            if rules.executable_identity(spec)!=item['fingerprint']:continue
            if engine.known_spec(user,spec):continue
            result=engine.propose(user,spec,dict(origin='PUBLIC_BULK_SCREEN',run_id=data['run_id'],source=item['source'],training=item['train'],later_folds=item['later_folds'],definition='Historical proxy screen; zero fresh paper trades on admission. Not live approval.'))
            if result:imported+=1
        except (ValueError,KeyError,TypeError):continue
    return imported
