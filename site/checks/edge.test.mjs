import test from 'node:test';
import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import {readFile} from 'node:fs/promises';
import {createWorker} from '../worker/index.js';

const origin='https://defivela.xyz';
const token='deterministic-test-token-never-production-123456';
const fixture=text=>({type:'text/html',etag:'"fixture"',gzip:gzipSync(text).toString('base64')});
const assets={'/index.html':fixture('original homepage'),'/portal.html':fixture('portal'),'/earn.html':fixture('earn'),'/mydoge.html':fixture('existing wallet')};
const worker=createWorker(assets,()=>{throw Error('Static pages must not contact backend');});

for (const [path,body] of [['/','original homepage'],['/trade','portal'],['/earn','earn'],['/mydoge.html','existing wallet']]) {
  test(`same-domain page ${path}`,async()=>{
    const r=await worker.fetch(new Request(origin+path));
    assert.equal(r.status,200);assert.equal(await r.text(),body);
  });
}
test('canonical redirects, HEAD, conditional GET, unknown files',async()=>{
  const r=await worker.fetch(new Request('https://defivela.uuuiiaaaa.chatgpt.site/trade?view=history'));
  assert.equal(r.status,308);assert.equal(r.headers.get('location'),origin+'/trade?view=history');
  assert.equal((await worker.fetch(new Request(origin+'/earn/'))).headers.get('location'),'/earn');
  assert.equal(await (await worker.fetch(new Request(origin+'/trade',{method:'HEAD'}))).text(),'');
  assert.equal((await worker.fetch(new Request(origin+'/trade',{headers:{'If-None-Match':'"fixture"'}}))).status,304);
  for(const path of ['/unknown','/.env','/server/index.js','/.openai/hosting.json']) assert.equal((await worker.fetch(new Request(origin+path))).status,404);
});
test('API proxy preserves body and secure session cookie; replaces visitor proxy headers',async()=>{
  let upstream;
  const w=createWorker(assets,async r=>{
    upstream=r;assert.equal(await r.text(),'{"amount":"100"}');
    return new Response('{}',{headers:{'Set-Cookie':'vela_demo_session=fixture; Path=/api/portal; Secure; HttpOnly; SameSite=Strict'}});
  });
  const r=await w.fetch(new Request(origin+'/api/portal/deposit',{method:'POST',body:'{"amount":"100"}',headers:{
    Origin:origin,'X-Vela-Portal':'1',Cookie:'vela_demo_session=fixture',
    'X-Vela-Site-Token':'attacker','X-Forwarded-Host':'evil.invalid','X-Vela-Client-IP':'evil','CF-Connecting-IP':'192.0.2.7'}}),{VELA_SITE_PROXY_TOKEN:token});
  assert.equal(upstream.url,'https://plot-adds-vertical-workforce.trycloudflare.com/api/portal/deposit');
  assert.equal(upstream.headers.get('x-vela-site-token'),token);
  assert.equal(upstream.headers.get('x-vela-public-origin'),origin);
  assert.equal(upstream.headers.get('x-vela-client-ip'),'192.0.2.7');
  assert.equal(upstream.headers.get('x-forwarded-host'),null);
  assert.equal(upstream.headers.get('cookie'),'vela_demo_session=fixture');
  assert.equal(upstream.redirect,'manual');
  assert.equal(r.headers.get('cache-control'),'no-store');
  assert.match(r.headers.get('set-cookie'),/Secure; HttpOnly; SameSite=Strict/);
});
test('forward existing API and health routes without changing their paths',async()=>{
  const paths=[];const w=createWorker({},async r=>{paths.push(new URL(r.url).pathname);return new Response('{}');});
  for(const path of ['/health','/health/engine','/api/config','/api/users/0x123','/api/paper/health']) {
    assert.equal((await w.fetch(new Request(origin+path),{VELA_SITE_PROXY_TOKEN:token})).status,200);
  }
  assert.deepEqual(paths,['/health','/health/engine','/api/config','/api/users/0x123','/api/paper/health']);
});
test('WebSocket proxy returns the upgrade response unchanged',async()=>{
  const upgrade={status:101,webSocket:{fixture:true}};
  const w=createWorker({},async r=>{assert.equal(r.headers.get('Upgrade'),'websocket');return upgrade;});
  const r=await w.fetch(new Request(origin+'/api/paper/stream',{headers:{Upgrade:'websocket',Origin:origin}}),{VELA_SITE_PROXY_TOKEN:token});
  assert.equal(r,upgrade);
});
test('missing token, foreign origin, invalid upstream and redirects fail closed',async()=>{
  let calls=0;
  const w=createWorker({},async()=>{calls++;return new Response(null,{status:302,headers:{Location:'https://evil.invalid'}});});
  assert.equal((await w.fetch(new Request(origin+'/api/portal/account'))).status,503);
  assert.equal((await w.fetch(new Request(origin+'/api/portal/deposit',{method:'POST',headers:{Origin:'https://evil.invalid'}}),{VELA_SITE_PROXY_TOKEN:token})).status,403);
  for(const backend of ['http://insecure.invalid','https://user:password@host.invalid','https://defivela.xyz','https://host.invalid/?key=bad']) {
    assert.equal((await w.fetch(new Request(origin+'/api/portal/account'),{VELA_SITE_PROXY_TOKEN:token,VELA_BACKEND_ORIGIN:backend})).status,503);
  }
  assert.equal(calls,0);
  assert.equal((await w.fetch(new Request(origin+'/api/portal/account'),{VELA_SITE_PROXY_TOKEN:token})).status,503);
  assert.equal(calls,1);
});
test('built artifact serves byte-identical approved pages and bundle',async()=>{
  const source=await readFile(new URL('../dist/server/index.js',import.meta.url),'utf8');
  const built=(await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default;
  for(const [path,file] of [['/','index.html'],['/trade','portal.html'],['/earn','earn.html'],['/assets/portal.js','assets/portal.js'],['/backend-config.js','backend-config.js']]) {
    const response=await built.fetch(new Request(origin+path));
    assert.equal(response.status,200);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()),await readFile(new URL('../dist/'+file,import.meta.url)));
  }
});
