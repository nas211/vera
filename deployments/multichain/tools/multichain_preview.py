#!/usr/bin/env python3
"""Activate only the isolated loopback multichain preview. Never touch port 8802."""
import argparse,datetime,fcntl,hashlib,http.client,json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from autodefi.config import Config
if __package__:
    from . import activate_preview as helper
else:
    import activate_preview as helper

RUNTIME=ROOT/'.runtime/multichain'
PORT=8803

def save(path,value):
    temp=path.with_suffix('.tmp')
    with os.fdopen(os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600),'w') as out:json.dump(value,out,indent=2)
    os.replace(temp,path)

def get(path,headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',PORT,timeout=10)
    try:
        connection.request('GET',path,headers=headers or {});response=connection.getresponse();return response.status,response.read(8000000)
    finally:connection.close()

def verify():
    build=ROOT/'site/multichain'
    for route in ('/','/vault'):
        status,body=get(route)
        if status!=200 or body!=(build/'index.html').read_bytes():raise ValueError('Multichain frontend mismatch')
    manifest=json.loads((build/'build-manifest.json').read_text())
    for name in manifest['outputs']:
        status,body=get('/'+name.removeprefix('multichain/'))
        if status!=200 or body!=(ROOT/'site'/name).read_bytes():raise ValueError('Multichain asset mismatch')
    from autodefi.adapters import CHAINS
    for network in CHAINS:
        status,body=get('/api/config',{'X-Vella-Network':network});config=json.loads(body)
        if status!=200 or not config.get('multichain') or {c['id'] for c in config.get('chains',[])}!=set(CHAINS) or config.get('chain',{}).get('id')!=network:raise ValueError('Multichain API mismatch: '+network)
        capabilities=config['chain']['capabilities']
        if any(capabilities.get(name) is not False for name in ('realLending','realStablecoin','liveTrading','crossChainSettlement')):raise ValueError('Real functionality must stay disabled')
        if get('/api/account',{'X-Vella-Network':network})[0]!=401:raise ValueError('Unauthenticated account exposed: '+network)
    status,body=get('/api/health');health=json.loads(body)
    if status!=200 or health.get('mode')!='PAPER' or health.get('lending')!='DEMO' or health.get('liveEnabled') is not False or health.get('realFunding') is not False:raise ValueError('Paper/demo safety verification failed')
    if get('/api/account')[0]!=401:raise ValueError('Unauthenticated account exposed')
    from websockets.sync.client import connect
    with connect('ws://127.0.0.1:8803/ws/markets',origin='http://127.0.0.1:8803',proxy=None,open_timeout=10,close_timeout=2) as ws:
        message=json.loads(ws.recv(timeout=10))
        if message.get('privateData') is not False:raise ValueError('Public market WebSocket failed')
    return dict(checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),http='PASS',assets='PASS',privateAccount='PASS',websocket='PASS',networks=list(CHAINS),
        freshLighter=health.get('marketData',False),modelConfigured=health.get('modelConfigured',False),
        genuineWalletVerified=False,liveProtectionVerified=False)

def prepare():
    if ROOT!=Path('/root/VellaMultichain'):raise ValueError('Isolated /root/VellaMultichain worktree required')
    branch=subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()
    if branch!='feature/vella-multichain':raise ValueError('Multichain feature branch required')
    baseline=subprocess.check_output(['git','rev-parse','dogeos-competition-baseline^{commit}'],cwd=ROOT,text=True).strip()
    if baseline!='1d82c1cbb45104a9e2c0b94d8c826949e9e55747':raise ValueError('DogeOS baseline tag changed')
    backup=Path(Path('/root/vella-releases/current-dogeos-backup').read_text().strip())
    if json.loads((backup/'manifest.json').read_text()).get('phase0')!='PASS':raise ValueError('Preservation gate has not passed')
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    envfile=RUNTIME/'preview.env'
    if not envfile.exists():
        with os.fdopen(os.open(envfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as out:
            out.write('VERA_PROTOCOL_MULTICHAIN=1\nVERA_PROTOCOL_ORIGIN=http://127.0.0.1:8803\nVERA_PROTOCOL_EXECUTION=PAPER\nVERA_PROTOCOL_LENDING=DEMO\nVERA_PROTOCOL_NETWORK_ENABLED=1\nVERA_PROTOCOL_DB='+str(ROOT/'.runtime/lighter-showcase/multichain.sqlite3')+'\n')
    # Never inherit either old application's runtime credentials or overrides.
    for key in list(os.environ):
        if key.startswith(('VERA_PROTOCOL_','ADP_','VELA_','WHY_')):os.environ.pop(key)
    helper.ALLOWED.add('MULTICHAIN');helper.load_environment(envfile)
    config=Config.from_env()
    if not config.multichain or config.origin!='http://127.0.0.1:8803' or config.db_path!=ROOT/'.runtime/lighter-showcase/multichain.sqlite3':raise ValueError('Separate multichain origin and database required')
    files=[ROOT/'site/multichain/index.html',ROOT/'site/multichain/build-manifest.json']
    files.extend((ROOT/'site/multichain/assets').glob('*'))
    if not all(p.is_file() for p in files):raise ValueError('Build multichain frontend first')
    files.extend((ROOT/'backend').rglob('*.py'))
    fingerprint=hashlib.sha256(envfile.read_bytes()+b''.join(hashlib.sha256(p.read_bytes()).digest() for p in sorted(files))).hexdigest()
    return config,fingerprint

def main():
    args=argparse.ArgumentParser(description=__doc__);args.add_argument('--activate',action='store_true');args.add_argument('--verify',action='store_true');options=args.parse_args()
    if options.verify:
        # Read-only probes never prepare configuration, rebuild or start a process.
        # Preserve the activation record and timestamp the new observation separately.
        try:result=dict(status='HTTP VERIFIED',**verify())
        except Exception as error:
            result=dict(status='UNVERIFIED',checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        errorType=type(error).__name__,error=str(error),processActions='None',liveProtectionVerified=False)
            if RUNTIME.is_dir():save(RUNTIME/'verification-current.json',result)
            raise
        save(RUNTIME/'verification-current.json',result);print(json.dumps(result,indent=2));return
    config,fingerprint=prepare()
    if not options.activate:
        print(json.dumps(dict(status='PREPARED; NOT ACTIVATED',origin=config.origin,database=str(config.db_path),port=PORT)));return
    with (RUNTIME/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        statefile=RUNTIME/'state.json'
        if statefile.exists():
            state=json.loads(statefile.read_text());identity=helper.process_identity(state['pid'])
            if identity==state['identity'] and identity['cwd']==str(ROOT) and state['fingerprint']==fingerprint:
                print(json.dumps(dict(status='ALREADY RUNNING',**verify()),indent=2));return
            raise ValueError('Recorded multichain state differs; no automatic restart or PID signalling')
        # Binding atomically verifies availability and reserves only loopback 8803.
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',PORT));listener.listen(128)
            argv=[str(ROOT/'.venv/bin/python'),'-m','uvicorn','autodefi.api:create_app','--factory','--fd',str(listener.fileno()),'--workers','1','--no-access-log','--no-proxy-headers']
            environment=dict(os.environ,PYTHONPATH=str(ROOT/'backend'),PYTHONDONTWRITEBYTECODE='1',XDG_CACHE_HOME=str(RUNTIME/'cache'))
            with os.fdopen(os.open(RUNTIME/'server.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600),'ab') as log:
                child=subprocess.Popen(argv,cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,pass_fds=(listener.fileno(),))
            try:
                deadline=time.monotonic()+45
                while child.poll() is None and time.monotonic()<deadline:
                    try:
                        if get('/api/health')[0]==200:break
                    except OSError:pass
                    time.sleep(.5)
                result=verify();identity=helper.process_identity(child.pid)
                if not identity or identity['cwd']!=str(ROOT) or not identity['app']:raise ValueError('Multichain process identity not confirmed')
                save(statefile,dict(pid=child.pid,identity=identity,fingerprint=fingerprint,
                    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    buildManifestSha256=hashlib.sha256((ROOT/'site/multichain/build-manifest.json').read_bytes()).hexdigest()))
                save(RUNTIME/'verification.json',dict(status='RUNNING AND HTTP VERIFIED',pid=child.pid,origin=config.origin,**result))
                print(json.dumps(dict(status='RUNNING AND HTTP VERIFIED',pid=child.pid,origin=config.origin,**result),indent=2))
            except BaseException:
                # This handle can refer only to the child created immediately above.
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                raise

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(type(error).__name__+': '+str(error),file=sys.stderr);sys.exit(1)
