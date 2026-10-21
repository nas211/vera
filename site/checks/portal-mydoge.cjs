// Mobile SDK hook fixture with genuine off-chain signature verification.
// This is not a real MyDoge mobile approval or funded wallet test.
const assert=require('node:assert/strict');
const {build}=require('esbuild');
const {chromium}=require('playwright');
const {generatePrivateKey,privateKeyToAccount}=require('viem/accounts');
const fs=require('node:fs');
const origin=process.env.PREVIEW_URL||'http://127.0.0.1:8791';
(async()=>{
  const signer=privateKeyToAccount(generatePrivateKey());
  const mock=`
    import {useSyncExternalStore} from 'react';
    const listeners=new Set();const subscribe=f=>{listeners.add(f);return()=>listeners.delete(f);};
    const state=window.mockDoge={wallet:{isConnected:false,isConnecting:false,isWalletReady:false,walletStatus:'disabled'},account:{address:null,chainType:'evm',chainId:6281971,currentProvider:null},calls:[],reject:false};
    const publish=()=>listeners.forEach(f=>f());
    const provider={request:async({method,params})=>{state.calls.push(method);
      if(method==='eth_chainId')return '0x5fdaf3';if(method==='eth_accounts')return [state.account.address];
      if(method==='eth_getBalance')return '0xa688906bd8b00000';
      if(method==='personal_sign')return window.signDogeFixture(params[0]);throw Error('Unexpected '+method);
    }};
    const openModal=async()=>{if(state.reject){state.reject=false;throw {code:4001};}state.account={...state.account,address:window.fixtureAddress,currentProvider:provider};state.wallet={...state.wallet,isConnected:true};publish();};
    const disconnect=async()=>{state.account={...state.account,address:null,currentProvider:null};state.wallet={...state.wallet,isConnected:false};publish();};
    state.changeAccount=address=>{state.account={...state.account,address};publish();};
    export const ChainTypeEnum={EVM:'evm'};
    export function WalletConnectProvider({config,children}){state.config=config;return children;}
    export function useAccount(){return useSyncExternalStore(subscribe,()=>state.account);}
    export function useWalletConnect(){return {...useSyncExternalStore(subscribe,()=>state.wallet),openModal,disconnect};}
  `;
  const output=await build({entryPoints:['src/portal-entry.jsx'],bundle:true,format:'esm',write:false,jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'mobile-sdk-fixture',setup(p){
    p.onResolve({filter:/^@dogeos\/dogeos-sdk$/},()=>({path:'mock',namespace:'mock'}));
    p.onLoad({filter:/.*/,namespace:'mock'},()=>({contents:mock,loader:'jsx',resolveDir:process.cwd()}));
    p.onResolve({filter:/@dogeos\/dogeos-sdk\/style.css/},()=>({path:'empty',namespace:'empty'}));
    p.onLoad({filter:/.*/,namespace:'empty'},()=>({contents:'',loader:'js'}));
  }}]});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36'});
  await context.exposeFunction('signDogeFixture',raw=>signer.signMessage({message:{raw}}));
  await context.addInitScript(address=>{window.fixtureAddress=address;},signer.address);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/assets/portal.js',r=>r.fulfill({contentType:'application/javascript',body:output.outputFiles[0].text}));
  try{
    await page.goto(origin+'/trade');
    assert.equal(await page.locator('.portal-wallet-options>button').count(),2);
    await page.getByRole('button',{name:/DogeOS \/ MyDoge/}).click();
    await page.getByRole('button',{name:'Connect in MyDoge',exact:true}).waitFor();
    await page.evaluate(()=>mockDoge.reject=true);
    await page.getByRole('button',{name:'Connect in MyDoge',exact:true}).click();
    await page.getByText('Connection cancelled. Please try again in MyDoge.').waitFor();
    await page.getByRole('button',{name:'Connect in MyDoge',exact:true}).click();
    await page.getByRole('button',{name:'Verify wallet & continue'}).click();
    await page.getByTestId('equity').waitFor();
    assert.equal(await page.evaluate(()=>mockDoge.wallet.isWalletReady),false);
    assert.deepEqual(await page.evaluate(()=>mockDoge.config.login),{basicLogins:['externalWallets'],socialLogins:[]});
    assert.equal(await page.evaluate(()=>mockDoge.config.chains.evm[0].id),6281971);
    await page.getByRole('navigation').getByRole('button',{name:'Account',exact:true}).click();
    await page.getByText('12 test DOGE',{exact:true}).waitFor();
    await page.evaluate(()=>mockDoge.changeAccount('0x'+'33'.repeat(20)));
    await page.getByRole('dialog').waitFor();
    await page.getByText('MyDoge account or network changed. Reconnect to continue.').waitFor();
    assert.equal((await page.request.get(origin+'/api/portal/account')).status(),401);
    assert(!(await page.evaluate(()=>mockDoge.calls)).some(m=>/sendTransaction|signTransaction/.test(m)));
    assert.deepEqual(errors,[]);
    const result={passed:true,wallet:'Mock MyDoge SDK hooks, real EIP-191 signature verification',realMobileApproval:false,desktopAvailability:'Covered by portal.cjs',mobileRejectionRetry:true,accountChangeRevokesSession:true,nativeDoesNotRequireEmbeddedReadiness:true,errors};
    if(process.env.CHECK_ARTIFACTS_DIR){fs.mkdirSync(process.env.CHECK_ARTIFACTS_DIR,{recursive:true});fs.writeFileSync(process.env.CHECK_ARTIFACTS_DIR+'/mydoge-result.json',JSON.stringify(result,null,2));}
    console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
