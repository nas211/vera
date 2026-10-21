"""Optional socket-free test transport for restricted executors.

Runs the actual ASGI app, middleware, lifespan, threadpool handlers and cookies.
It is not browser, extension approval, live network or model-provider evidence.
"""
import asyncio
import inspect
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx


class InProcessClient:
    def __init__(self,app,base_url='http://testserver',**kwargs):
        self.app=app;self.base_url=base_url
        self.loop=asyncio.new_event_loop()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=base_url,follow_redirects=True)
        self.headers=self.client.headers;self.cookies=self.client.cookies
        self.portal=SimpleNamespace(call=self.call)
        self.lifespan=None

    def run(self,awaitable):
        async def execute():
            # Worker-thread notifications cannot wake this executor's selector.
            # A timer avoids that restriction without replacing any app handler.
            async def heartbeat():
                while True:await asyncio.sleep(.02)
            tick=asyncio.create_task(heartbeat())
            try:return await asyncio.wait_for(awaitable,20)
            finally:
                tick.cancel();await asyncio.gather(tick,return_exceptions=True)
        return self.loop.run_until_complete(execute())

    def call(self,fn,*args):
        async def invoke():
            value=fn(*args)
            return await value if inspect.isawaitable(value) else value
        return self.run(invoke())

    def __enter__(self):
        self.lifespan=self.app.router.lifespan_context(self.app)
        self.run(self.lifespan.__aenter__())
        return self

    def __exit__(self,*exc):
        try:
            if self.lifespan:self.run(self.lifespan.__aexit__(*exc))
            self.run(self.client.aclose())
            self.run(self.loop.shutdown_asyncgens())
            self.run(self.loop.shutdown_default_executor())
        finally:self.loop.close()

    def get(self,url,**kw):return self.run(self.client.get(url,**kw))
    def post(self,url,**kw):return self.run(self.client.post(url,**kw))
    def websocket_connect(self,path):return Socket(self,path)


class Socket:
    def __init__(self,client,path):self.client=client;self.path=path
    def __enter__(self):
        async def begin():
            self.inbox=asyncio.Queue();self.outbox=asyncio.Queue()
            url=urlsplit(self.client.base_url)
            scope={'type':'websocket','asgi':{'version':'3.0'},'scheme':'ws','path':self.path,
                   'raw_path':self.path.encode(),'query_string':b'','root_path':'','subprotocols':[],
                   'server':(url.hostname,80),'client':('127.0.0.1',1),'headers':[(b'host',url.netloc.encode())]}
            await self.inbox.put({'type':'websocket.connect'})
            self.task=asyncio.create_task(self.client.app(scope,self.inbox.get,self.outbox.put))
            assert (await self.outbox.get())['type']=='websocket.accept'
        self.client.run(begin());return self
    def receive_json(self):return json.loads(self.client.run(self.outbox.get())['text'])
    def __exit__(self,*exc):
        async def close():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        self.client.run(close())
