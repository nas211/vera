// Simulated MetaMask providers with real EIP-191 signatures and the actual
// portal backend. This does NOT verify genuine extension or mobile approval.
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const {generatePrivateKey,privateKeyToAccount}=require('viem/accounts');
const origin=process.env.PREVIEW_URL||'http://127.0.0.1:8791';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
  const cases=[],errors=[];
  async function setup(options={}){
    const signers=[privateKeyToAccount(generatePrivateKey()),privateKeyToAccount(generatePrivateKey())];
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.exposeFunction('__signWalletFixture',(raw,address)=>signers.find(s=>s.address.toLowerCase()===address.toLowerCase()).signMessage({message:{raw}}));
    await context.addInitScript(({addresses,options})=>{
      const listeners={};
      const mock=window.mock={addresses,account:addresses[0],chain:sessionStorage.getItem('fixture-chain')||'0x1',calls:[],rejectConnect:false,rejectSign:false,missingChain:true};
      mock.emit=(name,value)=>{if(name==='accountsChanged')mock.account=value[0];if(name==='chainChanged')mock.chain=value;(listeners[name]||[]).forEach(fn=>fn(value));};
      const provider={isMetaMask:true,on:(n,f)=>(listeners[n]||=[]).push(f),removeListener:(n,f)=>{listeners[n]=(listeners[n]||[]).filter(x=>x!==f);},request:async({method,params})=>{
        mock.calls.push(method);
        if(method==='eth_requestAccounts'){if(mock.rejectConnect){mock.rejectConnect=false;throw {code:4001};}return[mock.account];}
        if(method==='eth_accounts')return mock.account?[mock.account]:[];
        if(method==='eth_chainId')return mock.chain;
        if(method==='wallet_switchEthereumChain'){if(mock.missingChain){mock.missingChain=false;throw {code:4902};}mock.chain=params[0].chainId;sessionStorage.setItem('fixture-chain',mock.chain);mock.emit('chainChanged',mock.chain);return null;}
        if(method==='wallet_addEthereumChain')return null;
        if(method==='personal_sign'){if(mock.rejectSign){mock.rejectSign=false;throw {code:4001};}return window.__signWalletFixture(params[0],params[1]);}
        if(method==='eth_getBalance')return '0x0';
        if(method==='wallet_revokePermissions')return null;
        throw Error('Unexpected wallet call: '+method);
      }};
      if(options.fallback)window.ethereum=provider;
      else if(!options.absent){
        window.ethereum={isCoinbaseWallet:true,request(){throw Error('Wrong wallet selected');}};
        window.addEventListener('eip6963:requestProvider',()=>window.dispatchEvent(new CustomEvent('eip6963:announceProvider',{detail:{info:{rdns:'io.metamask',uuid:'fixture',name:'MetaMask'},provider}})));
      }
    },{addresses:signers.map(s=>s.address),options});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin);assert.deepEqual(await page.evaluate(()=>mock.calls),[]);
    await page.locator('.wallet-open').first().click();await page.getByRole('dialog').waitFor();
    assert.deepEqual(await page.evaluate(()=>mock.calls),[],'Onboarding must not request wallet access before selection');
    return {context,page,signers};
  }
  const connect=async page=>{await page.getByRole('button',{name:/MetaMask Desktop/}).click();await page.getByTestId('equity').waitFor();};
  const loggedOut=async page=>{
    await page.getByRole('dialog').waitFor();
    for(let attempt=0;attempt<40;attempt++){
      if((await page.request.get(origin+'/api/portal/account')).status()===401)return;
      await page.waitForTimeout(100);
    }
    throw Error('Wallet change did not revoke the backend session');
  };
  try{
    let {context,page}=await setup({absent:true});
    await page.getByRole('button',{name:/MetaMask Desktop/}).click();
    await page.getByRole('alert').filter({hasText:'install the official MetaMask extension'}).waitFor();
    assert.equal(await page.getByRole('link',{name:'Get MetaMask'}).getAttribute('href'),'https://metamask.io/download/');
    assert(await page.getByRole('button',{name:/DogeOS \/ MyDoge/}).isDisabled());
    assert.match(await page.getByRole('dialog').innerText(),/Coming Soon — Mobile Only/);
    assert.deepEqual(await page.evaluate(()=>mock.calls),[]);
    cases.push('Missing extension: useful error, official link, desktop MyDoge disabled');await context.close();
    for(const fallback of [false,true]){
      ({context,page}=await setup({fallback}));
      await page.evaluate(()=>mock.rejectConnect=true);
      await page.getByRole('button',{name:/MetaMask Desktop/}).click();await page.getByText('Cancelled in your wallet. You can try again.').waitFor();
      await page.evaluate(()=>mock.rejectSign=true);
      await page.getByRole('button',{name:/MetaMask Desktop/}).click();
      await page.waitForFunction(()=>mock.calls.includes('personal_sign'));
      await page.getByText('Cancelled in your wallet. You can try again.').waitFor();
      await connect(page);
      const methods=await page.evaluate(()=>mock.calls);
      assert(methods.includes('wallet_addEthereumChain'));assert(methods.includes('wallet_switchEthereumChain'));
      await page.getByRole('navigation').getByRole('button',{name:'Account',exact:true}).click();
      assert((await page.locator('main').innerText()).includes((await page.evaluate(()=>mock.account)).toLowerCase()));
      await page.reload();await page.locator('.portal-account-address').waitFor();
      assert(!(await page.evaluate(()=>mock.calls)).includes('personal_sign'),'Reload must restore without signing again');
      await page.evaluate(()=>mock.emit('accountsChanged',[mock.addresses[1]]));await loggedOut(page);
      await connect(page);await page.getByRole('navigation').getByRole('button',{name:'Account',exact:true}).click();
      assert((await page.locator('main').innerText()).includes((await page.evaluate(()=>mock.addresses[1])).toLowerCase()));
      await page.evaluate(()=>mock.emit('chainChanged','0x1'));await loggedOut(page);
      await connect(page);await page.evaluate(()=>mock.emit('disconnect',{}));await loggedOut(page);
      await connect(page);await page.getByRole('navigation').getByRole('button',{name:'Account',exact:true}).click();
      await page.getByRole('button',{name:'Disconnect wallet'}).click();await loggedOut(page);
      assert((await page.evaluate(()=>mock.calls)).includes('wallet_revokePermissions'));
      assert(!(await page.evaluate(()=>mock.calls)).some(m=>/sendTransaction|signTransaction|eth_send|eth_sign$/.test(m)));
      cases.push((fallback?'Injected fallback':'EIP-6963')+': rejection/retry, signature, add/switch network, address, persistence, account/network changes, disconnect');
      await context.close();
    }
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,realWalletApproval:false,cases,errors},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
