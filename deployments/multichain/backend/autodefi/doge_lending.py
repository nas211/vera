"""Native TEST DOGE market observations. No signing, sends or demo-ledger mutations."""
import asyncio
import json
import re
import time
from web3 import Web3
from web3.exceptions import TransactionNotFound
from fastapi import Request
from .config import ROOT, CHAIN_ID
from .adapters import split_owner

CONFIG = ROOT / 'config/doge-lending.json'
FIELDS = ('collateral', 'principal', 'interest', 'interestRemainder', 'updatedAt', 'available', 'revision')

def configuration(path=CONFIG):
    d = json.loads(path.read_text())
    if d.get('schemaVersion') != 1 or d.get('chainId') != CHAIN_ID or type(d.get('enabled')) is not bool:
        raise ValueError('Invalid test DOGE lending configuration')
    if type(d.get('confirmations')) is not int or not 3 <= d['confirmations'] <= 64:
        raise ValueError('At least three confirmations required')
    if d['enabled']:
        for k in ('market', 'manager'):
            if not Web3.is_address(d.get(k)) or int(d[k], 16) == 0: raise ValueError('Verified market roles required')
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}', d.get('runtimeCodeHash') or ''):
            raise ValueError('Verified market runtime required')
        if type(d.get('deploymentBlock')) is not int or d['deploymentBlock'] < 1:
            raise ValueError('Confirmed deployment block required')
    # No UI/config switch can turn paper dollars into redeemable USDC or DOGE.
    if d.get('conversion', {}).get('enabled') is not False:
        raise ValueError('Conversion requires a separately implemented verified settlement adapter')
    return d

def public_configuration(d):
    return dict(**d, asset='DOGE', decimals=18, lending='NATIVE_TESTNET', maxLtvBps=7000,
                riskLtvBps=8000, aprBps=500, automaticLiquidation=False,
                execution='PAPER', paperProfitsRedeemable=False)

class DogeLendingReader:
    def __init__(self, w3, settings, clock=time.time):
        self.w3, self.settings, self.clock = w3, settings, clock

    def snapshot(self, account, tx=None):
        d, w3 = self.settings, self.w3
        if not d['enabled']: raise ValueError('Test DOGE lending awaits deployment and pool funding')
        if tx is not None and not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx): raise ValueError('Invalid transaction hash')
        if w3.eth.chain_id != CHAIN_ID: raise ValueError('Wrong DogeOS chain')
        account = Web3.to_checksum_address(account)
        head = w3.eth.get_block('latest')
        if not 0 <= self.clock() - head.timestamp <= 90: raise ValueError('Stale chain head')
        height = head.number - d['confirmations'] + 1
        if height < d['deploymentBlock']: raise ValueError('Market deployment is not confirmed')
        block = w3.eth.get_block(height)
        target = Web3.to_checksum_address(d['market'])
        if Web3.keccak(w3.eth.get_code(target, block_identifier=height)).to_0x_hex().lower() != d['runtimeCodeHash'].lower():
            raise ValueError('Market runtime does not match reviewed deployment')
        market = w3.eth.contract(target, abi=json.loads((ROOT/'config/TestDogeLending.abi.json').read_text()))
        def read(name, *args): return getattr(market.functions, name)(*args).call(block_identifier=height)
        if read('deployedChainId') != CHAIN_ID or read('owner').lower() != d['manager'].lower():
            raise ValueError('Market chain or manager differs')
        if (read('MAX_LTV_BPS'), read('RISK_LTV_BPS'), read('APR_BPS')) != (7000, 8000, 500):
            raise ValueError('Market terms differ')
        p = dict(zip(FIELDS, map(str, read('position', account))))
        liquidity, collateral, available = read('liquidity'), read('totalCollateral'), read('totalAvailable')
        if w3.eth.get_balance(target, block_identifier=height) < liquidity + collateral + available:
            raise ValueError('Market liabilities are not backed')
        receipt = None
        if tx:
            try:
                t = w3.eth.get_transaction(tx)
                if t['from'].lower() != account.lower() or (t['to'] or '').lower() != target.lower():
                    raise ValueError('Transaction belongs to a different wallet or market')
                receipt = dict(hash=tx, status='PENDING', sender=t['from'].lower(), to=target.lower(),
                               data=t['input'].to_0x_hex(), valueWei=str(t['value']))
                r = w3.eth.get_transaction_receipt(tx)
                if r.blockNumber <= height:
                    if w3.eth.get_block(r.blockNumber).hash != r.blockHash: raise ValueError('Transaction reorganized')
                    receipt.update(status='CONFIRMED' if r.status == 1 else 'REVERTED', block=r.blockNumber,
                                   blockHash=r.blockHash.to_0x_hex())
            except TransactionNotFound:
                receipt = receipt or dict(hash=tx, status='PENDING')
        capacity, paused = read('capacity', account), read('borrowingPaused')
        wallet = w3.eth.get_balance(account, block_identifier=height)
        if w3.eth.get_block(height).hash != block.hash: raise ValueError('Market observation reorganized')
        return dict(source='chain', chainId=CHAIN_ID, owner=account.lower(), market=target.lower(),
                    codeHash=d['runtimeCodeHash'], bytecodeVerified=True, block=height,
                    blockHash=block.hash.to_0x_hex(), observedAt=int(self.clock()), blockTimestamp=block.timestamp,
                    confirmations=d['confirmations'], position=p, debtWei=str(int(p['principal'])+int(p['interest'])),
                    availableToBorrowWei=str(capacity), liquidityWei=str(liquidity), walletBalanceWei=str(wallet),
                    paused=paused, transaction=receipt)

def attach(app, config, chain, owner, settings_path=CONFIG):
    @app.get('/api/doge-lending/config')
    async def doge_config():
        return public_configuration(configuration(settings_path))

    @app.get('/api/doge-lending/account')
    async def doge_account(req: Request, tx: str | None = None):
        network, wallet = split_owner(owner(req))
        if network != 'dogeos': raise ValueError('DogeOS wallet required')
        if not config.network_enabled: raise ValueError('DogeOS reads disabled')
        d = configuration(settings_path)
        return await asyncio.to_thread(DogeLendingReader(chain.w3, d).snapshot, wallet, tx)


def create_readonly_app(settings_path=CONFIG):
    """Public chain data only; independent process so trading need not restart.

    Addresses/positions are public on-chain. This API has no ledger, login,
    trading key, signer or transaction-submission endpoint.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    app=FastAPI(title='Vela test DOGE market reads',docs_url=None,redoc_url=None,openapi_url=None)
    w3=Web3(Web3.HTTPProvider('https://rpc.testnet.dogeos.com/',request_kwargs={'timeout':8}))
    cache={};pending={};semaphore=asyncio.Semaphore(2)
    @app.exception_handler(ValueError)
    async def invalid(req,exc):return JSONResponse({'error':str(exc)},status_code=400)
    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request);response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff';return response
    @app.get('/api/doge-lending/config')
    async def market_config():return public_configuration(configuration(settings_path))
    @app.get('/api/doge-lending/account')
    async def market_account(req:Request,tx:str|None=None):
        wallet=req.headers.get('x-wallet-address','')
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',wallet) or int(wallet,16)==0:raise ValueError('Valid public wallet address required')
        if tx is not None and not re.fullmatch(r'0x[0-9a-fA-F]{64}',tx):raise ValueError('Invalid transaction hash')
        d=configuration(settings_path)
        if not d['enabled']:raise ValueError('Test DOGE lending awaits deployment and pool funding')
        key=(json.dumps(d,sort_keys=True),wallet.lower(),tx)
        if key in cache and time.monotonic()-cache[key][0]<5:return cache[key][1]
        if key not in pending:
            if len(pending)>=8:return JSONResponse({'error':'Market reader busy; retry shortly'},status_code=503)
            async def fetch():
                async with semaphore:
                    result=await asyncio.to_thread(DogeLendingReader(w3,d).snapshot,wallet,tx)
                if len(cache)>=256:cache.clear()
                cache[key]=(time.monotonic(),result);return result
            task=asyncio.create_task(fetch());pending[key]=task
            def finished(task):
                pending.pop(key,None)
                if not task.cancelled():task.exception()
            task.add_done_callback(finished)
        try:return await asyncio.shield(pending[key])
        except ValueError:raise
        except Exception:return JSONResponse({'error':'DogeOS market read unavailable; retry shortly'},status_code=503)
    return app

readonly_app=create_readonly_app()
