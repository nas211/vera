import test from 'node:test';
import assert from 'node:assert/strict';
import {walletIdentityChanged} from '../src/wallet-events.js';
const address='0x'+'ab'.repeat(20), network={chainId:6281971};
const context={address,network};
test('reannouncing the connected EVM identity does not discard a pending review',()=>{
 assert.equal(walletIdentityChanged('accountsChanged',[address.toUpperCase()],context),false);
 assert.equal(walletIdentityChanged('chainChanged','0x'+network.chainId.toString(16),context),false);
});
test('changed, removed or malformed wallet identities still revoke authentication',()=>{
 for(const value of [[],null,[42],['0x'+'cd'.repeat(20)]])assert.equal(walletIdentityChanged('accountsChanged',value,context),true);
 for(const value of [null,'bad','0x1',undefined])assert.equal(walletIdentityChanged('chainChanged',value,context),true);
 assert.equal(walletIdentityChanged('disconnect',{},context),true);
});
test('Solana keys remain case-sensitive and an unbound account fails closed',()=>{
 assert.equal(walletIdentityChanged('accountChanged',{toString:()=> 'AbC'},{address:'AbC',network:{}}),false);
 assert.equal(walletIdentityChanged('accountChanged',{toString:()=> 'abc'},{address:'AbC',network:{}}),true);
 assert.equal(walletIdentityChanged('accountsChanged',[address],{address:'',network}),true);
});
