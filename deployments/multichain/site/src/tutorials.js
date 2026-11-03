// Storyboards and narration for interactive walkthroughs, not fabricated video.
// Optional future media: {src:'/tutorials/name.mp4',captions:'/tutorials/name.vtt'}.
// A supplied MP4 replaces the step player without changing the tutorial tile.
const step=(title,narration,visual,labels,seconds=10)=>({title,narration,visual,labels,seconds});
export const tutorials={
 overview:{title:'Choose Your VELA Product',steps:[
  step('Start with your wallet','Choose Vault, Borrow, AI Trade or Repay & Manage. Vault-only use needs no loan; AI Trade accepts direct demo allocation without a vault.','vault',['Your wallet','Testnet vault']),
  step('Choose trading capital','Borrow demo USDC against your DOGE collateral. This is simulated credit, not a transferable stablecoin. Your DOGE stays exposed to price changes.','borrow',['Test DOGE','Demo USDC']),
  step('Give AI permission','Set an allocation and authorize your agent. Start simulated trading when the model, market data and risk checks are ready.','trading',['Your permission','Risk checks','Trading engine']),
  step('Keep an eye on the whole picture','Trading losses reduce equity; DOGE price declines can threaten collateral. Stop new trades any time, or choose Stop & Settle to close positions and repay.','settle',['Trading equity','Debt','Eligible DOGE']),
 ]},
 vault:{title:'How Your DOGE Vault Works',videoPage:'https://app.heygen.com/videos/688a102c728e407aa4476fc9e05ca741',steps:[
  step('Your wallet owns your vault','Create one vault for your wallet on DogeOS Chikyū. Every deposit and withdrawal needs your approval. Use test DOGE from a faucet.','vault',['Your wallet','Your vault']),
  step('Use your vault independently','Vault-only use needs no loan or AI trading and earns no automatic yield. Once a borrowing request locks DOGE, that collateral cannot be freely withdrawn. Price changes still affect its value.','lock',['Test DOGE','Locked for debt']),
  step('Give bounded permission','Authorize an AI allocation and expiry. Contracts control vault actions; separate backend checks enforce trading limits. The AI cannot arbitrarily withdraw your DOGE.','trading',['Owner approval','Bounded agent']),
  step('Take control back','Revoke the agent or pause the vault. Stopping new trades does not remove debt or prevent liquidation. After settlement, withdraw only what the contract has unlocked.','settle',['Revoke or pause','Settle debt','Eligible withdrawal']),
 ]},
 borrow:{title:'Borrow Against DOGE',steps:[
  step('Start with collateral value','Your test DOGE amount and the illustrative DOGE price set the demo collateral value. No live lending oracle or real borrowing is involved.','borrow',['Test DOGE','Illustrative value']),
  step('Pick a borrowing ratio','A lower loan-to-value ratio means less debt for the same collateral. The borrowing button requests simulated USDC, then a wallet signature confirms the loan lock.','borrow',['Collateral','Borrowing ratio','Demo USDC']),
  step('Interest adds up','Demo interest increases what you owe over time, even if trading makes a loss. The scenario tool changes no account balances.','loan',['Principal','Interest','Total debt']),
  step('Watch DOGE as well as trades','A falling DOGE price can lower loan health and make collateral eligible for liquidation. Trading losses make repayment harder. Neither risk cancels the other.','risk',['DOGE price ↓','Trading equity ↓','Repayment risk']),
 ]},
 trading:{title:"How VELA AI Trading Works",steps:[
  step('Demo capital, real market inputs','Allocate demo capital directly, or use borrowed demo USDC. A vault and borrowing are optional for AI Trade. Lighter supplies market prices and depth. No real exchange orders are submitted.','trading',['Lighter data','AI decision','Simulated trade']),
  step('Every decision faces limits','Permission, market freshness, position size and loan health are checked before execution. The AI cannot overrule these checks.','risk',['AI proposal','Risk governor','Approved or blocked']),
  step('Loss limits have limits too','Each position keeps a fee-inclusive stop-trigger budget of $2 or less. Slippage and funding can cause larger realized losses. No return is guaranteed.','risk',['Position','Stop trigger','Possible slippage']),
  step('Stop is different from settle','Stop Trading blocks new entries and keeps protective management. Stop & Settle asks permission to close open simulated positions and repay debt.','settle',['Stop new entries','Or close & settle']),
 ]},
 agent:{title:'Your Complete AI Trading System',steps:[
  step('One account, four products','Repay & Manage uses your existing vault, loan and trading account. Use the existing demo vault and loan. Review repayment or settlement before confirming.','vault',['Your wallet','Demo vault','Simulated collateral']),
  step('Choose capital and permissions','Review your borrowing amount and interest, then confirm simulated USDC borrowing. Authorize bounded AI paper execution. No real exchange orders or stablecoins are issued.','trading',['Borrow','Authorize','Start AI']),
  step('Choose how to repay','Repay manually from free funds, sweep a percentage of new realized net profits, or set a daily, weekly or monthly payment with a minimum remaining balance. Revoke automation at any time.','loan',['Manual','Profit Sweep','Schedule']),
  step('Monitor, settle and unlock','Watch loan health as well as trading P&L. Stop and Settle explicitly closes positions and repays available funds; losses retain debt. Confirm eligible collateral release with your wallet.','settle',['Loan health','Repayment','Eligible DOGE']),
 ]},
 settle:{title:'How to Repay and Unlock Your DOGE',steps:[
  step('Stop & Settle closes positions','After your confirmation, new entries stop and simulated positions close at available market depth. The engine reconciles simulated equity.','settle',['Stop entries','Close positions','Reconcile']),
  step('Repay partially or in full','You can also repay any chosen amount without closing positions, using free demo equity with margin reserved. Profits are optional. Interest is paid first, then principal. Receipts identify simulated repayment.','loan',['Available equity','Debt repayment','Demo surplus']),
  step('A loss can leave a shortfall','If equity cannot repay the loan, unpaid debt stays recorded and collateral remains locked. Review simulated liquidation and any test DOGE transfer before approving settlement.','risk',['Shortfall','Locked collateral','Review settlement']),
  step('Confirm, then withdraw','Confirm settlement on the testnet contract. Withdraw only the DOGE the contract marks as eligible, with a separate wallet signature. No funds move in this walkthrough.','vault',['Confirm settlement','Eligible DOGE','Your wallet']),
 ]},
};
export const duration=tutorial=>tutorial.steps.reduce((seconds,s)=>seconds+s.seconds,0);

// Keep the existing player and DogeOS storyboards. Simulated networks need
// their own transaction wording; none of these steps asks for a token transfer.
export function networkTutorial(tab,chain,asset=chain?.collateral){
 if(!chain?.capabilities?.simulatedVault)return tutorials[tab];
 const original=tutorials[tab],replace=text=>text.replaceAll('Testnet vault','Simulated vault').replaceAll('Test DOGE','Demo '+asset).replaceAll('test DOGE','demo '+asset).replaceAll('DOGE',asset);
 const steps=original.steps.map(s=>({...s,title:replace(s.title),narration:replace(s.narration),labels:s.labels.map(replace)}));
 if(tab==='vault'){
  steps[0].narration=`Create a simulated ${asset} vault for your wallet on ${chain.name}. Allocate demo collateral locally. No wallet transfer or faucet funds are needed.`;
  steps[2].narration=`Authorize an AI allocation and expiry. Shared risk checks bound paper trading. The AI cannot withdraw real ${asset}.`;
  steps[3].narration='Revoke the agent or pause the vault. Debt stays recorded. Repay fully before removing eligible demo collateral; no tokens move.';
 }
 if(tab==='borrow')steps[1].narration='A lower loan-to-value ratio means less debt for the same collateral. Confirm a simulated USDC loan; demo collateral becomes locked in the local ledger. No wallet transaction is requested.';
 if(tab==='agent'){
  steps[0].narration=`Connect a compatible ${chain.name} wallet and sign an authentication message. Create a simulated vault and allocate demo ${asset}. Balances stay specific to this wallet and network.`;
  steps[0].labels=['Your wallet','Simulated vault','Demo '+asset];
  steps[3].narration='Watch loan health and trading P&L. Stop & Settle closes paper positions and repays available funds. Losses retain debt; only eligible demo collateral unlocks.';
 }
 if(tab==='settle'){
  steps[2].narration='If equity cannot repay the loan, unpaid debt stays recorded and collateral remains locked. Review the simulated collateral sale; no real assets move.';
  steps[3].narration=`Full simulated repayment unlocks eligible demo ${asset}. Confirm its removal from the simulation ledger. Demo balances are not redeemable and nothing transfers to your wallet.`;
 }
 return {title:replace(original.title),steps};
}
