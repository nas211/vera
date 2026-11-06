import {expect,it,vi} from 'vitest';
import {walletRead} from '../vela/walletRpc';
it('serializes different reads and coalesces duplicate reads across consumers',async()=>{
 let finish!:(v:unknown)=>void;const provider={request:vi.fn(()=>new Promise<unknown>(r=>{finish=r;}))};
 const a=walletRead(provider,{method:'eth_getBalance',params:['owner','latest']});
 const duplicate=walletRead(provider,{method:'eth_getBalance',params:['owner','latest']});
 const b=walletRead(provider,{method:'eth_gasPrice'});await Promise.resolve();
 expect(a).toBe(duplicate);expect(provider.request).toHaveBeenCalledTimes(1);finish('0x1');await a;
 await Promise.resolve();expect(provider.request).toHaveBeenCalledTimes(2);finish('0x2');expect(await b).toBe('0x2');
});
it('backs off only rate-limited reads and succeeds without duplicating a wallet send',async()=>{
 vi.useFakeTimers();try{
  const provider={request:vi.fn().mockRejectedValueOnce({code:-32005,message:'Request is being rate limited.'}).mockResolvedValue('0x1')};
  const result=walletRead(provider,{method:'eth_estimateGas'});await vi.advanceTimersByTimeAsync(999);expect(provider.request).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);expect(await result).toBe('0x1');expect(provider.request).toHaveBeenCalledTimes(2);
 }finally{vi.useRealTimers();}
});
it('bounds retries and prevents background or queued reads hammering a limited provider',async()=>{
 vi.useFakeTimers();try{
  const provider={request:vi.fn().mockRejectedValue({code:429})};
  const a=walletRead(provider,{method:'eth_getBalance'}),b=walletRead(provider,{method:'eth_gasPrice'});
  const rejectedA=expect(a).rejects.toThrow('30 seconds'),rejectedB=expect(b).rejects.toThrow('30 seconds');
  await vi.advanceTimersByTimeAsync(3001);await rejectedA;await rejectedB;expect(provider.request).toHaveBeenCalledTimes(3);
  await expect(walletRead(provider,{method:'eth_getCode'})).rejects.toThrow('30 seconds');expect(provider.request).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(30000);provider.request.mockResolvedValue('0x2');expect(await walletRead(provider,{method:'eth_getCode'})).toBe('0x2');
 }finally{vi.useRealTimers();}
});
it.each(['eth_sendTransaction','personal_sign','eth_requestAccounts'])('never retries or delays the wallet action %s',async method=>{
 const error={code:-32005,message:'Request is being rate limited.'},provider={request:vi.fn().mockRejectedValue(error)};
 await expect(walletRead(provider,{method})).rejects.toBe(error);expect(provider.request).toHaveBeenCalledTimes(1);
});
it('never retries an execution revert',async()=>{
 const error={code:3,message:'execution reverted'},provider={request:vi.fn().mockRejectedValue(error)};
 await expect(walletRead(provider,{method:'eth_estimateGas'})).rejects.toBe(error);expect(provider.request).toHaveBeenCalledTimes(1);
});

it('releases the queue after a hung RPC and ignores its late result',async()=>{
 vi.useFakeTimers();try{
  let late!:(value:unknown)=>void;
  const provider={request:vi.fn().mockImplementationOnce(()=>new Promise(r=>{late=r;})).mockResolvedValue('0x2')};
  const a=walletRead(provider,{method:'eth_getBalance'}),b=walletRead(provider,{method:'eth_gasPrice'}),failed=expect(a).rejects.toThrow('timed out');
  await vi.advanceTimersByTimeAsync(8001);await failed;expect(await b).toBe('0x2');late('0x999');
  expect(provider.request).toHaveBeenCalledTimes(2);
 }finally{vi.useRealTimers();}
});
it('recognizes the rate limit nested inside a MetaMask RPC error',async()=>{
 vi.useFakeTimers();try{
  const provider={request:vi.fn().mockRejectedValueOnce({code:-32603,data:{originalError:{code:-32005}}}).mockResolvedValue('0x1')};
  const pending=walletRead(provider,{method:'eth_gasPrice'});await vi.advanceTimersByTimeAsync(1000);expect(await pending).toBe('0x1');
 }finally{vi.useRealTimers();}
});
