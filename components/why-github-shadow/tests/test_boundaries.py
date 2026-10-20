import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_relay import valid_path
from run_shadow import public_target, validate_config, NAMES

class Boundaries(unittest.TestCase):
    def test_public_market_requests(self):
        self.assertTrue(valid_path('/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=400'))
        self.assertTrue(valid_path('/v5/market/funding/history?category=linear&symbol=BTCUSDT'))
        self.assertTrue(valid_path('/v5/market/risk-limit?category=linear&cursor=first%3DBTCUSDT%26last%3DETHUSDT'))

    def test_private_write_and_arbitrary_routes_denied(self):
        for target in ['/v5/order/create','/v5/account/wallet-balance',
                       '//evil.test/v5/market/time','https://evil.test/v5/market/time',
                       '/v5/market/time?api_key=secret','/v5/market/../order/create']:
            self.assertFalse(valid_path(target),target)
        for url,method,headers,body in [
            ('https://api.bybit.com/v5/market/time','POST',{},None),
            ('https://api.bybit.com/v5/order/create','GET',{},None),
            ('https://api.bybit.com/v5/market/time','GET',{'X-BAPI-API-KEY':'x'},None),
            ('https://evil.test/v5/market/time','GET',{},None),
            ('https://api.bybit.com/v5/market/time','GET',{},'x')]:
            with self.assertRaises(ValueError): public_target(url,method,headers,body)

    def test_all_six_configs_fail_closed(self):
        base=Path(__file__).resolve().parents[1]
        for name in NAMES:
            cfg=json.loads((base/'configs'/f'{name}.json').read_text())
            validate_config(cfg,name)
            for key,value in [('dry_run',False),('max_open_trades',100),('stake_amount',10000),
                              ('max_entry_position_adjustment',-1),('add_config_files',['secret.json'])]:
                changed=copy.deepcopy(cfg);changed[key]=value
                with self.assertRaises(ValueError):validate_config(changed,name)
            for key,value in [('secret','secret'),('enable_ws',True),('name','binance')]:
                changed=copy.deepcopy(cfg);changed['exchange'][key]=value
                with self.assertRaises(ValueError):validate_config(changed,name)

if __name__=='__main__':unittest.main()
