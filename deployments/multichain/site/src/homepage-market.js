// Public price presentation only; never an account or execution input.
export function validMarket(m,now=Date.now()){
 return Boolean(m?.fresh&&Number.isFinite(Number(m.price))&&Number(m.price)>0&&
  Number.isFinite(m.statsAt)&&now-m.statsAt*1000>=-2000&&now-m.statsAt*1000<=15000&&
  Number.isFinite(m.bookAt)&&now-m.bookAt*1000>=-2000&&now-m.bookAt*1000<=5000);
}
