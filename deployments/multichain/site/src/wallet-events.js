// Providers may announce their current account/chain again on mobile resume.
// Only a matching identity can preserve authentication; missing data fails closed.
export function walletIdentityChanged(event,value,{address,network}){
 if(event==='accountsChanged')return !address||!Array.isArray(value)||typeof value[0]!=='string'||value[0].toLowerCase()!==address.toLowerCase();
 if(event==='accountChanged')return !address||value?.toString()!==address;
 if(event==='chainChanged'){
  try{return !/^0x[0-9a-f]+$/i.test(value)||BigInt(value)!==BigInt(network.chainId);}
  catch{return true;}
 }
 return true; // Disconnects still invalidate the session.
}
