// Package the existing frontend as a Worker for the EXISTING Sites project.
// Assets are embedded compressed; no new storage binding or external asset host.
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {extname} from 'node:path';
import {build} from 'esbuild';

const manifest=JSON.parse(await readFile('.openai/hosting.json','utf8'));
if (manifest.project_id !== 'appgprj_6ad757d1f93c8191bc736d87ada530be' || manifest.static) throw Error('Expected existing Vela Worker manifest');
const mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8',
  '.svg':'image/svg+xml','.png':'image/png','.woff':'font/woff','.woff2':'font/woff2','.txt':'text/plain; charset=utf-8'};
const assets={};
async function collect(folder,prefix='') {
  for (const entry of (await readdir(folder,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.') || entry.name==='server') continue;
    const path=`${folder}/${entry.name}`, name=`${prefix}/${entry.name}`;
    if (entry.isDirectory()) await collect(path,name);
    else if (entry.isFile()) {
      if (!mime[extname(entry.name)]) throw Error(`Unexpected public asset type: ${name}`);
      const bytes=await readFile(path);
      assets[name]={type:mime[extname(entry.name)],etag:`"${createHash('sha256').update(bytes).digest('hex')}"`,gzip:gzipSync(bytes,{level:9}).toString('base64')};
    } else throw Error(`Non-regular asset: ${name}`);
  }
}
await collect('dist');
await mkdir('dist/server',{recursive:true});
await mkdir('dist/.openai',{recursive:true});
await build({stdin:{contents:`import {createWorker} from './worker/index.js'; export default createWorker(${JSON.stringify(assets)});`,
  resolveDir:process.cwd(),sourcefile:'vela-sites-entry.js'},bundle:true,format:'esm',platform:'browser',target:'es2022',
  minify:true,outfile:'dist/server/index.js',logLevel:'info'});
await writeFile('dist/.openai/hosting.json',JSON.stringify(manifest,null,2)+'\n');
console.log(`Packaged ${Object.keys(assets).length} existing assets for ${manifest.project_id}`);
