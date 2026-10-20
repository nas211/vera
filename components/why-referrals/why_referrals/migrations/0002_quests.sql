-- Social quests: paid tasks on our own posts (reply, quote, comment, like,
-- follow), and the accounts that are allowed to claim them.

-- A handle belongs to ONE person, forever. Without that constraint the whole
-- quest layer is a per-handle faucet that anyone with two accounts can double.
CREATE TABLE IF NOT EXISTS social_accounts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id      INTEGER NOT NULL REFERENCES persons(id),
  platform       TEXT NOT NULL,                  -- x | instagram
  handle         TEXT NOT NULL,
  handle_lc      TEXT NOT NULL,
  verified       INTEGER NOT NULL DEFAULT 0,
  -- Ownership proof: this nonce has to appear in a post BY that handle. It is
  -- the only free way to know the handle is theirs and not their favourite
  -- influencer's, whom they would otherwise happily submit as proof.
  nonce          TEXT NOT NULL DEFAULT '',
  nonce_expires  REAL NOT NULL DEFAULT 0,
  linked_at      REAL NOT NULL,
  verified_at    REAL,
  verify_method  TEXT NOT NULL DEFAULT '',
  UNIQUE (platform, handle_lc));
CREATE INDEX IF NOT EXISTS ix_social_person ON social_accounts(person_id);

-- Our own posts. The daemon sweeps these for engagement; a quest points at one.
CREATE TABLE IF NOT EXISTS our_posts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  platform    TEXT NOT NULL,
  post_id     TEXT NOT NULL,
  url         TEXT NOT NULL DEFAULT '',
  caption     TEXT NOT NULL DEFAULT '',
  posted_at   REAL,
  added_at    REAL NOT NULL,
  auto_quest  INTEGER NOT NULL DEFAULT 0,        -- open a quest for it automatically
  last_swept  REAL,
  UNIQUE (platform, post_id));

CREATE TABLE IF NOT EXISTS quests (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  code           TEXT NOT NULL UNIQUE,
  platform       TEXT NOT NULL,                  -- x | instagram | any
  kind           TEXT NOT NULL,                  -- REPLY|QUOTE|POST|COMMENT|LIKE|REPOST|FOLLOW
  target_ref     TEXT NOT NULL DEFAULT '',       -- our post id / handle to follow
  target_url     TEXT NOT NULL DEFAULT '',
  title          TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  y_reward       INTEGER NOT NULL,
  -- AUTO   : machine-checkable for free (oEmbed authorship, IG comment list)
  -- ATTEST : no free read path (likes, follows) — pays ATTESTED_REWARD_PCT and
  --          can be spot-checked and clawed back
  -- MANUAL : a human looks at it
  verify_mode    TEXT NOT NULL DEFAULT 'AUTO',
  -- A marker the post must contain (hashtag, @handle, campaign word). Empty
  -- means authorship alone is enough.
  marker         TEXT NOT NULL DEFAULT '',
  active         INTEGER NOT NULL DEFAULT 1,
  starts_at      REAL,
  ends_at        REAL,
  max_claims     INTEGER NOT NULL DEFAULT 0,     -- 0 = unlimited
  repeat_every_sec REAL NOT NULL DEFAULT 0,      -- 0 = once per person, ever
  created_at     REAL NOT NULL,
  created_by     TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_quest_active ON quests(active, platform);

CREATE TABLE IF NOT EXISTS quest_claims (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  quest_id     INTEGER NOT NULL REFERENCES quests(id),
  person_id    INTEGER NOT NULL REFERENCES persons(id),
  -- 'once' for a one-shot quest, else the repeat bucket, so a daily task is a
  -- new row each day and the UNIQUE below still stops double-claiming today.
  slot         TEXT NOT NULL DEFAULT 'once',
  handle       TEXT NOT NULL DEFAULT '',
  proof_url    TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL,                    -- PENDING|VERIFIED|PAID|REJECTED|CLAWED
  attested     INTEGER NOT NULL DEFAULT 0,
  spot_check   INTEGER NOT NULL DEFAULT 0,
  y_awarded    INTEGER NOT NULL DEFAULT 0,
  verifier     TEXT NOT NULL DEFAULT '',
  detail       TEXT NOT NULL DEFAULT '',
  submitted_at REAL NOT NULL,
  checked_at   REAL,
  paid_at      REAL,
  ip           TEXT,
  UNIQUE (quest_id, person_id, slot));
CREATE INDEX IF NOT EXISTS ix_claim_status ON quest_claims(status, submitted_at);
CREATE INDEX IF NOT EXISTS ix_claim_person ON quest_claims(person_id);

-- One proof URL can only ever pay once, across every quest and every person.
CREATE UNIQUE INDEX IF NOT EXISTS ux_claim_proof
  ON quest_claims(proof_url) WHERE proof_url <> '';
