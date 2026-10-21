// Same Sites project and hostname. Only API/stream traffic uses the existing backend.
const PUBLIC_ORIGIN = 'https://defivela.xyz';
const EXISTING_BACKEND = 'https://plot-adds-vertical-workforce.trycloudflare.com';
const aliases = {'/':'/index.html','/trade':'/portal.html','/earn':'/earn.html','/paper':'/paper.html'};

export function createWorker(assets, upstreamFetch = fetch) {
  return {
    async fetch(request, env = {}) {
      const url = new URL(request.url);
      if (url.origin !== PUBLIC_ORIGIN) {
        // Signatures and cookies have one canonical origin, including old Sites URLs.
        return new Response(null, {status:308, headers:{Location:PUBLIC_ORIGIN+url.pathname+url.search}});
      }
      const api = url.pathname.startsWith('/api/') || url.pathname === '/health' || url.pathname.startsWith('/health/');
      if (api) {
        if (!/^[A-Za-z0-9_-]{32,256}$/.test(env.VELA_SITE_PROXY_TOKEN || '')) {
          return unavailable();
        }
        if (!['GET','HEAD','POST','OPTIONS'].includes(request.method)) return new Response(null,{status:405});
        if (request.headers.get('Origin') && request.headers.get('Origin') !== PUBLIC_ORIGIN) {
          return new Response('Invalid application origin',{status:403});
        }
        const websocket = request.headers.get('Upgrade')?.toLowerCase() === 'websocket';
        if (websocket && (request.method !== 'GET' || url.pathname !== '/api/paper/stream')) {
          return new Response(null,{status:404});
        }
        let backend;
        try {
          backend = new URL(env.VELA_BACKEND_ORIGIN || EXISTING_BACKEND);
          // A runtime setting can follow the existing tunnel if its URL changes.
          if (backend.protocol !== 'https:' || backend.username || backend.password ||
              backend.pathname !== '/' || backend.search || backend.hash || backend.origin === PUBLIC_ORIGIN) return unavailable();
        } catch { return unavailable(); }
        backend.pathname = url.pathname; backend.search = url.search;
        const headers = new Headers(request.headers);
        for (const name of [...headers.keys()]) {
          if (name === 'host' || name === 'forwarded' || name.startsWith('x-forwarded-') || name.startsWith('x-vela-site-') || name.startsWith('x-vela-public-') || name === 'x-vela-client-ip') headers.delete(name);
        }
        headers.set('X-Vela-Site-Token',env.VELA_SITE_PROXY_TOKEN);
        headers.set('X-Vela-Public-Origin',PUBLIC_ORIGIN);
        const client = request.headers.get('CF-Connecting-IP');
        if (client) headers.set('X-Vela-Client-IP',client);
        // Manual redirects prevent forwarding the server-only token to other origins.
        const init = {method:request.method,headers,redirect:'manual'};
        if (!['GET','HEAD'].includes(request.method)) {init.body=request.body;init.duplex='half';}
        let response;
        try { response = await upstreamFetch(new Request(backend,init)); }
        catch { return unavailable(); }
        // Return the original WebSocket response so its webSocket attachment survives.
        if (response.status === 101) return response;
        if (response.status >= 300 && response.status < 400) return unavailable();
        const out = new Response(response.body,response);
        out.headers.set('Cache-Control','no-store');
        out.headers.delete('X-Vela-Site-Token');
        return out;
      }
      if (!['GET','HEAD'].includes(request.method)) return new Response(null,{status:405});
      if (['/trade/','/earn/','/paper/'].includes(url.pathname)) {
        return new Response(null,{status:307,headers:{Location:url.pathname.slice(0,-1)+url.search}});
      }
      const path = aliases[url.pathname] || url.pathname;
      const asset = assets[path];
      if (!asset) return new Response('Not found',{status:404});
      const headers = new Headers({'Content-Type':asset.type,'ETag':asset.etag,
        'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Vary':'Accept-Encoding'});
      if (request.headers.get('If-None-Match') === asset.etag) return new Response(null,{status:304,headers});
      const compressed = /(?:^|,)\s*gzip\s*(?:,|$)/i.test(request.headers.get('Accept-Encoding') || '');
      if (compressed) headers.set('Content-Encoding','gzip');
      if (request.method === 'HEAD') return new Response(null,{status:200,headers});
      const bytes=Uint8Array.from(atob(asset.gzip),c=>c.charCodeAt(0));
      const body=compressed ? bytes : new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      return new Response(body,{status:200,headers});
    }
  };
}

function unavailable() {
  return new Response(JSON.stringify({detail:'Vela connection unavailable. Please try again shortly.'}),
    {status:503,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
}
