"""Loopback-only products test harness; synthetic wallet/model/market fixtures."""
import asyncio,sys,time,uuid
from pathlib import Path
from contextlib import asynccontextmanager
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'backend'),str(root/'tests')]
from autodefi.api import create_app
from autodefi.config import Config
from conftest import fixture_feed
from test_api_ai import FixtureModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
feed=fixture_feed();config=Config(origin='http://127.0.0.1:18917',db_path=root/'.runtime/lighter-showcase'/('products-fixture-'+uuid.uuid4().hex+'.sqlite3'),multichain=True,demo_staking=True)
app=create_app(config,feed=feed,model=FixtureModel())
previous=app.router.lifespan_context
@asynccontextmanager
async def lifespan(app):
 async def freshness():
  while True:
   for m in feed.markets.values():m.book_at=m.stats_at=time.time()
   feed.error=''
   await asyncio.sleep(.1)
 task=asyncio.create_task(freshness())
 try:
  async with previous(app):yield
 finally:
  task.cancel();await asyncio.gather(task,return_exceptions=True)
app.router.lifespan_context=lifespan
app.mount('/product-assets/paper-earn-20261103',StaticFiles(directory=root/'site/products-built'))
@app.get('/paper')
async def paper():return FileResponse(root/'site/products-built/paper.html')
@app.get('/earn')
async def earn():return FileResponse(root/'site/products-built/earn.html')
