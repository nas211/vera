import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdir,copyFile,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const require=createRequire(new URL('../products-toolchain/package.json',import.meta.url));
const {build}=require('esbuild');
const nodePaths=[fileURLToPath(new URL('../products-toolchain/node_modules',import.meta.url))];
const output='products-built',prefix='/product-assets/paper-earn-20261103';
await mkdir(output+'/assets',{recursive:true});
const built=await build({nodePaths,metafile:true,entryPoints:{paper:'src/paper-entry.jsx','earn-account':'src/earn-entry.jsx'},outdir:output+'/assets',bundle:true,format:'esm',target:['es2022'],minify:true,define:{'process.env.NODE_ENV':'"production"'}});
const dependencies=[...new Set(Object.keys(built.metafile.inputs).filter(p=>p.includes('node_modules/')).map(p=>{const parts=p.split('node_modules/').at(-1).split('/');return parts[0].startsWith('@')?parts.slice(0,2).join('/'):parts[0];}))].sort();
if(!dependencies.includes('react')||!dependencies.includes('react-dom'))throw Error('Missing bundle dependency evidence');
for(const dependency of dependencies)if(!['react','react-dom','scheduler','@wallet-standard/app','@wallet-standard/base','@wallet-standard/errors','@wallet-standard/features','@wallet-standard/wallet'].includes(dependency))throw Error('Review new product dependency: '+dependency);
for(const name of ['styles.css','paper.css','favicon.svg','earn.js','earn-account.css'])await copyFile('products/'+name,output+'/'+name);
for(const name of ['paper.html','earn.html']){
 const html=(await readFile('products/'+name,'utf8')).replace(/(href|src)="\/(styles\.css|paper\.css|favicon\.svg|earn\.js|earn-account\.css|assets\/(?:paper|earn-account)\.js)"/g,`$1="${prefix}/$2"`);
 await writeFile(output+'/'+name,html);
}
const files=['paper.html','earn.html','styles.css','paper.css','favicon.svg','earn.js','earn-account.css','assets/paper.js','assets/earn-account.js'];
const hashes={};for(const name of files)hashes[name]=createHash('sha256').update(await readFile(output+'/'+name)).digest('hex');
await writeFile(output+'/manifest.json',JSON.stringify({prefix,files:hashes,bundledDependencies:dependencies,homepageModified:false},null,2)+'\n');
console.log(JSON.stringify({built:true,output,files:files.length,homepageModified:false}));
