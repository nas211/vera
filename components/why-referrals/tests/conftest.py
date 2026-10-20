import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def refs(tmp_path):
    # NOTE: the package is imported ONCE for the whole session and each test
    # gets its own database file. Reloading why_referrals per test would give
    # every test module a different ReferralError class, and `pytest.raises`
    # would stop matching the exception the engine actually raises.
    from why_referrals.engine import Referrals
    return Referrals(path=tmp_path / "r.db")


@pytest.fixture
def quests(refs):
    from why_referrals.quests import Quests
    return Quests(refs)
