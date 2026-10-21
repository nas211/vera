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


def pytest_addoption(parser):
    parser.addoption('--in-process-asgi',action='store_true',
                     help='Use actual in-process ASGI requests instead of TestClient cross-thread transport')


def pytest_configure(config):
    if config.getoption('--in-process-asgi'):
        import fastapi.testclient
        from asgi_client import InProcessClient
        fastapi.testclient.TestClient=InProcessClient


@pytest.fixture
def api_factory(setup_db):
    """Original API tests get an isolated paper runtime, never production state/feed."""
    from types import SimpleNamespace
    from vela.api import create_app
    from vela.lighter.config import PaperConfig
    folder=ROOT/".runtime/lighter-showcase"/("test-api-"+uuid.uuid4().hex)
    def factory(settings=None):
        return create_app(settings or setup_db[1],paper_settings=PaperConfig(db_path=folder/'paper.sqlite3'),
                          model=SimpleNamespace(ready=False),start_background=False,
                          portal_origin='http://testserver')
    yield factory
    shutil.rmtree(folder,ignore_errors=True)


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
