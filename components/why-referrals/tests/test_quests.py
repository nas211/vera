"""The quest layer. The rules under test are the ones that decide whether this
is a rewards programme or a faucet: proof is per-post, a task pays once, an
outage is not a rejection, and what we cannot check pays less."""
import json

import pytest

from why_referrals import config, quests as Q
from why_referrals.verifiers import REJECTED, UNKNOWN, VERIFIED, Result


@pytest.fixture(autouse=True)
def no_cooldown(monkeypatch):
    monkeypatch.setattr(config, "QUEST_CLAIM_COOLDOWN_SEC", 0)


@pytest.fixture
def verified_user(quests, monkeypatch):
    """A person with a proven X handle."""
    monkeypatch.setattr("why_referrals.verifiers.x_oembed.verify_handle_nonce",
                        lambda url, handle, nonce: Result(VERIFIED, "x_oembed", {"author": handle}))
    quests.link_handle("predict", 1, social="x", handle="@bob")
    quests.verify_handle("predict", 1, social="x", proof_url="https://x.com/bob/status/1")
    return ("predict", 1)


def a_quest(quests, **kw):
    kw.setdefault("code", "REPLY_1")
    kw.setdefault("kind", "REPLY")
    kw.setdefault("title", "Reply to our launch post")
    kw.setdefault("y_reward", 100)
    kw.setdefault("target_url", "https://x.com/whyprotocolx/status/1234567890")
    kw.setdefault("marker", "#WHYTRADE")
    quests.add_quest(**kw)
    return kw["code"]


def result(monkeypatch, status, verifier="x_oembed", **detail):
    monkeypatch.setattr("why_referrals.quests.x_oembed.verify_post",
                        lambda *a, **k: Result(status, verifier, detail))


# ---------------------------------------------------------------- handles
def test_handle_must_be_proven_before_claiming(quests):
    code = a_quest(quests)
    quests.link_handle("predict", 1, social="x", handle="bob")
    with pytest.raises(Q.QuestError) as e:
        quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/9")
    assert "verify your x handle" in str(e.value)


def test_handle_verification_needs_our_nonce(quests, monkeypatch):
    out = quests.link_handle("predict", 1, social="x", handle="bob")
    assert len(out["nonce"]) == config.HANDLE_NONCE_LEN
    monkeypatch.setattr("why_referrals.verifiers.x_oembed.verify_handle_nonce",
                        lambda url, handle, nonce: Result(REJECTED, "x_oembed", {"reason": "no code"}))
    assert quests.verify_handle("predict", 1, social="x", proof_url="u")["ok"] is False


def test_a_verified_handle_cannot_be_stolen(quests, verified_user, monkeypatch):
    with pytest.raises(Q.QuestError):
        quests.link_handle("predict", 2, social="x", handle="BOB")


# ---------------------------------------------------------------- claiming
def test_verified_claim_pays_the_full_reward(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, VERIFIED, author="bob")
    out = quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/55")
    assert out["status"] == "PAID" and out["y"] == 100
    assert quests.r.balance("predict", 1) == 100


def test_someone_elses_post_is_rejected_and_pays_nothing(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, REJECTED, reason="written by someone else")
    out = quests.claim(code, "predict", 1, proof_url="https://x.com/other/status/55")
    assert out["status"] == "REJECTED" and quests.r.balance("predict", 1) == 0


def test_an_outage_queues_the_claim_instead_of_rejecting_it(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, UNKNOWN, error="http 503")
    out = quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/55")
    assert out["status"] == "PENDING" and out["y"] == 0
    # ...and the sweep pays it once X answers again.
    result(monkeypatch, VERIFIED, author="bob")
    swept = quests.sweep()
    assert swept["paid"] == 1
    assert quests.r.balance("predict", 1) == 100


def test_the_same_post_cannot_be_submitted_twice(quests, verified_user, monkeypatch):
    a_quest(quests)
    a_quest(quests, code="REPLY_2", target_url="https://x.com/whyprotocolx/status/999")
    result(monkeypatch, VERIFIED)
    quests.claim("REPLY_1", "predict", 1, proof_url="https://x.com/bob/status/55")
    with pytest.raises(Q.QuestError) as e:
        quests.claim("REPLY_2", "predict", 1, proof_url="https://x.com/bob/status/55")
    assert "already been submitted" in str(e.value)


def test_url_forms_of_one_post_are_the_same_post(quests, verified_user, monkeypatch):
    a_quest(quests)
    a_quest(quests, code="REPLY_2")
    result(monkeypatch, VERIFIED)
    quests.claim("REPLY_1", "predict", 1, proof_url="https://twitter.com/bob/status/55?s=20")
    with pytest.raises(Q.QuestError):
        quests.claim("REPLY_2", "predict", 1, proof_url="https://x.com/bob/status/55")


def test_one_quest_pays_one_person_once(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, VERIFIED)
    quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/1")
    with pytest.raises(Q.QuestError):
        quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/2")


def test_max_claims_closes_the_quest(quests, monkeypatch):
    code = a_quest(quests, max_claims=1)
    monkeypatch.setattr("why_referrals.verifiers.x_oembed.verify_handle_nonce",
                        lambda url, handle, nonce: Result(VERIFIED, "x_oembed", {}))
    result(monkeypatch, VERIFIED)
    for uid, h in ((1, "bob"), (2, "carol")):
        quests.link_handle("predict", uid, social="x", handle=h)
        quests.verify_handle("predict", uid, social="x", proof_url=f"https://x.com/{h}/status/1")
    quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/7")
    with pytest.raises(Q.QuestError) as e:
        quests.claim(code, "predict", 2, proof_url="https://x.com/carol/status/8")
    assert "fully claimed" in str(e.value)


def test_daily_claim_cap(quests, verified_user, monkeypatch):
    monkeypatch.setattr(config, "QUEST_MAX_CLAIMS_PER_DAY", 2)
    result(monkeypatch, VERIFIED)
    for i in range(2):
        a_quest(quests, code=f"Q{i}")
        quests.claim(f"Q{i}", "predict", 1, proof_url=f"https://x.com/bob/status/{i}")
    a_quest(quests, code="Q9")
    with pytest.raises(Q.QuestError) as e:
        quests.claim("Q9", "predict", 1, proof_url="https://x.com/bob/status/9")
    assert "daily task limit" in str(e.value)


# ---------------------------------------------------------------- attested
def test_a_like_pays_a_fraction_because_we_cannot_see_it(quests, verified_user):
    quests.add_quest(code="LIKE_1", kind="LIKE", title="Like the launch post", y_reward=100,
                     target_ref="1234567890")
    out = quests.claim("LIKE_1", "predict", 1)
    assert out["status"] == "PAID" and out["attested"] is True
    assert out["y"] == 50                       # ATTESTED_REWARD_PCT
    assert quests.r.balance("predict", 1) == 50


def test_a_paid_verifier_still_kills_a_false_like(quests, verified_user, monkeypatch):
    quests.add_quest(code="LIKE_1", kind="LIKE", title="Like it", y_reward=100, target_ref="123")
    monkeypatch.setattr("why_referrals.quests.x_api.verify_like",
                        lambda pid, handle: Result(REJECTED, "x_api", {"n_seen": 400}))
    out = quests.claim("LIKE_1", "predict", 1)
    assert out["status"] == "REJECTED" and quests.r.balance("predict", 1) == 0


def test_spot_check_claws_back_a_like_that_was_never_there(quests, verified_user, monkeypatch):
    monkeypatch.setattr(config, "SPOT_CHECK_PCT", 100)
    quests.add_quest(code="LIKE_1", kind="LIKE", title="Like it", y_reward=100, target_ref="123")
    out = quests.claim("LIKE_1", "predict", 1)
    assert out["y"] == 50
    monkeypatch.setattr("why_referrals.quests.x_api.available", lambda: True)
    monkeypatch.setattr("why_referrals.quests.x_api.verify_like",
                        lambda pid, handle: Result(REJECTED, "x_api", {"n_seen": 400}))
    swept = quests.sweep()
    assert swept["clawed"] == 1
    assert quests.r.balance("predict", 1) == 0


def test_spot_checks_do_not_run_without_a_verifier_that_can_see(quests, verified_user, monkeypatch):
    monkeypatch.setattr(config, "SPOT_CHECK_PCT", 100)
    quests.add_quest(code="LIKE_1", kind="LIKE", title="Like it", y_reward=100, target_ref="123")
    quests.claim("LIKE_1", "predict", 1)
    assert quests.sweep()["spot_checked"] == 0
    assert quests.r.balance("predict", 1) == 50


# ---------------------------------------------------------------- catalogue / admin
def test_catalogue_shows_the_reward_at_this_persons_tier(quests, verified_user):
    a_quest(quests)
    quests.r.grant("predict", 1, 3000, "seed")          # FORGE, x1.25
    row = quests.catalogue("predict", 1)[0]
    assert row["y_base"] == 100 and row["y_reward"] == 125


def test_catalogue_marks_what_i_already_did(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, VERIFIED)
    quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/1")
    assert quests.catalogue("predict", 1)[0]["my_claim"]["status"] == "PAID"


def test_manual_quests_wait_for_a_human_then_pay(quests, verified_user):
    quests.add_quest(code="ART", kind="POST", title="Make us something", y_reward=500,
                     verify_mode="MANUAL")
    out = quests.claim("ART", "predict", 1, proof_url="https://x.com/bob/status/77")
    assert out["status"] == "PENDING"
    assert quests.sweep()["still_pending"] == 1        # never auto-decided
    claim_id = quests.pending()[0]["id"]
    paid = quests.admin_decide(claim_id, True, "admin", "nice work")
    assert paid["status"] == "PAID" and quests.r.balance("predict", 1) == 500


def test_quest_pay_is_idempotent_under_a_replayed_sweep(quests, verified_user, monkeypatch):
    code = a_quest(quests)
    result(monkeypatch, UNKNOWN)
    quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/1")
    result(monkeypatch, VERIFIED)
    quests.sweep()
    quests.sweep()
    assert quests.r.balance("predict", 1) == 100


def test_suspended_person_cannot_claim(quests, verified_user):
    code = a_quest(quests)
    quests.r.suspend("predict", 1, True, "admin", "abuse")
    with pytest.raises(Q.QuestError):
        quests.claim(code, "predict", 1, proof_url="https://x.com/bob/status/1")


def test_quest_earnings_count_toward_referral_tier(quests, verified_user, monkeypatch):
    result(monkeypatch, VERIFIED)
    for i in range(6):
        a_quest(quests, code=f"Q{i}", y_reward=100)
        quests.claim(f"Q{i}", "predict", 1, proof_url=f"https://x.com/bob/status/{i}")
    assert quests.r.summary("predict", 1)["tier"]["name"] == "EMBER"


# ---------------------------------------------------------------- X only
def test_only_the_networks_we_run_accept_handles(quests):
    """WHY runs an X account and no Instagram account (owner, 2026-08-26).
    A task nobody can complete is worse than no task."""
    assert config.SOCIAL_PLATFORMS == ("x",)
    with pytest.raises(Q.QuestError) as e:
        quests.link_handle("predict", 1, social="instagram", handle="bob")
    assert "we only run tasks on X" in str(e.value)


def test_a_task_for_a_switched_off_network_cannot_be_created(quests):
    with pytest.raises(Q.QuestError) as e:
        quests.add_quest(code="IG1", kind="COMMENT", title="Comment on our post",
                         y_reward=100, platform="instagram")
    assert "Instagram is switched off" in str(e.value)


def test_turning_instagram_back_on_is_one_setting(quests, monkeypatch):
    monkeypatch.setattr(config, "SOCIAL_PLATFORMS", ("x", "instagram"))
    out = quests.link_handle("predict", 1, social="instagram", handle="bob")
    assert "Comment this code" in out["instructions"]
    quests.add_quest(code="IG1", kind="COMMENT", title="Comment on our post",
                     y_reward=100, platform="instagram", target_ref="17900")
