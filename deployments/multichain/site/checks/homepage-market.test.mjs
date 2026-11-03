import test from 'node:test';
import assert from 'node:assert/strict';
import {validMarket} from '../src/homepage-market.js';
const now=1800000000000,market={fresh:true,price:'84432.3',statsAt:now/1000,bookAt:now/1000};
test('public prices require current server freshness, stats and orderbook timestamps',()=>{
 assert.equal(validMarket(market,now),true);
 for(const patch of [{fresh:false},{price:'NaN'},{price:0},{statsAt:now/1000-16},{bookAt:now/1000-6},{statsAt:now/1000+3},{bookAt:now/1000+3},{statsAt:undefined}])assert.equal(validMarket({...market,...patch},now),false);
 assert.equal(validMarket(undefined,now),false);
});
test('a previously live price automatically expires even if the next fetch stalls',()=>{
 assert.equal(validMarket(market,now+4999),true);
 assert.equal(validMarket(market,now+5001),false);
});
