-- WHY Referrals schema v1.
-- $Y is a POINTS ledger, not money. Balance == SUM(y_ledger.delta).

CREATE TABLE IF NOT EXISTS persons (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  REAL NOT NULL,
  suspended   INTEGER NOT NULL DEFAULT 0,
  note        TEXT NOT NULL DEFAULT '',
  merged_into INTEGER REFERENCES persons(id));   -- set when this row lost a merge

-- One row per account on one host. ('predict', '42') and ('telegram', '7712')
-- can point at the same person; that is the whole point of the table.
CREATE TABLE IF NOT EXISTS person_subjects (
  platform    TEXT NOT NULL,                     -- predict | telegram
  external_id TEXT NOT NULL,
  person_id   INTEGER NOT NULL REFERENCES persons(id),
  display     TEXT NOT NULL DEFAULT '',
  linked_at   REAL NOT NULL,
  PRIMARY KEY (platform, external_id));
CREATE INDEX IF NOT EXISTS ix_subj_person ON person_subjects(person_id);

CREATE TABLE IF NOT EXISTS referral_codes (
  code       TEXT PRIMARY KEY,
  person_id  INTEGER NOT NULL REFERENCES persons(id),
  created_at REAL NOT NULL,
  active     INTEGER NOT NULL DEFAULT 1,
  label      TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_code_person ON referral_codes(person_id);

-- FIRST TOUCH WINS AND IS PERMANENT: referee_id is UNIQUE, so a person can be
-- referred exactly once, ever. Re-binding under a second code is not a feature
-- request, it is how you launder a self-referral through a friend.
CREATE TABLE IF NOT EXISTS referrals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer_id   INTEGER NOT NULL REFERENCES persons(id),
  referee_id    INTEGER NOT NULL UNIQUE REFERENCES persons(id),
  code          TEXT NOT NULL,
  platform      TEXT NOT NULL,                   -- where the bind happened
  ip            TEXT,
  bound_at      REAL NOT NULL,
  status        TEXT NOT NULL,                   -- PENDING | QUALIFIED | REJECTED
  qualified_at  REAL,
  evidence      TEXT NOT NULL DEFAULT '',
  reject_reason TEXT NOT NULL DEFAULT '',
  CHECK (referrer_id <> referee_id));
CREATE INDEX IF NOT EXISTS ix_ref_referrer ON referrals(referrer_id, status);

-- APPEND-ONLY. The triggers below are not decoration: this ledger is the
-- record of what everyone is owed at a token event, and a balance that can be
-- quietly edited is not a record of anything.
CREATE TABLE IF NOT EXISTS y_ledger (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  delta     INTEGER NOT NULL,
  kind      TEXT NOT NULL,                       -- EARN | SPEND | ADJUST | CLAWBACK
  reason    TEXT NOT NULL,                       -- REFERRAL_SIGNUP | QUEST | ...
  ref       TEXT NOT NULL DEFAULT '',
  -- The idempotency key. Every earn names the exact thing it pays for
  -- ('SUB:payment:57'), so replaying an event cannot pay twice.
  award_key TEXT UNIQUE,
  ts        REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_yl_person ON y_ledger(person_id);

CREATE TRIGGER IF NOT EXISTS trg_yl_no_update BEFORE UPDATE ON y_ledger
BEGIN SELECT RAISE(ABORT, 'y_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_yl_no_delete BEFORE DELETE ON y_ledger
BEGIN SELECT RAISE(ABORT, 'y_ledger is append-only'); END;

-- An award that anti-abuse would not pay immediately. It is a REAL award, held
-- for a human to look at, not a refusal — see config.HOLD_ON_FLAG.
CREATE TABLE IF NOT EXISTS held_awards (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id    INTEGER NOT NULL REFERENCES persons(id),
  delta        INTEGER NOT NULL,
  reason       TEXT NOT NULL,
  ref          TEXT NOT NULL DEFAULT '',
  award_key    TEXT NOT NULL UNIQUE,
  flag_kind    TEXT NOT NULL DEFAULT '',
  created_at   REAL NOT NULL,
  status       TEXT NOT NULL DEFAULT 'HELD',     -- HELD | RELEASED | REFUSED
  decided_at   REAL,
  decided_by   TEXT NOT NULL DEFAULT '',
  decision_note TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS abuse_flags (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind      TEXT NOT NULL,
  person_id INTEGER,
  ip        TEXT,
  detail    TEXT NOT NULL DEFAULT '{}',
  ts        REAL NOT NULL,
  reviewed  INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_flag_person ON abuse_flags(person_id);

-- One-time token that collapses a Telegram id and a Predict account into one
-- person. Short TTL and single use, because whoever holds it inherits a balance.
CREATE TABLE IF NOT EXISTS link_tokens (
  token      TEXT PRIMARY KEY,
  person_id  INTEGER NOT NULL REFERENCES persons(id),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at    REAL,
  used_by    TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS audit (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     REAL NOT NULL,
  actor  TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
