"""Canonical signal source: the running strategy book's journal.

yellow_paper.py (unchanged) appends OPEN / CLOSE records to state/journal.jsonl. Tailing
that file turns each OPEN into ONE CanonicalSignal with a deterministic id
("wy:<market>:<side>:<strategy>:<opened_ms>") that every tenant receives; CLOSE records
become CloseSignals. The execution router sends TARGET/STOP closes only to the isolated
Paper ledger (live venues manage their own protection orders) and sends TIMEOUT closes
to every execution path. The strategy is therefore executed exactly once and only
re-sized per customer. The file offset is persisted so a restart never replays old
signals; on first start the history is skipped."""
from __future__ import annotations
import json
import os
import tempfile
import time
from decimal import Decimal
from typing import Callable, List, Optional, Union

from .log import get_logger, safe_exc
from .tenants import CanonicalSignal, CloseSignal

log = get_logger("fanout")


def signal_id_for(rec: dict) -> str:
    return f"wy:{rec['market']}:{rec['side']}:{rec['strategy']}:{int(rec['ts'])}"


class JournalSignalSource:
    def __init__(self, journal_path: str, offset_path: str, clock: Callable[[], float] = time.time, max_age_s: float = 120.0):
        self.path, self.offset_path, self.clock, self.max_age_s = journal_path, offset_path, clock, max_age_s
        self.offset: Optional[int] = None
        self.skipped_stale = 0
        self.seen = 0

    def _load_offset(self) -> None:
        try:
            with open(self.offset_path) as f:
                self.offset = int(json.load(f).get("offset", 0))
        except (OSError, ValueError):
            try:
                self.offset = os.path.getsize(self.path)     # first start: skip history
            except OSError:
                self.offset = 0
            self._save_offset()

    def _save_offset(self) -> None:
        d = os.path.dirname(self.offset_path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".off-")
        with os.fdopen(fd, "w") as f:
            json.dump({"offset": self.offset, "ts": self.clock()}, f)
        os.replace(tmp, self.offset_path)

    def poll(self) -> List[Union[CanonicalSignal, CloseSignal]]:
        if self.offset is None:
            self._load_offset()
        out: List[Union[CanonicalSignal, CloseSignal]] = []
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return out
        if size < self.offset:          # rotated/truncated
            self.offset = 0
        if size == self.offset:
            return out
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            return out
        consumed = last_nl + 1
        now = self.clock()
        for raw in chunk[:consumed].split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode())
            except Exception:
                continue
            ev = rec.get("event")
            try:
                ts = float(rec.get("ts", 0)) / 1000.0
                if ev == "OPEN":
                    if now - ts > self.max_age_s:
                        self.skipped_stale += 1
                        continue
                    out.append(CanonicalSignal(signal_id=signal_id_for(rec), market=str(rec["market"]), direction=str(rec["side"]),
                                               strategy=str(rec["strategy"]), ref_price=Decimal(str(rec["entry"])), ts=ts,
                                               detail=dict(rec.get("detail") or {})))
                    self.seen += 1
                elif ev == "CLOSE":
                    reason = str(rec.get("reason") or "STRATEGY_CLOSE").upper()
                    out.append(CloseSignal(market=str(rec["market"]), direction=str(rec["side"]), reason=reason, ts=ts))
            except Exception as e:
                log.warning("bad journal record skipped: %s", safe_exc(e))
        self.offset += consumed
        self._save_offset()
        return out
