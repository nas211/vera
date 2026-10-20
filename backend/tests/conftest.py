import shutil
import sys
import time
import uuid
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from vela.settings import Settings
from vela.db import connect


@pytest.fixture
def setup_db():
    folder = ROOT / ".runtime" / ("test-" + uuid.uuid4().hex)
    settings = Settings(db_path=folder / "test.sqlite3")
    db = connect(settings.db_path)
    user = "0x" + "11" * 20
    db.execute("""INSERT INTO users(address,session,strategy,status,initial,balance,doge_in,started,start_tx)
        VALUES (?, 'session-one',1,'active','100','100','500000000000000000000',?,'0xstart')""", (user, time.time()))
    for symbol, value in [("DOGE/USD", ".2"), ("BTC/USD", "60000"), ("ETH/USD", "3000"), ("SOL/USD", "150"), ("XAU/USD", "3000")]:
        db.execute("INSERT INTO prices VALUES (?,?,?,'fixture',1,'')", (symbol, value, time.time()))
    yield db, settings, user
    db.close()
    shutil.rmtree(folder)
