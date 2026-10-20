"""Loopback-only authenticated paper API. No exchange credentials or live routes."""
import json
import os
import queue
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .agents import Agents
from . import bulk_bridge
from .engine import Engine
from .market import PublicFeed, CandleFeed
from .model import Model
from .store import Store

def handler(engine,verifier):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):super().setup();self.connection.settimeout(10)
        def reply(self,status,value):
            body=json.dumps(value,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def process(self):
            if self.command=='GET' and self.path=='/health':return self.reply(200,dict(mode='paper',live_enabled=False,feed=engine.feed_status(),review_context=engine.context_status,fee_model_version=2,assumed_fee_per_fill=0.0004,bulk_research=bulk_bridge.status()))
            auth=self.headers.get('Authorization','')
            try:
                if not auth.startswith('Bearer '):return self.reply(401,dict(error='AUTH_REQUIRED'))
                user=verifier.verify(auth[7:]).user_id
            except Exception:return self.reply(401,dict(error='AUTH_INVALID_OR_UNAVAILABLE'))
            try:
                if self.command=='GET' and self.path=='/state':return self.reply(200,engine.state(user))
                if self.command=='GET' and urllib.parse.urlsplit(self.path).path=='/archive':
                    params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    if set(params)-{'offset'} or len(params.get('offset',['0']))!=1:raise ValueError('INVALID_OFFSET')
                    offset=int(params.get('offset',['0'])[0])
                    if not 0<=offset<=1000000:raise ValueError('INVALID_OFFSET')
                    return self.reply(200,engine.archives(user,offset))
                if self.command!='POST' or self.path not in ('/start','/pause','/close'):return self.reply(404,dict(error='NOT_FOUND'))
                if self.headers.get('Transfer-Encoding'):return self.reply(400,dict(error='INVALID_BODY'))
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=2048:return self.reply(413,dict(error='INVALID_BODY_SIZE'))
                body=json.loads(self.rfile.read(n))
                allowed={'candidate'} if self.path=='/close' else set()
                if not isinstance(body,dict) or set(body)!=allowed or (allowed and not isinstance(body['candidate'],str)):return self.reply(400,dict(error='INVALID_BODY'))
                return self.reply(200,engine.action(user,self.path[1:],body.get('candidate')))
            except (ValueError,KeyError,TypeError):return self.reply(400,dict(error='INVALID_REQUEST_OR_ACTION'))
            except Exception:return self.reply(503,dict(error='PAPER_SERVICE_UNAVAILABLE'))
        do_GET=process
        do_POST=process
    return Handler

def main():
    from customer.auth import Verifier
    url=os.environ.get('SUPABASE_URL','')
    if not url.startswith('https://'):raise RuntimeError('SUPABASE_URL_REQUIRED')
    verifier=Verifier(url,os.environ.get('WHY_DISCOVERY_AUTH_JWT_SECRET'))
    state=Path(os.environ.get('WHY_DISCOVERY_STATE','/var/lib/why-discovery'));state.mkdir(parents=True,exist_ok=True)
    e=Engine(Store(str(state/'paper.sqlite3')));out=queue.Queue(maxsize=20000);stop=threading.Event()
    def pinned():
        with e.lock:return {c['spec']['market'] for a in e.accounts.values() for c in a['candidates'].values() if c['entry'] or c['position']}
    feed=PublicFeed(out,pinned)
    def candle_markets():
        with e.lock:
            if not any(a['running'] or any(c['position'] for c in a['candidates'].values()) for a in e.accounts.values()):return set()
            return {m for m in e.markets if e.books.fresh(m,time.time())}
    candles=CandleFeed(out,candle_markets,stop)
    def pump():
        while not stop.is_set():
            try:
                try:kind,data,now=out.get(timeout=.1)
                except queue.Empty:e.tick();continue
                if not e.receive(kind,data,now):
                    # Repair only the affected market; other streams stay continuous.
                    feed.recover(data.get('market'))
            except Exception:
                with e.lock:
                    for a in e.accounts.values():a['running']=False
                    e.gap('PAPER_ENGINE_ERROR')
    threading.Thread(target=feed.run,daemon=True).start()
    threading.Thread(target=pump,daemon=True).start()
    threading.Thread(target=candles.run,daemon=True).start()
    threading.Thread(target=Agents(e,Model(),stop).run,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('WHY_DISCOVERY_PORT','8114'))),handler(e,verifier))
    try:server.serve_forever(poll_interval=.5)
    finally:stop.set();feed.stopping.set();server.server_close()

if __name__=='__main__':main()
