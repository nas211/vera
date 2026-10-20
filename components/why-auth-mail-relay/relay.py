"""Retry pending signup confirmations through WHY's configured Resend sender.

Read-only access to Supabase Auth. Never confirms users or generates tokens.
Only the address attached to the pending confirmation receives its existing link.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import psycopg
import requests
from psycopg.rows import dict_row

LOG = logging.getLogger("why.auth_mail")
UTC = dt.timezone.utc
DELAY = 60
MAX_AGE = 1800
MAX_ATTEMPTS = 5


def identity(row):
    value = str(row['id']) + ':' + row['confirmation_token'] + ':' + row['confirmation_sent_at'].isoformat()
    return hashlib.sha256(value.encode()).hexdigest()


def eligible(row, now):
    stamp = row.get('confirmation_sent_at')
    return bool(not row.get('email_confirmed_at') and row.get('email')
                and row.get('confirmation_token') and stamp
                and DELAY <= (now - stamp).total_seconds() <= MAX_AGE)


def payload(row, base, sender):
    if urlsplit(base).scheme != 'https' or urlsplit(base).query or urlsplit(base).fragment:
        raise ValueError('invalid_auth_base')
    link = base.rstrip('/') + '/auth/v1/verify?' + urlencode({
        'token': row['confirmation_token'], 'type': 'signup',
        'redirect_to': 'https://whytradingbot.com/profile'})
    text = ('Please confirm your email address to finish setting up your WHY account:\n\n'
            + link + '\n\nIf you did not request this account, ignore this email.\n\nWHY Trading')
    return {'from': sender, 'to': [row['email']], 'subject': 'Confirm your WHY account',
            'text': text, 'html': '<p>Please confirm your email address to finish setting up your WHY account.</p>'
            '<p><a href="' + html.escape(link, quote=True) + '">Confirm my WHY account</a></p>'
            '<p>If you did not request this account, ignore this email.</p><p>WHY Trading</p>'}


class Relay:
    def __init__(self, state, base, sender, key, post=requests.post):
        self.db = sqlite3.connect(state)
        self.db.execute('''CREATE TABLE IF NOT EXISTS deliveries (
            fingerprint TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
            accepted INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            message_id TEXT, error TEXT, updated REAL NOT NULL)''')
        self.db.commit()
        self.base, self.sender, self.key, self.post = base, sender, key, post

    def mark_accepted(self, row, message_id, now):
        self.db.execute('''INSERT INTO deliveries(fingerprint,accepted,message_id,updated)
            VALUES (?,1,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
            accepted=1,message_id=excluded.message_id,error=NULL,updated=excluded.updated''',
            (identity(row), message_id, now))
        self.db.commit()

    def deliver(self, row, now):
        if not eligible(row, dt.datetime.fromtimestamp(now, UTC)):
            return 'ineligible'
        fp = identity(row)
        prior = self.db.execute('SELECT attempts,accepted,next_attempt FROM deliveries WHERE fingerprint=?', (fp,)).fetchone()
        if prior and (prior[1] or prior[0] >= MAX_ATTEMPTS or prior[2] > now):
            return 'suppressed'
        attempts = (prior[0] if prior else 0) + 1
        self.db.execute('''INSERT INTO deliveries(fingerprint,attempts,next_attempt,updated)
            VALUES (?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
            attempts=excluded.attempts,next_attempt=excluded.next_attempt,updated=excluded.updated''',
            (fp, attempts, now + min(30 * 2 ** (attempts - 1), 300), now))
        self.db.commit()
        try:
            r = self.post('https://api.resend.com/emails', headers={
                'Authorization': 'Bearer ' + self.key,
                'Idempotency-Key': 'why-signup-' + fp},
                json=payload(row, self.base, self.sender), timeout=15)
            if r.status_code not in (200, 201):
                raise RuntimeError('provider_http_' + str(r.status_code))
            message_id = r.json().get('id')
            if not isinstance(message_id, str) or not message_id:
                raise RuntimeError('provider_missing_message_id')
            self.mark_accepted(row, message_id, now)
            LOG.info('confirmation accepted by provider message_id=%s', message_id)
            return 'accepted'
        except Exception as exc:
            # Never log request bodies, URLs containing tokens, or response bodies.
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            self.db.execute('UPDATE deliveries SET error=? WHERE fingerprint=?', (error, fp))
            self.db.commit()
            LOG.error('confirmation delivery failed attempt=%s error=%s', attempts, error)
            return 'failed'


def pending(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=8,
                         options='-c default_transaction_read_only=on -c statement_timeout=8000') as conn:
        return conn.execute('''SELECT id,email,email_confirmed_at,confirmation_token,confirmation_sent_at
            FROM auth.users WHERE email_confirmed_at IS NULL AND deleted_at IS NULL
            AND (banned_until IS NULL OR banned_until < now())
            AND confirmation_token IS NOT NULL AND confirmation_token <> ''
            AND confirmation_sent_at BETWEEN now()-interval '30 minutes' AND now()-interval '60 seconds'
            ORDER BY confirmation_sent_at LIMIT 100''').fetchall()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--once', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    state = Path(os.environ.get('AUTH_MAIL_STATE', '/var/lib/why-auth-mail-relay'))
    state.mkdir(parents=True, exist_ok=True)
    relay = Relay(state / 'deliveries.sqlite3', os.environ['SUPABASE_URL'],
                  os.environ.get('AFFILIATE_MAIL_FROM', 'WHY Trading <noreply@whytradingbot.com>'),
                  os.environ['RESEND_API_KEY'])
    while True:
        status = {'checked_at': dt.datetime.now(UTC).isoformat(), 'ok': False}
        try:
            rows = pending(os.environ['SUPABASE_DB_URL'])
            counts = {}
            for row in rows:
                result = 'dry_run' if args.dry_run else relay.deliver(row, time.time())
                counts[result] = counts.get(result, 0) + 1
            exhausted = relay.db.execute('SELECT count(*) FROM deliveries WHERE accepted=0 AND attempts>=?', (MAX_ATTEMPTS,)).fetchone()[0]
            status.update(ok=not counts.get('failed') and not exhausted, pending=len(rows), results=counts, exhausted=exhausted)
        except Exception as exc:
            LOG.error('confirmation scan failed error=%s', type(exc).__name__)
            status['error'] = type(exc).__name__
        tmp = state / 'health.tmp'
        tmp.write_text(json.dumps(status) + '\n')
        tmp.replace(state / 'health.json')
        if args.once or args.dry_run:
            print(json.dumps(status))
            return
        time.sleep(15)


if __name__ == '__main__':
    main()
