"""Credential-free Freqtrade launcher; OS network isolation is mandatory in service units."""
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.parse

from market_relay import valid_path, SOCKET

BASE = Path('/opt/why-github-shadow')
NAMES = ('FAdxSmaStrategy', 'BbandRsi', 'Strategy001', 'FSupertrendStrategy',
         'VolatilitySystem', 'NostalgiaForInfinityX8')

class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)

def public_target(url, method, headers, body):
    u = urllib.parse.urlsplit(url)
    target = u.path + ('?' + u.query if u.query else '')
    if (method != 'GET' or body or u.scheme != 'https' or u.netloc != 'api.bybit.com'
            or not valid_path(target) or any('api-key' in k.lower() or 'sign' in k.lower()
                                           for k in (headers or {}))):
        raise ValueError('SHADOW: non-public exchange request denied: ' + str({
            'host': u.netloc, 'path': u.path, 'method': method,
            'query_keys': sorted(urllib.parse.parse_qs(u.query)),
            'header_keys': sorted(headers or {}), 'body_present': bool(body),
            'path_valid': valid_path(target)}))
    return target

def relay_fetch(exchange, url, method='GET', headers=None, body=None):
    from ccxt.base.errors import ExchangeNotAvailable, NetworkError
    target = public_target(url, method, headers, body)
    connection = UnixHTTP('public-market-relay', timeout=35)
    try:
        connection.request('GET', target)
        response = connection.getresponse()
        raw = response.read().decode()
        response_headers = dict(response.getheaders())
        if response.status != 200:
            raise ExchangeNotAvailable('Public relay HTTP ' + str(response.status))
        parsed = json.loads(raw)
        exchange.last_http_response = raw
        exchange.last_json_response = parsed
        exchange.last_response_headers = response_headers
        exchange.handle_errors(response.status, response.reason, url, method,
                               response_headers, raw, parsed, headers, body)
        return parsed
    except (OSError, http.client.HTTPException) as error:
        raise NetworkError('Public relay unavailable: ' + type(error).__name__) from error
    finally:
        connection.close()

def validate_config(config, name):
    if config.get('dry_run') is not True or config.get('trading_mode') != 'futures':
        raise ValueError('SHADOW ONLY: dry_run futures required')
    ex = config.get('exchange', {})
    if ex.get('name') != 'bybit' or any(ex.get(k) for k in ('key','secret','password','uid')):
        raise ValueError('SHADOW: credentials and other exchanges forbidden')
    if ex.get('enable_ws', True) or config.get('api_server', {}).get('enabled'):
        raise ValueError('SHADOW: websocket/API server disabled')
    if config.get('telegram', {}).get('enabled') or config.get('webhook', {}).get('enabled'):
        raise ValueError('SHADOW: external messaging disabled')
    if config.get('strategy') != name or not 0 < config.get('max_open_trades', 0) <= 8:
        raise ValueError('SHADOW: invalid strategy/capacity')
    if config.get('stake_amount') != 200 or config.get('max_entry_position_adjustment') != 2:
        raise ValueError('SHADOW: fixed experiment sizing required')
    if config.get('add_config_files'):
        raise ValueError('SHADOW: nested configs forbidden')

def write_heartbeat(name, **fields):
    path = BASE / 'state' / name / 'runtime.json'
    payload = dict(ts=time.time(), name=name, mode='DRY_RUN', credentials=False,
                   exchange_write_routes=False, **fields)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, allow_nan=False))
    os.replace(temporary, path)

def main():
    name = sys.argv[1]
    if name not in NAMES: raise ValueError('Unknown strategy')
    if any(k.startswith('FREQTRADE__') for k in os.environ):
        raise ValueError('Environment configuration overrides forbidden')
    manifest = json.loads((BASE/'review'/'runtime-manifest.json').read_text())
    for filename, expected in manifest['runtime_sha256'].items():
        if hashlib.sha256((BASE/filename).read_bytes()).hexdigest() != expected:
            raise ValueError('Reviewed runtime file changed: ' + filename)
    config = json.loads((BASE/'configs'/f'{name}.json').read_text())
    validate_config(config, name)
    write_heartbeat(name, state='starting')
    from ccxt.base.exchange import Exchange
    from ccxt.async_support.base.exchange import Exchange as AsyncExchange
    Exchange.fetch = relay_fetch
    async def async_fetch(self, url, method='GET', headers=None, body=None):
        if not hasattr(self, '_why_relay_slots'):
            self._why_relay_slots = asyncio.Semaphore(3)
        async with self._why_relay_slots:
            return await asyncio.to_thread(relay_fetch, self, url, method, headers, body)
    AsyncExchange.fetch = async_fetch
    from freqtrade.freqtradebot import FreqtradeBot
    original_init, original_process = FreqtradeBot.__init__, FreqtradeBot.process
    def safe_init(self, cfg):
        validate_config(cfg, name)
        original_init(self, cfg)
        self.strategy.leverage = lambda *args, **kwargs: 1.0
        write_heartbeat(name, state='initialized', timeframe=self.strategy.timeframe)
    def safe_process(self):
        if self.config.get('dry_run') is not True:
            raise RuntimeError('SHADOW invariant violated')
        original_process(self)
        from freqtrade.exchange import timeframe_to_seconds
        ready=0
        ages=[]
        for pair in self.active_pair_whitelist:
            frame, _ = self.dataprovider.get_analyzed_dataframe(pair,self.strategy.timeframe)
            if len(frame):
                age=time.time()-frame.iloc[-1]['date'].timestamp()
                ages.append(age)
                if 0<=age<=timeframe_to_seconds(self.strategy.timeframe)*2+120:
                    ready+=1
        write_heartbeat(name, state='running' if ready==len(self.active_pair_whitelist) and ready else 'warming_up_or_analysis_error',
                        timeframe=self.strategy.timeframe, pairs=len(self.active_pair_whitelist),
                        analyzed_pairs=ready, oldest_analyzed_candle_age_s=round(max(ages),1) if ages else None)
    FreqtradeBot.__init__, FreqtradeBot.process = safe_init, safe_process
    from freqtrade.main import main as freqtrade_main
    try:
        freqtrade_main(['trade', '--config', str(BASE/'configs'/f'{name}.json'),
                        '--strategy-path', str(BASE/'strategies'/name)])
    except SystemExit as error:
        write_heartbeat(name,state='stopped',exit_code=error.code)
        raise
    except BaseException as error:
        write_heartbeat(name,state='error',error_type=type(error).__name__)
        raise

if __name__ == '__main__':
    main()
