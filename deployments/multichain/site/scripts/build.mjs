import {build} from 'esbuild';
import {mkdir,writeFile,readFile,rm,mkdtemp,rename} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const brand=JSON.parse(await readFile('../config/brand.json','utf8'));
// Backend changes must be activated together with this bundle. Keep the running
// preview's dist entrypoint untouched while preparing an independently tested build.
const destination=process.env.VELLA_BUILD_OUTPUT||'prepared';
if(!['prepared','multichain','candidate'].includes(destination))throw Error('Isolated output required');
// Publish hashed assets before replacing HTML; preserve open preview sessions.
const staging=await mkdtemp('.build-');
try{
 const result=await build({entryPoints:{app:'src/app.jsx'},outdir:`${staging}/assets`,publicPath:'/assets',bundle:true,alias:{'wagmi/connectors':'./src/injected-connector.js'},splitting:true,format:'esm',target:['es2022'],minify:true,metafile:true,entryNames:'[name]-[hash]',chunkNames:'chunk-[hash]',assetNames:'[name]-[hash]',define:{'process.env.NODE_ENV':'"production"',global:'globalThis'},loader:{'.png':'file','.webp':'file','.svg':'file','.woff2':'file','.woff':'file','.ttf':'file'}});
 const outputs=Object.keys(result.metafile.outputs),script=outputs.find(p=>result.metafile.outputs[p].entryPoint==='src/app.jsx'&&p.endsWith('.js'));
 const style=script&&result.metafile.outputs[script].cssBundle;
 if(!script||!style)throw Error('Missing frontend entrypoint or styles');
 const published=p=>p.replace(staging+'/',destination+'/'),url=p=>'/'+published(p).replace(destination+'/','');
 const escape=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const title=brand.name+' — Multichain AI-Powered DeFi Trading';
 await writeFile(`${staging}/index.html`,`<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#080908"><meta name="description" content="VELA — Multichain AI-Powered DeFi Trading. Seven network experiences. DogeOS featured. Demo DeFi and paper trading with Lighter market data. No real funding or live orders."><title>${escape(title)}</title><link rel="stylesheet" href="${url(style)}"></head><body><div id="root"></div><script type="module" src="${url(script)}"></script></body></html>`);
 const sha256={};for(const file of outputs)sha256[published(file)]=createHash('sha256').update(await readFile(file)).digest('hex');
 await writeFile(`${staging}/build-manifest.json`,JSON.stringify({builtAt:new Date().toISOString(),title,entry:published(script),outputs:outputs.map(published),sha256,htmlSha256:createHash('sha256').update(await readFile(`${staging}/index.html`)).digest('hex')},null,2));
 await mkdir(destination+'/assets',{recursive:true});
 for(const output of outputs)await rename(output,published(output));
 await rename(`${staging}/build-manifest.json`,destination+'/build-manifest.json');
 await rename(`${staging}/index.html`,destination+'/index.html');
 console.log('Frontend build complete:',published(script));
 console.log('Prepared complete assets and HTML; active preview entrypoint preserved.');
}finally{await rm(staging,{recursive:true,force:true});}
