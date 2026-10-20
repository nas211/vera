#!/usr/bin/env python3
"""SHARED AGENT TRANSPORT — the one place a hosted model is called from.

Both agents (direction_agent.py, promotion_agent.py) speak to the same vendor
over the same OpenAI-compatible endpoint, against the same account quota. They
were doing it with two near-identical copies of the same code, which is how the
same three failures had to be diagnosed twice. This is that code, once, plus
the two things the copies did not have: a classification of WHY a call failed,
and a governor that stops the account's daily token budget being spent by
lunchtime.

WHAT WENT WRONG, AND WHAT EACH PART HERE ANSWERS.

1. `json_validate_failed` with an EMPTY `failed_generation`. Measured against
   qwen/qwen3.6-27b on 2026-08-17, reproduced deliberately: it is a REASONING
   model, its thinking tokens are billed against `max_tokens`, and under
   `response_format: json_object` the vendor validates the completion server
   side. Give it a budget thinking can swallow whole and it emits zero content
   tokens, so the validator is handed "" and answers HTTP 400. The error names
   JSON; the cause is the token budget. Nothing about the schema was ever
   wrong.

   Two answers, both here. `reasoning_effort` is passed through so thinking can
   be collapsed (see the config note — for this model that is permanent), and
   a reply that fails validation because it was TRUNCATED is retried once with
   thinking off rather than reported as a broken schema. Suppressing the error
   would mean catching the 400 and returning neutral; this fixes the cause and
   then fails loudly if it recurs.

2. Structured output that is actually structured. `json_object` only promises
   valid JSON — the shape is a request in the prompt. This vendor DOES support
   strict `json_schema` on this model (verified live), which makes a
   wrong-shaped reply unrepresentable rather than discouraged. It is opt-in
   (`OPENAI_COMPAT_JSON_SCHEMA=1`) because schema support is exactly the thing
   that varies between "compatible" vendors, and the default must stay the
   portable one. `strict_schema` adapts a tool schema to the strict dialect:
   every object closed, every property required, and numeric `enum`s dropped
   (0.0 and 1.0 read as integers and the validator refuses a mixed type).

3. HTTP 429 on tokens-per-DAY. 200,000/day, and two books running two agents
   each at the shipped refresh intervals want roughly 1.1M. The budget was
   therefore always going to run out; the only question was whether it ran out
   in the middle of the day and stayed out. It did: the last hours of every day
   were a solid wall of TPD 429s, and every one of them landed in the record as
   a 50/50 book — the same shape a genuine "the market pays neither side" read
   has.

   So: a token bucket, refilled continuously at budget/day, SHARED between the
   two books through one file, and consulted BEFORE the call. When there is not
   enough in the bucket the call does not happen — the caller is told, in a
   machine-readable field, that this was the governor and not the market. The
   effect is a model that answers a bit less often but answers all day, instead
   of answering constantly until 17:00 and then not at all.

4. DIRECTION OUTRANKS PROMOTION, and the governor is where that is enforced.
   The direction read sets how the book leans and is consumed every cycle; the
   promotion read reorders an armed list every half hour. Under budget pressure
   the promotion agent must give way, not compete. Two rules do it:

     * promotion NEVER borrows. Its bucket is its own and that is all it gets.
     * promotion is refused outright while the direction bucket is below
       DIRECTION_FLOOR_TOKENS — a protected reserve holding several direction
       reads, so the cheap frequent read cannot be crowded out by the
       expensive infrequent one.
     * direction MAY borrow the shortfall from promotion's bucket when its own
       runs dry. So the degradation order is: defer promotion, keep direction,
       and only starve direction when the account has nothing left at all.

Every failure returns a dict with `kind`, so a caller can tell a governor
deferral from a rate limit from a truncated reply from a dead socket. The
vocabulary is fixed and each value means exactly one thing:

    budget_deferred       the GOVERNOR held this call back to protect the day
    rate_limit            the vendor said 429 (quota_scope tpd or tpm)
    truncated             the reply hit max_tokens before the JSON closed
    invalid_output        a reply arrived and was not the shape asked for
    provider_error        the vendor returned some other HTTP failure
    provider_unreachable  the socket, DNS or timeout — no reply at all
    config_error          not configured to call anything (no key)

None of these is a judgement. `kind` is the field that stops a provider outage
masquerading as one.

stdlib only, like the rest of the repo.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

# --- the daily token governor ----------------------------------------------
# ONE file for the whole box, deliberately NOT inside a book's state dir. This
# is a property of the API ACCOUNT, not of a shelf: both books spend the same
# 200,000 tokens, so a per-book counter would let each of them believe it had
# the whole budget. It is not trading state and merges nothing between venues.
QUOTA_FILE = os.environ.get("AGENT_QUOTA_FILE", "state/agent_quota.json")
# 0 disables the governor entirely — the setting for a paid tier, and the
# default for any vendor whose limits we have not measured.
DAILY_BUDGET = float(os.environ.get("AGENT_TOKEN_BUDGET_PER_DAY", "0"))
# How the day is split. The direction agent runs on a 10-minute clock and is
# cheap; promotion runs on a 30-minute clock and costs ~3x per call. Shares
# rather than a shared pool so the frequent agent cannot starve the one that
# decides which strategies get slots.
SHARES = {
    "direction": float(os.environ.get("AGENT_TOKEN_SHARE_DIRECTION", "0.55")),
    "promotion": float(os.environ.get("AGENT_TOKEN_SHARE_PROMOTION", "0.45")),
}
# The bucket's ceiling, as a fraction of that agent's daily allowance. This is
# how much unspent budget may accumulate while nothing is calling — a small
# burst so a restart is not penalised, not a licence to spend the day at once.
BURST_FRACTION = float(os.environ.get("AGENT_TOKEN_BURST_FRACTION", "0.08"))
# Floor under the ceiling above. Without it a small share plus a small burst
# fraction can produce a bucket too shallow to ever hold ONE call's estimate,
# and the agent would be blocked forever by arithmetic rather than by cost.
BURST_FLOOR = float(os.environ.get("AGENT_TOKEN_BURST_FLOOR", "12000"))
# THE PRIORITY RULE, as a number. Promotion is refused while direction holds
# less than this — roughly three direction reads at ~3,000 tokens each, so the
# read that runs every cycle always has a few in hand no matter what the read
# that runs every half hour is doing. Direction itself is never checked against
# it; the floor exists to be protected FOR direction, not FROM it.
DIRECTION_FLOOR_TOKENS = float(os.environ.get("AGENT_TOKEN_DIRECTION_FLOOR", "9000"))
# Which agent yields to which. Direction may take the shortfall from the
# agents listed here when its own bucket is dry; nothing may borrow from
# direction.
BORROW_FROM = {"direction": ["promotion"]}
# Vendors bill prompt+completion; we can only estimate the prompt before the
# call. 3.6 chars/token measured against this repo's own briefs (7,250 chars
# -> 1,926 prompt tokens on the direction read).
CHARS_PER_TOKEN = float(os.environ.get("AGENT_CHARS_PER_TOKEN", "3.6"))


def _now() -> float:
    return time.time()


def _read_state() -> Dict[str, Any]:
    try:
        with open(QUOTA_FILE, "r") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _write_state(doc: Dict[str, Any]) -> None:
    """Atomic, and never fatal.

    A governor that can crash the agent it governs is worse than no governor.
    Every failure here degrades to "no accounting this call", which costs
    tokens; raising would cost the read.
    """
    try:
        os.makedirs(os.path.dirname(QUOTA_FILE) or ".", exist_ok=True)
        tmp = f"{QUOTA_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, QUOTA_FILE)
    except Exception:
        pass


def _with_lock(fn):
    """Serialise read-modify-write across the two book processes.

    Both books call the same vendor from background threads. Without this the
    two of them interleave a read and a write and one call's spend disappears,
    which is the failure mode that ends with a 429 the governor was supposed to
    have prevented. flock is advisory and process-local platforms differ, so a
    failure to lock proceeds unlocked rather than blocking the read.
    """
    def _wrapped(*a, **kw):
        try:
            import fcntl
        except Exception:
            return fn(*a, **kw)
        try:
            os.makedirs(os.path.dirname(QUOTA_FILE) or ".", exist_ok=True)
            with open(QUOTA_FILE + ".lock", "a+") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                try:
                    return fn(*a, **kw)
                finally:
                    fcntl.flock(lk, fcntl.LOCK_UN)
        except Exception:
            return fn(*a, **kw)
    return _wrapped


def _bucket(state: Dict[str, Any], agent: str, now: float) -> Dict[str, float]:
    """Current bucket for `agent`, refilled to `now`.

    A continuously-refilled bucket rather than a per-calendar-day counter
    because the vendor's own TPD window is ROLLING — its 429 says "try again in
    18m", not "try again tomorrow". A midnight reset would model a limit this
    account does not have.
    """
    share = max(0.0, SHARES.get(agent, 0.25))
    daily = DAILY_BUDGET * share
    rate = daily / 86400.0
    cap = max(daily * BURST_FRACTION, BURST_FLOOR)
    b = dict((state.get("buckets") or {}).get(agent) or {})
    tokens = float(b.get("tokens", cap))     # a fresh bucket starts full
    ts = float(b.get("ts", now))
    tokens = min(cap, tokens + rate * max(0.0, now - ts))
    return {"tokens": tokens, "ts": now, "cap": cap, "rate": rate, "daily": daily}


def estimate_tokens(system: str, user: str, max_tokens: int) -> int:
    """What this call will cost the budget, before it is made.

    prompt + max_tokens, matching how the vendor ADMITS a request: its 429 body
    reads "Requested 6155" for a 2,155-token prompt with max_tokens=4000. An
    oversized reply ceiling is therefore not a free option, it is spend.
    """
    chars = len(system or "") + len(user or "")
    return int(chars / CHARS_PER_TOKEN) + int(max_tokens)


@_with_lock
def reserve(agent: str, est: int, now: Optional[float] = None) -> Tuple[bool, Dict[str, Any]]:
    """May `agent` spend `est` tokens now? Deducts them if so.

    Returns (False, why) when the governor defers the call, or when a previous
    429 left a cooldown that has not expired. `why` carries the numbers so the
    log line says how long the wait is rather than only that there was one.

    The priority rule lives here: promotion is deferred while direction's
    reserve is low, and direction may take promotion's tokens when its own are
    gone. A cooldown imposed by the vendor stops both — that one is not ours to
    prioritise around.
    """
    if DAILY_BUDGET <= 0:
        return True, {}
    now = _now() if now is None else now
    state = _read_state()

    cool = state.get("cooldown") or {}
    until = float(cool.get("until") or 0.0)
    if until > now:
        return False, {"kind": "rate_limit", "quota_scope": cool.get("scope") or "unknown",
                       "retry_after_sec": round(until - now, 1),
                       "detail": str(cool.get("detail") or "")[:200],
                       "reason": "vendor cooldown from an earlier 429"}

    b = _bucket(state, agent, now)
    buckets = {agent: b}

    # PRIORITY, step one: promotion does not touch the day while direction is
    # running low. Checked before promotion's own balance, because the point is
    # that promotion can be perfectly able to afford a call and still not be
    # entitled to it.
    if agent != "direction":
        d = _bucket(state, "direction", now)
        buckets["direction"] = d
        if d["tokens"] < DIRECTION_FLOOR_TOKENS:
            short = DIRECTION_FLOOR_TOKENS - d["tokens"]
            return False, {"kind": "budget_deferred", "deferred_for": "direction",
                           "retry_after_sec": round(short / d["rate"], 1)
                           if d["rate"] > 0 else None,
                           "direction_tokens_left": round(d["tokens"]),
                           "direction_floor": DIRECTION_FLOOR_TOKENS,
                           "estimate": est,
                           "reason": "deferred to protect the direction agent's "
                                     "token reserve"}

    # PRIORITY, step two: direction takes what it needs from the agents that
    # yield to it before it accepts being deferred itself.
    borrowed: Dict[str, float] = {}
    if b["tokens"] < est:
        for donor in BORROW_FROM.get(agent, []):
            if b["tokens"] >= est:
                break
            db = buckets.get(donor) or _bucket(state, donor, now)
            buckets[donor] = db
            take = min(db["tokens"], est - b["tokens"])
            if take > 0:
                db["tokens"] -= take
                b["tokens"] += take
                borrowed[donor] = round(take)

    if b["tokens"] < est:
        short = est - b["tokens"]
        return False, {"kind": "budget_deferred",
                       "retry_after_sec": round(short / b["rate"], 1)
                       if b["rate"] > 0 else None,
                       "budget_tokens_left": round(b["tokens"]),
                       "estimate": est,
                       "reason": "daily token budget deferred this call"}

    b["tokens"] -= est
    out_buckets = state.setdefault("buckets", {})
    for name, bk in buckets.items():
        out_buckets[name] = {"tokens": bk["tokens"], "ts": now}
    _write_state(state)
    why = {"budget_tokens_left": round(b["tokens"]), "estimate": est}
    if borrowed:
        why["borrowed"] = borrowed
    return True, why


@_with_lock
def settle(agent: str, est: int, actual: int, now: Optional[float] = None) -> None:
    """Correct the reservation once the vendor reports what it really cost."""
    if DAILY_BUDGET <= 0 or not actual:
        return
    now = _now() if now is None else now
    state = _read_state()
    b = _bucket(state, agent, now)
    b["tokens"] = min(b["cap"], b["tokens"] + (est - actual))
    state.setdefault("buckets", {})[agent] = {"tokens": b["tokens"], "ts": now}
    _write_state(state)


@_with_lock
def note_cooldown(seconds: float, scope: str, detail: str,
                  now: Optional[float] = None) -> None:
    """Record a vendor-imposed wait so the OTHER book does not walk into it.

    The two books share one account. Without this the second process spends the
    whole cooldown discovering the same 429 for itself, once per refresh.
    """
    now = _now() if now is None else now
    state = _read_state()
    prev = float((state.get("cooldown") or {}).get("until") or 0.0)
    state["cooldown"] = {"until": max(prev, now + max(0.0, seconds)),
                         "scope": scope, "detail": str(detail)[:300],
                         "noted_at": now}
    _write_state(state)


def budget_status(now: Optional[float] = None) -> Dict[str, Any]:
    """For the operator: what is left, and is a cooldown running."""
    now = _now() if now is None else now
    state = _read_state()
    out: Dict[str, Any] = {"daily_budget": DAILY_BUDGET, "enabled": DAILY_BUDGET > 0}
    for agent in SHARES:
        b = _bucket(state, agent, now)
        out[agent] = {"tokens_available": round(b["tokens"]),
                      "bucket_cap": round(b["cap"]),
                      "refill_per_hour": round(b["rate"] * 3600)}
    cool = state.get("cooldown") or {}
    until = float(cool.get("until") or 0.0)
    if until > now:
        out["cooldown_sec_left"] = round(until - now, 1)
        out["cooldown_scope"] = cool.get("scope")
        out["cooldown_detail"] = cool.get("detail")
    return out


# --- vendor error classification -------------------------------------------
# The body carries the real reason. Losing it is what made the Anthropic outage
# take hours to spot; keeping it as prose is what let a 429 read like a
# judgement. Both agents now get a `kind` they can act on.
_RETRY_RE = re.compile(r"try again in\s+(?:(\d+)m)?([\d.]+)s", re.I)


def retry_after_seconds(body: str) -> Optional[float]:
    m = _RETRY_RE.search(body or "")
    if not m:
        return None
    mins = float(m.group(1) or 0)
    return mins * 60.0 + float(m.group(2))


def classify_http(code: int, body: str) -> Dict[str, Any]:
    """Turn an HTTP failure into something the caller can branch on.

    kinds:
      rate_limit      429. `quota_scope` says tpd (the day's allowance, a long
                      wait) or tpm (a burst limit, seconds) — same status code,
                      opposite responses.
      too_large       this one request cannot fit the window at any time.
      truncated       the model hit max_tokens before closing the JSON.
      invalid_output  a document arrived and was the wrong shape.
      provider_error  anything else, reported verbatim.
    """
    low = (body or "").lower()
    detail = (body or "")[:300]
    after = retry_after_seconds(body)
    if code == 429 or "rate limit reached" in low:
        scope = "tpd" if "per day" in low or "(tpd)" in low else (
            "tpm" if "per minute" in low or "(tpm)" in low else "unknown")
        return {"kind": "rate_limit", "quota_scope": scope,
                "retry_after_sec": after, "detail": detail}
    if code == 413 or "request too large" in low:
        return {"kind": "too_large", "retry_after_sec": after, "detail": detail}
    if "json_validate_failed" in low or "failed to validate json" in low \
            or "failed to generate json" in low:
        # `failed_generation` empty, or the vendor saying so in words, means the
        # model never got to the body — it spent the budget thinking. That is a
        # TRUNCATION and it is recoverable. A genuinely malformed document is
        # not, must not be retried into the same wall, and is a different kind.
        truncated = ('"failed_generation":""' in (body or "").replace(" ", "")
                     or "max completion tokens reached" in low)
        return ({"kind": "truncated", "truncated": True, "detail": detail}
                if truncated else {"kind": "invalid_output", "detail": detail})
    return {"kind": "provider_error", "detail": detail}


# --- strict schema adaptation ----------------------------------------------
def strict_schema(node: Any) -> Any:
    """A tool input_schema, in the dialect strict json_schema mode accepts.

    Three differences, each measured against the vendor's own 400s:
      * every object must set additionalProperties:false
      * every property must appear in `required`
      * a numeric `enum` is refused ("cannot include both 'integer' and
        'number'") because 0.0 and 1.0 parse as integers. The enum exists for
        llama.cpp's grammar, which honours it and ignores minimum/maximum; the
        hosted path does not need it, and promotion_agent._validate already
        treats an off-scale conviction as untrusted.
    """
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if out.get("type") == "object":
        props = {k: strict_schema(v) for k, v in (out.get("properties") or {}).items()}
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    elif out.get("type") == "array" and "items" in out:
        out["items"] = strict_schema(out["items"])
    elif out.get("type") == "number":
        out.pop("enum", None)
    return out


# --- the call ---------------------------------------------------------------
JSON_SCHEMA_MODE = os.environ.get("OPENAI_COMPAT_JSON_SCHEMA", "0") == "1"


def _post(url: str, body: Dict[str, Any], key: str, user_agent: str,
          timeout: float) -> Dict[str, Any]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 # Groq sits behind Cloudflare, which blocks urllib's default
                 # "Python-urllib/3.x" agent with a 403 body of "error code:
                 # 1010" — measured 2026-08-14. Not an auth failure.
                 "User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _extract(doc: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(parsed, error). Fenced replies are unwrapped; nothing else is coaxed."""
    try:
        choice = doc["choices"][0]
        content = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None, {"kind": "invalid_output", "error": "reply had no message content"}
    finish = str((doc.get("choices") or [{}])[0].get("finish_reason") or "")
    if not content:
        # An empty body with finish_reason=length is the same truncation as the
        # 400 above, arriving as a 200 because strict schema mode does not
        # server-side validate. Same cause, same name.
        if finish == "length":
            return None, {"kind": "truncated", "truncated": True,
                          "error": "empty reply truncated at max_tokens"}
        return None, {"kind": "invalid_output", "error": "returned empty content"}
    if finish == "length":
        return None, {"kind": "truncated", "truncated": True,
                      "error": "reply truncated at max_tokens"}
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if content[:4].lower() == "json" else content
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None, {"kind": "invalid_output", "error": "reply was not valid JSON"}
    if not isinstance(parsed, dict):
        return None, {"kind": "invalid_output", "error": "reply was not an object"}
    return parsed, None


def chat_json(*, agent: str, base_url: str, model: str, key_env: str,
              user_agent: str, system: str, user: str, schema: Dict[str, Any],
              max_tokens: int, timeout: float, reasoning_effort: str = "",
              json_schema_mode: Optional[bool] = None,
              schema_name: str = "reply") -> Dict[str, Any]:
    """One structured-JSON call to an OpenAI-compatible endpoint.

    Returns either
        {"ok": True,  "parsed": {...}, "tokens": {"in": n, "out": n}, ...}
    or
        {"ok": False, "error": "...", "kind": "...", ...}

    `kind` is the contract that matters: `quota`/`paced` mean the provider or
    the governor, never the market, and the caller must record them as such.
    """
    key = os.environ.get(key_env, "")
    if not key:
        return {"ok": False, "kind": "config_error", "error": f"{key_env} not set"}

    use_schema = JSON_SCHEMA_MODE if json_schema_mode is None else json_schema_mode
    if use_schema:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": schema_name,
                                           "schema": strict_schema(schema),
                                           "strict": True}}
        # The schema is IN the response_format now, so repeating it in the
        # prompt is ~230 tokens a call buying nothing. Under json_object it is
        # the only statement of the shape and must stay.
        prompt_system = system + ("\n\nReply with a single JSON object and "
                                  "nothing else.")
    else:
        response_format = {"type": "json_object"}
        prompt_system = system + ("\n\nReply with a single JSON object and "
                                  "nothing else. It must satisfy this JSON "
                                  f"Schema:\n{json.dumps(schema)}")

    est = estimate_tokens(prompt_system, user, max_tokens)
    ok, why = reserve(agent, est)
    if not ok:
        why = dict(why)
        why["ok"] = False
        why["error"] = (f"{why.get('reason')} "
                        f"(retry in {why.get('retry_after_sec')}s)")
        return why

    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": int(max_tokens),
        "response_format": response_format,
        "messages": [{"role": "system", "content": prompt_system},
                     {"role": "user", "content": user}],
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    url = base_url.rstrip("/") + "/chat/completions"
    attempted_no_thinking = reasoning_effort == "none"

    for attempt in (0, 1):
        try:
            doc = _post(url, body, key, user_agent, timeout)
        except Exception as exc:
            import urllib.error

            if isinstance(exc, urllib.error.HTTPError):
                try:
                    detail = exc.read().decode()
                except Exception:
                    detail = ""
                info = classify_http(exc.code, detail)
                if info["kind"] == "rate_limit":
                    # Tell the other book too, so it does not spend the whole
                    # wait rediscovering the same limit once per refresh.
                    scope = info.get("quota_scope") or "unknown"
                    note_cooldown(info.get("retry_after_sec")
                                  or (900.0 if scope == "tpd" else 60.0),
                                  scope, info.get("detail", ""))
                if (info.get("truncated") and attempt == 0
                        and not attempted_no_thinking):
                    # THE self-heal. A reasoning model that spent its budget
                    # thinking gets one more go with thinking off, which is the
                    # actual fix rather than a swallowed 400.
                    body["reasoning_effort"] = "none"
                    attempted_no_thinking = True
                    continue
                info.update({"ok": False,
                             "error": f"HTTP {exc.code}: {info.get('detail','')}"})
                settle(agent, est, 0)      # refund: nothing was generated
                return info
            settle(agent, est, 0)
            return {"ok": False, "kind": "provider_unreachable",
                    "error": f"{type(exc).__name__}: {exc}"}

        parsed, err = _extract(doc)
        u = doc.get("usage") or {}
        used = int(u.get("total_tokens") or 0)
        if err is not None:
            if (err.get("truncated") and attempt == 0
                    and not attempted_no_thinking):
                settle(agent, est, used)
                ok2, why2 = reserve(agent, est)
                if not ok2:
                    why2 = dict(why2)
                    why2["ok"] = False
                    why2["error"] = str(why2.get("reason"))
                    return why2
                body["reasoning_effort"] = "none"
                attempted_no_thinking = True
                continue
            settle(agent, est, used)
            err.update({"ok": False, "error": err.get("error", "bad reply")})
            return err

        settle(agent, est, used)
        return {"ok": True, "parsed": parsed, "model": model,
                "tokens": {"in": int(u.get("prompt_tokens") or 0),
                           "out": int(u.get("completion_tokens") or 0)},
                "retried_without_thinking": attempted_no_thinking
                and reasoning_effort != "none"}

    return {"ok": False, "kind": "truncated",
            "error": "reply truncated even with thinking off"}


# Failures where asking a DIFFERENT backend is worth the second call: the
# provider refused, deferred, or never answered. `invalid_output` is excluded
# on purpose — a model that answered with the wrong shape is a model problem,
# and a smaller local model asked the same question will not do better while
# costing ~100s of CPU.
FALLBACK_WORTHY_KINDS = {"budget_deferred", "rate_limit", "provider_unreachable",
                         "too_large", "truncated", "config_error", "provider_error"}


def main() -> int:
    """`python3 agent_transport.py` — what the governor thinks is left."""
    print(json.dumps(budget_status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
