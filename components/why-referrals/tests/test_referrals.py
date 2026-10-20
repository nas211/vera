"""The referral engine. Every test here is a rule someone could otherwise
argue about later: who gets paid, how much, once, and when not at all."""
import pytest

from why_referrals import ids, ledger, rules
from why_referrals.engine import ReferralError


def code_of(refs, plat="telegram", eid=1, name="alice"):
    return refs.code_for(plat, eid, display=name)


# ---------------------------------------------------------------- codes
def test_code_is_stable_per_person(refs):
    a = refs.code_for("telegram", 1)
    assert refs.code_for("telegram", 1) == a
    assert refs.code_for("telegram", 2) != a


def test_code_avoids_confusable_characters(refs):
    for _ in range(50):
        c = ids.random_code()
        assert not set(c[4:]) & set("01ILOU")
        assert ids.looks_like_code(c)


@pytest.mark.parametrize("typed", [
    "WHY-ABC234", "why-abc234", " WHY-ABC234 ", "ABC234", "why_abc234",
    "r_WHYABC234", "https://whytradingbot.com/predict?r=WHY-ABC234",
])
def test_every_form_of_the_code_resolves(refs, typed, monkeypatch):
    monkeypatch.setattr("why_referrals.ids.random_code", lambda: "WHY-ABC234")
    refs.code_for("telegram", 1)
    assert refs.resolve_code(typed) is not None


# ---------------------------------------------------------------- binding
def test_bind_pays_both_sides(refs):
    c = code_of(refs)
    out = refs.bind(c, "predict", 99, ip="1.1.1.1", display="bob")
    assert out["ok"]
    assert refs.balance("telegram", 1) == 50      # referrer
    assert refs.balance("predict", 99) == 25      # referee welcome


def test_cannot_refer_yourself(refs):
    c = code_of(refs)
    out = refs.bind(c, "telegram", 1)
    assert not out["ok"] and "yourself" in out["reason"]


def test_second_code_is_refused(refs):
    a, b = refs.code_for("telegram", 1), refs.code_for("telegram", 2)
    assert refs.bind(a, "predict", 99)["ok"]
    out = refs.bind(b, "predict", 99)
    assert not out["ok"] and "already referred" in out["reason"]


def test_circular_referral_is_refused(refs):
    a = refs.code_for("telegram", 1)
    refs.bind(a, "telegram", 2)
    b = refs.code_for("telegram", 2)
    out = refs.bind(b, "telegram", 1)
    assert not out["ok"] and out["reason"] == "circular referral"


def test_old_account_cannot_be_backfilled_onto_a_code(refs):
    c = code_of(refs)
    out = refs.bind(c, "predict", 99, account_age_sec=86400)
    assert not out["ok"] and "new accounts" in out["reason"]


def test_unknown_code_fails_softly(refs):
    out = refs.bind("WHY-ZZZZZZ", "predict", 99)
    assert out["ok"] is False and out["reason"] == "unknown code"


# ---------------------------------------------------------------- awards
def test_qualification_pays_once(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 99)
    first = refs.note_activity("predict", 99, n_actions=3)
    assert first["y_paid"] == 100
    again = refs.note_activity("predict", 99, n_actions=9)
    assert again.get("already") == "QUALIFIED"
    assert refs.balance("telegram", 1) == 150


def test_below_threshold_does_not_qualify(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 99)
    assert refs.note_activity("predict", 99, n_actions=2)["ok"] is False
    assert refs.balance("telegram", 1) == 50


def test_commission_recurs_but_never_twice_for_one_payment(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 99)
    r1 = refs.note_payment("predict", 99, usd=99.0, tier="premium", payment_id=1)
    r2 = refs.note_payment("predict", 99, usd=99.0, tier="premium", payment_id=1)   # replay
    r3 = refs.note_payment("predict", 99, usd=99.0, tier="premium", payment_id=2)   # renewal
    assert r1["y_paid"] == 198 and r2["y_paid"] == 0 and r3["y_paid"] == 198


def test_payment_qualifies_a_pending_referral(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 99)
    refs.note_payment("predict", 99, usd=49.0, payment_id=7)
    assert refs.summary("telegram", 1)["referrals"]["qualified"] == 1


def test_milestone_pays_on_the_fifth(refs):
    c = code_of(refs)
    for i in range(5):
        refs.bind(c, "predict", 100 + i)
        refs.note_activity("predict", 100 + i, n_actions=3)
    s = refs.summary("telegram", 1)
    # 5 signups + 5 qualifieds + one milestone, with the tier multiplier that
    # kicks in partway through.
    assert s["referrals"]["qualified"] == 5
    assert s["y_lifetime"] >= 5 * 50 + 5 * 100 + 250


# ---------------------------------------------------------------- tiers
def test_tier_multiplies_earnings_and_spending_never_demotes(refs):
    refs.grant("telegram", 1, 3000, "seed")
    assert rules.tier_for(3000)[0] == "FORGE"
    c = code_of(refs)
    refs.bind(c, "predict", 99)
    assert refs.balance("telegram", 1) == 3000 + int(50 * 1.25)
    refs.convert_to_credits("telegram", 1, 3000)
    assert refs.summary("telegram", 1)["tier"]["name"] == "FORGE"


# ---------------------------------------------------------------- anti-abuse
def test_shared_ip_holds_the_award_instead_of_refusing_it(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 91, ip="9.9.9.9")
    out = refs.bind(c, "predict", 92, ip="9.9.9.9")
    assert out["ok"] and out["held"] is True
    assert refs.balance("telegram", 1) == 50          # only the first paid
    assert refs.balance("predict", 92) == 25          # referee still welcomed
    h = refs.holds()
    assert h and h[0]["delta"] == 50
    refs.release(h[0]["id"], "admin", "known couple")
    assert refs.balance("telegram", 1) == 100


def test_refused_hold_never_pays(refs):
    c = code_of(refs)
    refs.bind(c, "predict", 91, ip="9.9.9.9")
    refs.bind(c, "predict", 92, ip="9.9.9.9")
    h = refs.holds()[0]
    refs.refuse(h["id"], "admin", "same person")
    assert refs.balance("telegram", 1) == 50
    assert refs.holds() == []


def test_suspended_referrer_code_stops_working(refs):
    c = code_of(refs)
    refs.suspend("telegram", 1, True, "admin", "fraud review")
    assert refs.bind(c, "predict", 99)["ok"] is False


# ---------------------------------------------------------------- spending
def test_convert_to_credits_debits_exactly_once(refs):
    refs.grant("telegram", 1, 100, "seed")
    out = refs.convert_to_credits("telegram", 1, 60)
    assert out["credits"] == 600 and out["balance"] == 40
    with pytest.raises(ReferralError):
        refs.convert_to_credits("telegram", 1, 60)


def test_minimum_conversion_is_enforced(refs):
    refs.grant("telegram", 1, 100, "seed")
    with pytest.raises(ReferralError):
        refs.convert_to_credits("telegram", 1, 10)


def test_sub_days_cost_and_bounds(refs):
    refs.grant("telegram", 1, 1000, "seed")
    out = refs.spend_for_sub_days("telegram", 1, 7)
    assert out["y_spent"] == 280 and out["balance"] == 720
    with pytest.raises(ReferralError):
        refs.spend_for_sub_days("telegram", 1, 3)      # under the minimum


def test_cannot_spend_what_you_do_not_have(refs):
    with pytest.raises(ReferralError):
        refs.spend_for_sub_days("telegram", 1, 7)


# ---------------------------------------------------------------- ledger
def test_ledger_is_append_only(refs):
    refs.grant("telegram", 1, 10, "seed")
    con = refs.store.con
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE y_ledger SET delta=999")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM y_ledger")


def test_balance_is_the_sum_of_the_ledger(refs):
    refs.grant("telegram", 1, 500, "seed")
    refs.convert_to_credits("telegram", 1, 200)
    con = refs.store.con
    total = con.execute("SELECT SUM(delta) s FROM y_ledger").fetchone()["s"]
    assert total == refs.balance("telegram", 1) == 300


# ---------------------------------------------------------------- linking
def test_link_merges_two_accounts_into_one_balance(refs):
    refs.grant("telegram", 1, 100, "tg side")
    refs.grant("predict", 55, 40, "web side")
    tok = refs.issue_link_token("predict", 55)["token"]
    out = refs.claim_link_token(tok, "telegram", 1)
    assert out["ok"]
    assert refs.balance("telegram", 1) == refs.balance("predict", 55) == 140


def test_link_token_is_single_use(refs):
    tok = refs.issue_link_token("predict", 55)["token"]
    refs.claim_link_token(tok, "telegram", 1)
    assert refs.claim_link_token(tok, "telegram", 2)["ok"] is False


def test_link_refuses_to_merge_a_referrer_with_their_referee(refs):
    c = refs.code_for("telegram", 1)
    refs.bind(c, "predict", 99)
    tok = refs.issue_link_token("predict", 99)["token"]
    out = refs.claim_link_token(tok, "telegram", 1)
    assert out["ok"] is False and "referred each other" in out["reason"]


def test_merged_person_keeps_earning_on_the_old_code(refs):
    c = refs.code_for("telegram", 1)
    tok = refs.issue_link_token("telegram", 1)["token"]
    refs.claim_link_token(tok, "predict", 55)
    refs.bind(c, "predict", 99)
    assert refs.balance("predict", 55) == 50


# ---------------------------------------------------------------- clean count
def test_clean_referrals_counts_the_screened_not_the_qualified(refs):
    """The waitlist's "verified friends" question. A person sitting on a
    waitlist has had no chance to use the product, so counting QUALIFIED there
    would mean the priority queue never fills."""
    c = code_of(refs)
    refs.bind(c, "predict", 91, ip="1.1.1.1")
    refs.bind(c, "predict", 92, ip="2.2.2.2")
    pid = refs.resolve_code(c)
    assert refs.clean_referrals(pid) == 2               # neither has "qualified"
    assert refs.summary("telegram", 1)["referrals"]["qualified"] == 0

    refs.bind(c, "predict", 93, ip="2.2.2.2")           # shared IP -> held
    assert refs.clean_referrals(pid) == 2               # ...and not counted
    refs.release(refs.holds()[0]["id"], "admin", "checked out")
    assert refs.clean_referrals(pid) == 3
