from pathlib import Path
p=Path(__file__).with_name('runner.py')
s=p.read_text()
s=s.replace("self.event('MAKER_EXIT_FILL',market=m,price=o['price'],qty=q,pnl=pnl,fee=0)", "self.event('MAKER_EXIT_FILL',market=m,price=o['price'],qty=q,pnl=pnl,fee=0,lot=o['lot'],entry_order=l.get('entry_order'),entry_price=l['entry'],side='long' if l['buy'] else 'short',holding_seconds=self.clock()-l['opened'],reason=o.get('exit_reason','unknown'),remaining_qty=max(0,l['qty']),closed=l['qty']<1e-10)")
s=s.replace("self.event('MAKER_ENTRY_FILL',market=m,price=o['price'],qty=q,fee=0)", "self.event('MAKER_ENTRY_FILL',market=m,price=o['price'],qty=q,fee=0,lot=lid,entry_order=o['id'],side='long' if buy else 'short')")
p.write_text(s)
