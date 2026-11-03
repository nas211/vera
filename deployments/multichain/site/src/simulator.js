// Public, illustrative scenario math. Never credits a ledger or supplies an oracle.
export function simulateVault({doge,price,ltv,apr,days,performance,drop=0,maxLtv=.4,liquidationThreshold=.65}){
 const values=[doge,price,ltv,apr,days,performance,drop,maxLtv,liquidationThreshold].map(Number);
 if(values.some(v=>!Number.isFinite(v)))throw Error('Enter finite scenario values.');
 [doge,price,ltv,apr,days,performance,drop,maxLtv,liquidationThreshold]=values;
 if(doge<0||doge>1e12||price<=0||price>1e6||ltv<0||ltv>maxLtv||maxLtv<=0||maxLtv>=liquidationThreshold||liquidationThreshold>1||apr<0||apr>1||days<0||days>3650||performance< -1||performance>10||drop<0||drop>=1)throw Error('Scenario values are outside the illustrative limits.');
 const baselineValue=doge*price,principal=baselineValue*ltv,interest=principal*apr*days/365;
 const debt=principal+interest,collateralValue=baselineValue*(1-drop),equity=principal*(1+performance);
 return {baselineValue,collateralValue,price:price*(1-drop),principal,interest,debt,
  borrowingCapacity:baselineValue*maxLtv,currentLtv:collateralValue?debt/collateralValue:0,
  liquidationThreshold,healthFactor:debt?collateralValue*liquidationThreshold/debt:null,
  liquidationPrice:doge&&debt?debt/(doge*liquidationThreshold):null,equity,
  surplus:Math.max(0,equity-debt),shortfall:Math.max(0,debt-equity),
  liquidationEligible:debt>0&&collateralValue*liquidationThreshold<debt};
}
