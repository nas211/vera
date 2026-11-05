export function walletAttempt(onTimeout,timeoutMs=45000){
 const controller=new AbortController();let active=true;
 const cancel=()=>{active=false;clearTimeout(timer);controller.abort();};
 const timer=setTimeout(()=>{if(active){cancel();onTimeout();}},timeoutMs);
 return {signal:controller.signal,current:()=>active,cancel,
  finish:()=>{active=false;clearTimeout(timer);}};
}
