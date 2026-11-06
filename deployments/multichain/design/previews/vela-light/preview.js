(()=>{
 const $=s=>document.querySelector(s),all=s=>[...document.querySelectorAll(s)];
 let toastTimer;
 const toast=text=>{const t=$('#preview-toast');t.textContent=text;t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,4200);};
 const show=title=>{$('#preview-dialog h2').textContent=title;$('#preview-dialog').showModal();};
 $('#preview-dialog button').onclick=()=>$('#preview-dialog').close();
 $('#preview-dialog').addEventListener('click',e=>{if(e.target===$('#preview-dialog'))$('#preview-dialog').close();});
 all('button').forEach(b=>{
  const label=b.textContent.trim();
  if(b.closest('#preview-dialog'))return;
  if(b.getAttribute('aria-label')==='Open profile menu'){b.onclick=()=>{$('#preview-profile').hidden=!$('#preview-profile').hidden;};return;}
  if(b.getAttribute('role')==='tab')return;
  if(label==='MAX')return;
  if(label==='Settings'){b.onclick=()=>location.href='settings.html#trading-settings';return;}
  if(label==='Night'||label==='Day'){b.onclick=()=>toast('This is the light design preview. Appearance settings on the live app are unchanged.');return;}
  if(label==='Refresh balances'){b.onclick=()=>toast('Example balances for this visual preview.');return;}
  if(label.includes('Disconnect')||label==='Preview wallet'){b.onclick=()=>toast('Design preview only. No wallet is connected.');return;}
  if(b.closest('form'))return;
  b.onclick=()=>show(label||'Design preview');
 });
 const form=$('.simple-action form'),input=$('#doge-amount');let mode='borrow';
 if(form){
  const label=form.querySelector('label'),submit=form.querySelector('button[type=submit]'),hint=form.querySelector('.simple-amount>div:last-child>span');
  const source=document.createElement('select');source.className='simple-source';source.setAttribute('aria-label','Repayment source');source.innerHTML='<option>Use available DOGE</option><option>Use wallet DOGE</option>';source.hidden=true;label.after(source);
  const render=()=>{const n=Math.max(0,Number(input.value)||0),available=mode==='borrow'?2.99:4;hint.textContent='Available: '+available.toFixed(2)+' DOGE';submit.disabled=n<=0||n>available;submit.firstChild.textContent=mode==='borrow'?'Borrow DOGE':'Repay DOGE';const values=all('.simple-loan-preview strong');if(values.length===2){const next=mode==='borrow'?4+n:Math.max(0,4-n);values[0].textContent=next.toFixed(2)+' DOGE';values[1].textContent=(next/10*100).toFixed(2)+'%';}form.querySelector('.simple-loan-preview .simple-row span').textContent='Your loan after '+(mode==='borrow'?'borrowing':'repayment');};
  all('.simple-tabs [role=tab]').forEach(tab=>tab.onclick=()=>{mode=tab.textContent.trim().toLowerCase();all('.simple-tabs [role=tab]').forEach(t=>{t.classList.toggle('selected',t===tab);t.setAttribute('aria-selected',String(t===tab));});label.textContent='How much would you like to '+mode+'?';input.value='';source.hidden=mode!=='repay';render();});
  all('button').filter(b=>b.textContent.trim()==='MAX').forEach(b=>b.onclick=()=>{input.value=mode==='borrow'?'2.99':'4';render();});
  input.oninput=render;form.onsubmit=e=>{e.preventDefault();show(mode==='borrow'?'Borrow '+input.value+' DOGE':'Repay '+input.value+' DOGE');};
  render();
 }
 const strategy=$('select[aria-label=Strategy]');if(strategy)strategy.addEventListener('change',()=>toast('Preview strategy: '+strategy.options[strategy.selectedIndex].text));
 all('form').filter(f=>f!==form).forEach(f=>f.onsubmit=e=>{e.preventDefault();show('Trading settings');});
 all('input[type=file]').forEach(i=>i.addEventListener('change',()=>toast('Profile pictures stay unchanged in this preview.')));
})();
