"""Durable execution ledger (SQLite, WAL). One row per order identity, keyed by a
deterministic client_order_id derived from (account, signal, purpose, market,
direction). Transitions are checked against execution.state_machine and every
transition is an event with a unique id, so duplicate deliveries are no-ops.
Intent rows are written BEFORE any transport call (audit F-02)."""
from __future__ import annotations
import hashlib, json, os, sqlite3, threading, time
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from .state_machine import (IllegalTransition, check_order_transition, check_position_transition)
from .switches import Switches, write_mirror
from .types import OrderPurpose, OrderState as O, PositionState as P

COID_PREFIX = "why-"

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  client_order_id TEXT PRIMARY KEY, intent_key TEXT UNIQUE NOT NULL, signal_id TEXT NOT NULL,
  account_id TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL, direction TEXT NOT NULL,
  order_type TEXT NOT NULL, purpose TEXT NOT NULL, strategy TEXT NOT NULL, qty TEXT NOT NULL,
  price TEXT, leverage INTEGER NOT NULL, reduce_only INTEGER NOT NULL, tif TEXT NOT NULL,
  state TEXT NOT NULL, venue_order_id TEXT, filled_qty TEXT NOT NULL DEFAULT '0',
  avg_price TEXT NOT NULL DEFAULT '0', fees TEXT NOT NULL DEFAULT '0', attempts INTEGER NOT NULL DEFAULT 0,
  position_id TEXT, reason TEXT, meta TEXT, created_ts REAL NOT NULL, updated_ts REAL NOT NULL, state_ts REAL NOT NULL, submitted_ts REAL);
CREATE INDEX IF NOT EXISTS orders_state ON orders(state);
CREATE INDEX IF NOT EXISTS orders_venue ON orders(venue_order_id);
CREATE TABLE IF NOT EXISTS positions(
  position_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, market TEXT NOT NULL, direction TEXT NOT NULL,
  strategy TEXT NOT NULL, entry_coid TEXT, qty TEXT NOT NULL, entry_price TEXT NOT NULL, state TEXT NOT NULL,
  origin TEXT NOT NULL, reason TEXT, opened_ts REAL NOT NULL, updated_ts REAL NOT NULL, state_ts REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS positions_open ON positions(account_id, market, direction) WHERE state != 'CLOSED';
CREATE TABLE IF NOT EXISTS fills(
  trade_id TEXT PRIMARY KEY, client_order_id TEXT, venue_order_id TEXT, account_id TEXT NOT NULL,
  market TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL, fee_currency TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, from_state TEXT, to_state TEXT,
  ts REAL NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS protection(
  position_id TEXT PRIMARY KEY, version INTEGER NOT NULL, target_coid TEXT, stop_coid TEXT, stop_kind TEXT NOT NULL,
  tp_price TEXT NOT NULL, stop_price TEXT NOT NULL, qty TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL, updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS flags(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, severity TEXT NOT NULL, code TEXT NOT NULL,
  account_id TEXT, market TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS switches(
  id INTEGER PRIMARY KEY CHECK(id=1), allow_new_entries INTEGER NOT NULL, manage_existing_positions INTEGER NOT NULL,
  updated_ts REAL NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def make_coid(account_id: str, signal_id: str, purpose: str, market: str, direction: str) -> str:
    h = hashlib.sha256(f"{account_id}|{signal_id}|{purpose}|{market}|{direction}".encode()).hexdigest()
    return COID_PREFIX + h[:28]


def intent_key(account_id: str, signal_id: str, purpose: str, market: str, direction: str) -> str:
    return f"{account_id}|{signal_id}|{purpose}|{market}|{direction}"


class Ledger:
    def __init__(self, path: str, switches_mirror: Optional[str] = None, clock=None):
        self.path = path
        self.mirror = switches_mirror
        self.clock = clock or time.time
        self._lock = threading.RLock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO switches(id, allow_new_entries, manage_existing_positions, updated_ts, reason) VALUES (1,0,1,?, 'initial: entries OFF, management ON')", (self.clock(),))

    # ---------------------------------------------------------------- helpers
    def _tx(self):
        return _Tx(self)

    @staticmethod
    def _row(r) -> Optional[Dict[str, Any]]:
        return None if r is None else dict(r)

    def _event(self, event_id: str, kind: str, ref: str, frm: Optional[str], to: Optional[str], ts: float, detail: str = "") -> bool:
        try:
            self.db.execute("INSERT INTO events(event_id, kind, ref, from_state, to_state, ts, detail) VALUES (?,?,?,?,?,?,?)",
                            (event_id, kind, ref, frm, to, ts, detail))
            return True
        except sqlite3.IntegrityError:
            return False

    def event_seen(self, event_id: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is not None

    # ---------------------------------------------------------------- orders
    def create_intent(self, *, signal_id: str, account_id: str, market: str, side: str, direction: str, order_type: str,
                      purpose: str, strategy: str, qty: Decimal, leverage: int, reduce_only: bool, tif: str,
                      price: Optional[Decimal] = None, position_id: Optional[str] = None, meta: Optional[dict] = None,
                      coid: Optional[str] = None):
        """Idempotent. Returns (row, created)."""
        key = intent_key(account_id, signal_id, purpose, market, direction)
        coid = coid or make_coid(account_id, signal_id, purpose, market, direction)
        now = self.clock()
        with self._tx():
            existing = self.db.execute("SELECT * FROM orders WHERE intent_key=?", (key,)).fetchone()
            if existing is not None:
                return self._row(existing), False
            self.db.execute("""INSERT INTO orders(client_order_id,intent_key,signal_id,account_id,market,side,direction,order_type,purpose,
                strategy,qty,price,leverage,reduce_only,tif,state,position_id,meta,created_ts,updated_ts,state_ts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (coid, key, signal_id, account_id, market, side, direction, order_type, purpose, strategy, str(qty),
                 None if price is None else str(price), int(leverage), int(bool(reduce_only)), tif, O.INTENT.value,
                 position_id, json.dumps(meta or {}), now, now, now))
            self._event(f"intent:{coid}", "order", coid, None, O.INTENT.value, now)
            return self._row(self.db.execute("SELECT * FROM orders WHERE client_order_id=?", (coid,)).fetchone()), True

    def get_order(self, coid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row(self.db.execute("SELECT * FROM orders WHERE client_order_id=?", (coid,)).fetchone())

    def find_order_by_venue_id(self, venue_order_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row(self.db.execute("SELECT * FROM orders WHERE venue_order_id=?", (venue_order_id,)).fetchone())

    def orders_where(self, states: Iterable[O], account_id: Optional[str] = None, market: Optional[str] = None,
                     purpose: Optional[str] = None) -> List[Dict[str, Any]]:
        st = [s.value for s in states]
        q = f"SELECT * FROM orders WHERE state IN ({','.join('?' * len(st))})"
        args: list = list(st)
        if account_id: q += " AND account_id=?"; args.append(account_id)
        if market: q += " AND market=?"; args.append(market)
        if purpose: q += " AND purpose=?"; args.append(purpose)
        with self._lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY created_ts", args).fetchall()]

    def orders_for_position(self, position_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM orders WHERE position_id=? ORDER BY created_ts", (position_id,)).fetchall()]

    def claim_for_submission(self, coid: str) -> bool:
        """Atomic VALIDATED -> SUBMITTING. Exactly one caller wins; every other worker
        gets False and must not submit."""
        now = self.clock()
        with self._tx():
            cur = self.db.execute("UPDATE orders SET state=?, attempts=attempts+1, updated_ts=?, state_ts=?, submitted_ts=? WHERE client_order_id=? AND state=?",
                                  (O.SUBMITTING.value, now, now, now, coid, O.VALIDATED.value))
            if cur.rowcount != 1:
                return False
            n = self.db.execute("SELECT attempts FROM orders WHERE client_order_id=?", (coid,)).fetchone()[0]
            self._event(f"submit:{coid}:{n}", "order", coid, O.VALIDATED.value, O.SUBMITTING.value, now)
            return True

    def transition_order(self, coid: str, new: O, event_id: str, reason: Optional[str] = None, **fields) -> bool:
        """Returns False when the event was already applied (duplicate) or the state is
        unchanged; raises IllegalTransition for anything the state machine forbids."""
        now = self.clock()
        with self._tx():
            row = self.db.execute("SELECT * FROM orders WHERE client_order_id=?", (coid,)).fetchone()
            if row is None:
                raise KeyError(coid)
            if self.db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                return False
            cur = O(row["state"])
            real = check_order_transition(cur, new)
            sets = ["updated_ts=?"]; args: list = [now]
            if real:
                sets += ["state=?", "state_ts=?"]; args += [new.value, now]
            if reason is not None:
                sets.append("reason=?"); args.append(reason)
            for k, v in fields.items():
                if k not in ("venue_order_id", "filled_qty", "avg_price", "fees", "position_id"):
                    raise ValueError(k)
                sets.append(f"{k}=?"); args.append(None if v is None else str(v))
            args.append(coid)
            self.db.execute(f"UPDATE orders SET {', '.join(sets)} WHERE client_order_id=?", args)
            self._event(event_id, "order", coid, cur.value, new.value if real else cur.value, now, reason or "")
            return real

    def approve_resubmission(self, coid: str, reconcile_id: str, reason: str) -> bool:
        """FAILED -> VALIDATED, only with a reconciliation id proving venue absence."""
        if not reconcile_id.startswith("reconcile:"):
            raise IllegalTransition("resubmission requires a reconciliation id")
        return self.transition_order(coid, O.VALIDATED, f"resubmit:{coid}:{reconcile_id}", reason=reason)

    # ---------------------------------------------------------------- fills
    def record_fill(self, trade, coid: Optional[str]) -> bool:
        """Idempotent on trade_id. Updates the order's filled_qty/avg_price/fees."""
        with self._tx():
            try:
                self.db.execute("INSERT INTO fills(trade_id,client_order_id,venue_order_id,account_id,market,qty,price,fee,fee_currency,ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (trade.trade_id, coid, trade.venue_order_id, getattr(trade, "account_id", "") or "", trade.market,
                                 str(trade.qty), str(trade.price), str(trade.fee), trade.fee_currency, float(trade.ts)))
            except sqlite3.IntegrityError:
                return False
            if coid:
                row = self.db.execute("SELECT filled_qty, avg_price, fees FROM orders WHERE client_order_id=?", (coid,)).fetchone()
                if row is not None:
                    fq, ap, fe = Decimal(row["filled_qty"]), Decimal(row["avg_price"]), Decimal(row["fees"])
                    nq = fq + trade.qty
                    nap = ((fq * ap) + trade.qty * trade.price) / nq if nq > 0 else Decimal(0)
                    self.db.execute("UPDATE orders SET filled_qty=?, avg_price=?, fees=?, updated_ts=? WHERE client_order_id=?",
                                    (str(nq), str(nap), str(fe + trade.fee), self.clock(), coid))
            return True

    def fills_for(self, coid: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM fills WHERE client_order_id=? ORDER BY ts", (coid,)).fetchall()]

    # ---------------------------------------------------------------- positions
    def open_position(self, *, position_id: str, account_id: str, market: str, direction: str, strategy: str,
                      entry_coid: Optional[str], qty: Decimal, entry_price: Decimal, state: P, origin: str, reason: str = "") -> Dict[str, Any]:
        now = self.clock()
        with self._tx():
            self.db.execute("""INSERT INTO positions(position_id,account_id,market,direction,strategy,entry_coid,qty,entry_price,state,origin,reason,opened_ts,updated_ts,state_ts)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (position_id, account_id, market, direction, strategy, entry_coid, str(qty), str(entry_price), state.value, origin, reason, now, now, now))
            self._event(f"position_open:{position_id}", "position", position_id, None, state.value, now, origin)
            return self.get_position(position_id)

    def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row(self.db.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone())

    def open_position_for(self, account_id: str, market: str, direction: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row(self.db.execute("SELECT * FROM positions WHERE account_id=? AND market=? AND direction=? AND state!='CLOSED'",
                                             (account_id, market, direction)).fetchone())

    def positions_where(self, states: Iterable[P], account_id: Optional[str] = None, market: Optional[str] = None) -> List[Dict[str, Any]]:
        st = [s.value for s in states]
        q = f"SELECT * FROM positions WHERE state IN ({','.join('?' * len(st))})"
        args: list = list(st)
        if account_id: q += " AND account_id=?"; args.append(account_id)
        if market: q += " AND market=?"; args.append(market)
        with self._lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY opened_ts", args).fetchall()]

    def update_position(self, position_id: str, **fields) -> None:
        sets, args = ["updated_ts=?"], [self.clock()]
        for k, v in fields.items():
            if k not in ("qty", "entry_price", "reason", "strategy"):
                raise ValueError(k)
            sets.append(f"{k}=?"); args.append(str(v))
        args.append(position_id)
        with self._tx():
            self.db.execute(f"UPDATE positions SET {', '.join(sets)} WHERE position_id=?", args)

    def transition_position(self, position_id: str, new: P, event_id: str, reason: Optional[str] = None) -> bool:
        now = self.clock()
        with self._tx():
            row = self.db.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()
            if row is None:
                raise KeyError(position_id)
            if self.db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                return False
            cur = P(row["state"])
            real = check_position_transition(cur, new)
            if real:
                self.db.execute("UPDATE positions SET state=?, state_ts=?, updated_ts=?, reason=COALESCE(?, reason) WHERE position_id=?",
                                (new.value, now, now, reason, position_id))
            self._event(event_id, "position", position_id, cur.value, new.value if real else cur.value, now, reason or "")
            return real

    # ---------------------------------------------------------------- protection
    def get_protection(self, position_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row(self.db.execute("SELECT * FROM protection WHERE position_id=?", (position_id,)).fetchone())

    def set_protection(self, position_id: str, **f) -> Dict[str, Any]:
        now = self.clock()
        with self._tx():
            cur = self.get_protection(position_id)
            merged = dict(cur or dict(position_id=position_id, version=0, target_coid=None, stop_coid=None, stop_kind="software",
                                      tp_price="0", stop_price="0", qty="0", attempts=0, state="PENDING"))
            for k, v in f.items():
                if k not in merged:
                    raise ValueError(k)
                merged[k] = v if v is None or isinstance(v, (int, str)) else str(v)
            merged["updated_ts"] = now
            self.db.execute("""INSERT INTO protection(position_id,version,target_coid,stop_coid,stop_kind,tp_price,stop_price,qty,attempts,state,updated_ts)
                VALUES (:position_id,:version,:target_coid,:stop_coid,:stop_kind,:tp_price,:stop_price,:qty,:attempts,:state,:updated_ts)
                ON CONFLICT(position_id) DO UPDATE SET version=excluded.version,target_coid=excluded.target_coid,stop_coid=excluded.stop_coid,
                stop_kind=excluded.stop_kind,tp_price=excluded.tp_price,stop_price=excluded.stop_price,qty=excluded.qty,attempts=excluded.attempts,
                state=excluded.state,updated_ts=excluded.updated_ts""", merged)
            return merged

    # ---------------------------------------------------------------- flags / switches / kv
    def add_flag(self, severity: str, code: str, detail: str = "", account_id: Optional[str] = None, market: Optional[str] = None) -> None:
        with self._tx():
            self.db.execute("INSERT INTO flags(ts,severity,code,account_id,market,detail) VALUES (?,?,?,?,?,?)",
                            (self.clock(), severity, code, account_id, market, detail))

    def flags(self, code: Optional[str] = None, severity: Optional[str] = None) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM flags WHERE 1=1", []
        if code: q += " AND code=?"; args.append(code)
        if severity: q += " AND severity=?"; args.append(severity)
        with self._lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY id", args).fetchall()]

    def get_switches(self) -> Switches:
        with self._lock:
            r = self.db.execute("SELECT * FROM switches WHERE id=1").fetchone()
        return Switches(bool(r["allow_new_entries"]), bool(r["manage_existing_positions"]), float(r["updated_ts"]), r["reason"] or "")

    def set_switches(self, *, allow_new_entries: Optional[bool] = None, manage_existing_positions: Optional[bool] = None, reason: str = "") -> Switches:
        cur = self.get_switches()
        a = cur.allow_new_entries if allow_new_entries is None else bool(allow_new_entries)
        m = cur.manage_existing_positions if manage_existing_positions is None else bool(manage_existing_positions)
        now = self.clock()
        with self._tx():
            self.db.execute("UPDATE switches SET allow_new_entries=?, manage_existing_positions=?, updated_ts=?, reason=? WHERE id=1", (int(a), int(m), now, reason))
            self._event(f"switches:{now!r}:{a}:{m}", "switches", "1", f"{cur.allow_new_entries}/{cur.manage_existing_positions}", f"{a}/{m}", now, reason)
        sw = Switches(a, m, now, reason)
        if self.mirror:
            write_mirror(self.mirror, sw)
        return sw

    def kv_get(self, k: str, default=None):
        with self._lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return default if r is None else json.loads(r["v"])

    def kv_set(self, k: str, v) -> None:
        with self._tx():
            self.db.execute("INSERT INTO kv(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "positions", "fills", "events", "protection", "flags")}

    def close(self) -> None:
        with self._lock:
            self.db.close()


class _Tx:
    """Process-wide RLock + BEGIN IMMEDIATE (cross-process write lock)."""
    def __init__(self, ledger: Ledger):
        self.l = ledger
        self.nested = False

    def __enter__(self):
        self.l._lock.acquire()
        if self.l.db.in_transaction:
            self.nested = True
        else:
            self.l.db.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, et, ev, tb):
        try:
            if not self.nested:
                if et is None:
                    self.l.db.execute("COMMIT")
                else:
                    self.l.db.execute("ROLLBACK")
        finally:
            self.l._lock.release()
        return False
