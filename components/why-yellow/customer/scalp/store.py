"""Atomic, account-local order intents, confirmed fills and strategy checkpoint."""
import json
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, venue_id TEXT, state TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        ''')
        self.db.commit()
    def state(self):
        row=self.db.execute('select payload from checkpoint where id=1').fetchone()
        return json.loads(row[0]) if row else None
    def save(self, model):
        self.db.execute('insert into checkpoint values(1,?) on conflict(id) do update set payload=excluded.payload',
                        (json.dumps(model.checkpoint(),allow_nan=False),))
    def orders(self):
        return [dict(r, payload=json.loads(r['payload'])) for r in self.db.execute('select * from orders')]
    def active_orders(self):
        # Hot paths run per feed message; terminal history grows without bound
        # and must not be decoded there.
        return [dict(r, payload=json.loads(r['payload'])) for r in
                self.db.execute("select * from orders where state not in ('filled','cancelled','rejected')")]
    def get_order(self, coid):
        r=self.db.execute('select * from orders where id=?',(coid,)).fetchone()
        return dict(r, payload=json.loads(r['payload'])) if r else None
    def has_order(self, coid):
        return self.db.execute('select 1 from orders where id=?',(coid,)).fetchone() is not None
    def put(self, coid, state, payload, venue_id=None):
        self.db.execute('insert into orders values(?,?,?,?) on conflict(id) do update set state=excluded.state,payload=excluded.payload,venue_id=coalesce(excluded.venue_id,orders.venue_id)',
                        (coid,venue_id,state,json.dumps(payload,allow_nan=False)))
    def seen(self, fill_id):
        return self.db.execute('select 1 from fills where id=?',(fill_id,)).fetchone() is not None
    def fill(self, trade, context=None):
        data={k:str(v) for k,v in vars(trade).items()}
        data['context']=context or {}
        self.db.execute('insert into fills values(?,?)',(trade.trade_id,json.dumps(data)))

    def repair_fill_counters(self):
        from decimal import Decimal
        rows=self.orders()
        by_coid={r['id']:r for r in rows}
        by_vid={r['venue_id']:r for r in rows if r['venue_id']}
        totals={r['id']:Decimal(0) for r in rows}
        for record in self.db.execute('select payload from fills'):
            fill=json.loads(record[0])
            row=by_coid.get(fill.get('client_order_id')) or by_vid.get(fill.get('venue_order_id'))
            if row:
                qty=Decimal(fill['qty'])
                if not qty.is_finite() or qty<=0:raise ValueError('invalid_private_fill_journal')
                totals[row['id']]+=qty
        with self.db:
            for row in rows:
                qty=totals[row['id']]
                if qty>Decimal(row['payload']['qty']):raise ValueError('invalid_private_fill_journal')
                if qty!=Decimal(row['payload'].get('filled_qty','0')):
                    payload=dict(row['payload'],filled_qty=str(qty))
                    self.put(row['id'],row['state'],payload,row['venue_id'])
