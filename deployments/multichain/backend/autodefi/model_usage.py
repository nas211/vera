"""Persistent AI budgets and observed provider usage; no prompts or secrets are stored."""
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vela.db import get, put, transaction


class UsageLedger:
    def __init__(self, path, calls=200, tokens=2_000_000, max_input_bytes=64000, rates=None, clock=time.time):
        self.path = Path(path).resolve()
        if not 0 < calls <= 200 or not 0 < tokens <= 2_000_000 or not 0 < max_input_bytes <= 64000:
            raise ValueError('Invalid model budget')
        self.calls, self.tokens, self.max_input_bytes = calls, tokens, max_input_bytes
        self.clock = clock
        self.rates = None if rates is None else tuple(Decimal(str(x)) for x in rates)
        if self.rates is not None and (len(self.rates) != 2 or any(not x.is_finite() or x < 0 for x in self.rates)):
            raise ValueError('Invalid explicit model pricing')

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def reserve(self, payload):
        # The complete serialized request bounds text input bytes, with extra
        # framing headroom. Unknown provider usage retains this reservation.
        size = len(payload)
        if size > self.max_input_bytes:
            raise RuntimeError('AI_INPUT_LIMIT')
        data = json.loads(payload)
        output = data.get('max_output_tokens', data.get('max_completion_tokens'))
        if type(output) is not int or not 0 < output <= 3000:
            raise RuntimeError('AI_OUTPUT_LIMIT')
        reserve = size + 1024 + output
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        key = 'model-budget:' + day
        identifier = 'model-call:' + uuid.uuid4().hex
        with closing(self.connect()) as db, transaction(db):
            total = get(db, key, dict(calls=0, budgetTokens=0, inputTokens=0, outputTokens=0, unknownUsage=0))
            if total['calls'] >= self.calls or total['budgetTokens'] + reserve > self.tokens:
                raise RuntimeError('AI_PERSISTENT_BUDGET')
            total['calls'] += 1
            total['budgetTokens'] += reserve
            total['unknownUsage'] += 1
            put(db, key, total)
            put(db, identifier, dict(day=key, reserved=reserve, at=self.clock(), status='reserved',
                                     inputTokens=None, outputTokens=None, estimatedCostUsd=None))
        return identifier

    def finish(self, identifier, usage=None, status='response_received'):
        usage = usage if isinstance(usage, dict) else {}
        counts = (usage.get('input_tokens', usage.get('prompt_tokens')),
                  usage.get('output_tokens', usage.get('completion_tokens')))
        known = all(type(n) is int and 0 <= n <= 100_000_000 for n in counts)
        with closing(self.connect()) as db, transaction(db):
            call = get(db, identifier)
            if not call or call['status'] != 'reserved':
                return
            total = get(db, call['day'])
            call.update(status=status, finishedAt=self.clock())
            if known:
                total['budgetTokens'] += sum(counts) - call['reserved']
                total['inputTokens'] += counts[0]
                total['outputTokens'] += counts[1]
                total['unknownUsage'] -= 1
                call.update(inputTokens=counts[0], outputTokens=counts[1])
                if self.rates is not None:
                    call['estimatedCostUsd'] = str(sum(Decimal(n)*rate for n, rate in zip(counts, self.rates))/1_000_000)
                    call['priceBasis'] = 'Explicit configured rates; excludes discounts and invoice adjustments'
            put(db, call['day'], total)
            put(db, identifier, call)

    def summary(self):
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        with closing(self.connect()) as db:
            totals = get(db, 'model-budget:' + day, dict(calls=0, budgetTokens=0, inputTokens=0, outputTokens=0, unknownUsage=0))
        return dict(totals, dayUtc=day, callLimit=self.calls, tokenLimit=self.tokens,
                    pricingConfigured=self.rates is not None, finalBill=False,
                    note='Unknown usage retains a conservative reservation; provider invoices determine final charges')


class MeteredOpener:
    def __init__(self, opener, ledger):
        self.opener, self.ledger = opener, ledger

    def open(self, request, **kwargs):
        identifier = self.ledger.reserve(request.data)
        try:
            response = self.opener.open(request, **kwargs)
        except BaseException:
            # A timeout may still be billable: retain the full reservation.
            self.ledger.finish(identifier, status='request_failed_usage_unknown')
            raise
        return MeteredResponse(response, self.ledger, identifier)


class MeteredResponse:
    def __init__(self, response, ledger, identifier):
        self.response, self.ledger, self.identifier = response, ledger, identifier
        self.usage = None

    def __enter__(self):
        self.response.__enter__()
        return self

    def read(self, size):
        body = self.response.read(size)
        try:
            data = json.loads(body)
            self.usage = data.get('usage') if isinstance(data, dict) else None
        except (ValueError, UnicodeError):
            pass
        return body

    def __exit__(self, *args):
        try:
            return self.response.__exit__(*args)
        finally:
            self.ledger.finish(self.identifier, self.usage)
