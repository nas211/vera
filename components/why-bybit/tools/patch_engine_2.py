p='/opt/why-bybit/bybit_paper.py'; s=open(p).read()
def rep(old,new):
    global s
    assert s.count(old)==1, f"count={s.count(old)} for: "+old[:90]
    s=s.replace(old,new)

rep('''  $('#stats').innerHTML=
   card('Win rate',wr==null?'—':fmt(wr,1)+'%',wr>=68?'pos':'')+
   card('Net PnL',(net>=0?'+$':'-$')+fmt(Math.abs(net)),net>=0?'pos':'neg')+
   card('Equity','$'+fmt(b.equity))+
   card('Closed trades',st.trades||0)+
   card('Open',(b.open||[]).length+' / '+(b.max_open||8))+
   card('Fees paid','$'+fmt(st.fees_usd||0));''','''  const usd=(x,d=2)=>(x>=0?'+$':'-$')+fmt(Math.abs(x),d), ref=st.ref_net_usd||0, fs=b.fees||{};
  $('#stats').innerHTML=
   card('Win rate',wr==null?'—':fmt(wr,1)+'%',wr>=68?'pos':'')+
   card('NET PnL (after fees &amp; funding)',usd(net),net>=0?'pos':'neg')+
   card('Gross PnL (before costs)',usd(st.gross_usd||0),'')+
   card('Trading fees','-$'+fmt(st.fees_usd||0),'')+
   card('Funding paid','-$'+fmt(st.funding_usd||0,4),'')+
   card('Same trades at Yellow fees',usd(ref),ref>=0?'pos':'neg')+
   card('Fee difference (Bybit vs Yellow)',usd(net-ref),(net-ref)>=0?'pos':'neg')+
   card('Equity','$'+fmt(b.equity))+
   card('Closed trades',st.trades||0)+
   card('Open',(b.open||[]).length+' / '+(b.max_open||8));
  $('#feeline').textContent='Fees applied: maker '+fmt(fs.maker_bps,2)+' bps / taker '+fmt(fs.taker_bps,2)+' bps ('+(fs.source||'?')+(fs.vip?', '+fs.vip:'')+'). Yellow comparison: '+fmt(fs.ref_maker_bps,2)+' / '+fmt(fs.ref_taker_bps,2)+' bps.';''')
rep("card('Realized PnL',(nt>=0?'+$':'-$')+fmt(Math.abs(nt)),nt>=0?'pos':'neg')","card('Realized PnL (net)',(nt>=0?'+$':'-$')+fmt(Math.abs(nt)),nt>=0?'pos':'neg')")
rep('<th>Exit type</th><th>PnL</th><th>Held</th><th>Signal</th>','<th>Exit type</th><th>Gross</th><th>Fees</th><th>Funding</th><th>Net PnL</th><th>Held</th><th>Signal</th>')
rep('''   <td class="n ${t.pnl_usd>=0?'g':'r'}">${(t.pnl_usd>=0?'+$':'-$')+fmt(Math.abs(t.pnl_usd))}</td>
   <td class="d">${t.hold_min''','''   <td class="n d">${t.gross_usd==null?'—':(t.gross_usd>=0?'+$':'-$')+fmt(Math.abs(t.gross_usd),3)}</td>
   <td class="n d">${t.fees_usd==null?'—':'-$'+fmt(t.fees_usd,3)}</td>
   <td class="n d">${t.funding_usd==null?'—':(t.funding_usd>0?'-$':'+$')+fmt(Math.abs(t.funding_usd),4)}</td>
   <td class="n ${t.pnl_usd>=0?'g':'r'}">${(t.pnl_usd>=0?'+$':'-$')+fmt(Math.abs(t.pnl_usd),3)}</td>
   <td class="d">${t.hold_min''')
rep('<tr><td colspan="10" class="d">no closed trades yet</td></tr>','<tr><td colspan="13" class="d">no closed trades yet</td></tr>')
rep('''<div class="foot">Performance''','''<div class="foot" id="feeline" style="margin-top:14px"></div>
<div class="foot">Performance''')
open(p,'w').write(s); print("page patched")
