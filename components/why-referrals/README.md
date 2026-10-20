# WHY Referrals — the $Y engine

One person, one code, one balance. A customer who arrives through the Telegram
bot and one who signs up on `whytradingbot.com/predict` are the same person, so
the identity this package works in is a **person**, not a Telegram id and not a
Predict user row.

    /opt/why-referrals/why_referrals/     the engine (stdlib only)
    /var/lib/why-referrals/referrals.db   the ledger  (SQLite, WAL, two writers)
    /etc/why-referrals/env                shared settings for every host
    ./whyrefctl                           the operator CLI

## What pays what

| Event | Who gets it | Default |
|---|---|---|
| Someone signs up on your code | you / them | 50 $Y / 25 $Y |
| ...and makes 3 predictions | you | 100 $Y |
| ...and pays for a subscription | you | 20% of the USD, in $Y, **every renewal** |
| Every 5th active referral | you | 250 $Y |
| A social task | you | per task, see below |

Tier comes from **lifetime** $Y and multiplies everything you earn:
SPARK ×1.00 → EMBER (500) ×1.10 → FORGE (2500) ×1.25 → INFERNO (10000) ×1.50.
Spending never demotes you; a clawback does.

$Y buys: **10 play-credits each** in WHY Predict, or **1 day of Premium per
40 $Y** (minimum 7 days) in the bot. It is not money and not yet a token —
`rules.rate_card()["notice"]` is the sentence shown to customers.

## What we can actually verify

This is the whole design constraint of the task layer:

**X only** — `WHYR_SOCIAL_PLATFORMS=x`. WHY runs one social account, `@whyprotocolx`.
The Instagram verifier is written and tested but switched off; add `instagram`
to that setting and it works, nothing else changes.

| Task | Verified how | Cost |
|---|---|---|
| X post / reply / quote | `publish.twitter.com/oembed` — author + text | free, no credentials |
| X like / repost / follow | **no free read** — attested, half reward, spot-checked | a paid X tier would fix it |
| Instagram comment (off) | IG Graph comment list, if ever turned on | free, with an account token |

`./whyrefctl probe` says which of these are live right now. A verifier that
cannot look returns UNKNOWN and the claim **waits for a human** — an outage at
X is never read as a user cheating.

To turn on automatic like/follow verification, put a paid X API bearer in
`/etc/why-referrals/env` as `WHYR_X_BEARER=` and restart nothing: it is read per
call. Attested tasks then verify for real and failed spot checks claw back.

## Operating it

    ./whyrefctl probe                    # what can we verify
    ./whyrefctl stats                    # people, $Y issued/spent, flags
    ./whyrefctl who telegram 7712        # one person, whole picture
    ./whyrefctl holds                    # awards parked by anti-abuse
    ./whyrefctl release <id> --note "known couple"
    ./whyrefctl claims                   # task claims waiting for a human
    ./whyrefctl approve <claim_id>
    ./whyrefctl grant predict 42 500 "goodwill"
    ./whyrefctl sweep                    # what the timer runs every 10 min

Add a task for a specific post:

    ./whyrefctl quest add --code REPLY_LAUNCH --kind REPLY \
      --title "Reply to the launch post" --y 120 \
      --url https://x.com/whyprotocolx/status/<id> --marker '#WHYTRADE'

`--marker` is what makes a reply checkable: oEmbed gives us the author and the
text but not what a post was replying to, so the required word is the link
between the claim and the campaign. Quote-posts do carry the target and report
`parent_confirmed: true`.

## Anti-abuse

REFUSED outright (nothing is recorded as owed): referring yourself, a second
code on an account that already has one, a circular A→B→A, an account older
than an hour, a suspended referrer.

HELD for review (a real award, parked — `whyrefctl holds`): shared IP with the
referrer, more than 3 binds from one IP in a day, more than 20 referrals in a
day. Two people on one home wifi are the most ordinary referral there is, so
these are never silent refusals.

Also: one proof URL can pay exactly once across every task and every person;
one social handle belongs to one person forever; handles are proven by posting
a nonce we generate.

## Integrity

`y_ledger` is append-only, enforced by SQLite triggers — the balance is
`SUM(delta)` and there is no balance column to disagree with it. Every earn
carries a UNIQUE `award_key` naming what it pays for (`SUB:payment:57`), so a
replayed event pays nothing. A merge never rewrites ledger rows; it sets
`persons.merged_into` and the reads resolve the group.

## Hosts

* **The page: `whytradingbot.com/earn`** — `why_predict/static/earn.html`, served by
  the Predict app and proxied by nginx (`location ^~ /earn`). It is what an
  invite link points at (`/earn?r=WHY-XXXXXX`): signed out it explains the
  programme, lists the live tasks and takes the signup; signed in it is the
  dashboard — code, balance, tier, tasks, invitees, history. The `Earn $Y` tab
  inside /predict links here rather than duplicating it.
* **The waitlist** on the marketing site: joining puts you in the ledger too
  (`POST /api/referral/waitlist`, server-to-server, bearer token + no
  X-Forwarded-For), so a waitlist invite earns $Y and fills the 3-friend
  priority queue. Waitlist referrals stay PENDING — `clean_referrals()` is the
  "verified friends" count, because somebody on a waitlist has not used
  anything yet.
* `why_predict/referrals.py` + `/api/referral/*`. `rates`, `quests` and
  `leaderboard` are PUBLIC (a landing page must be able to state its terms
  before anyone has an account); `me`, `convert`, `handle` and `claim` need a
  login.
* `tgbot/referrals.py` + `/invite`, `/tasks`, `/link`, the `🎁 Invite & $Y`
  button and the `/start r_CODE` deep link.

Both adapters fail soft: if this package is missing or its database is locked,
they lose a card and nothing else. A signup must never fail because a rewards
programme did.

$Y **cannot** grant paid product from here. `spend_for_sub_days()` debits the
points and returns the days; `billing.grant_days()` in the bot is the only code
that moves `plan_until`.

Tests: `python3 -m pytest tests/` (55), `tgbot/test_referrals.py` (20),
`why_predict/tests/test_referrals_api.py` (13).
