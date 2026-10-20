import { chain, networkConfig } from "./network.js";
import { decodeErrorResult, encodeFunctionData, formatEther, keccak256, parseAbi, parseTransaction, recoverTransactionAddress, serializeTransaction } from "viem";

const feeAbi = parseAbi(["function getL1Fee(bytes data) view returns (uint256)"]);
const feeOracle = "0x5300000000000000000000000000000000000002";

export async function estimateFees(tx, read=rpc, signedRaw=null) {
  // Nonzero signature placeholders bound the byte cost before asking the wallet.
  const raw = signedRaw || serializeTransaction({type:"legacy",chainId:chain.id,
    nonce:Number(BigInt(tx.nonce)),gas:BigInt(tx.gas),gasPrice:BigInt(tx.gasPrice),
    to:tx.to,value:BigInt(tx.value||"0x0"),data:tx.data},
    {r:`0x${"7f".repeat(32)}`,s:`0x${"7f".repeat(32)}`,v:BigInt(chain.id*2+36)});
  const data = encodeFunctionData({abi:feeAbi,functionName:"getL1Fee",args:[raw]});
  let dataFee;
  try {
    const result=await read("eth_call",[{to:networkConfig.feeOracle||feeOracle,data},"latest"]);
    if(typeof result!=="string" || !/^0x[0-9a-f]{64}$/i.test(result))throw Error("Invalid fee result");
    dataFee=BigInt(result);
    if(dataFee<=0n)throw Error("Invalid fee");
  } catch {throw new Error("The full network fee is unavailable. Nothing was submitted; retry when the fee service recovers.");}
  const execution=BigInt(tx.gas)*BigInt(tx.gasPrice),total=execution+dataFee;
  return {execution,data:dataFee,total,allowance:(total*150n+99n)/100n};
}

export async function rpc(method, params=[]) {
  const response = await fetch(networkConfig.rpcUrls[0], {
    method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({jsonrpc:"2.0",id:1,method,params}), signal:AbortSignal.timeout(15000)
  });
  if (!response.ok) throw new Error("The network is busy. Please retry in a moment.");
  const data = await response.json();
  if (data.error) {
    const error = new Error(data.error.message || "Network request failed");
    error.data = data.error.data;error.code = data.error.code;throw error;
  }
  return data.result;
}

export async function prepareTransaction(tx, abi, read=rpc) {
  if (BigInt(await read("eth_chainId")) !== BigInt(chain.id)) throw new Error("Network mismatch. Reconnect to DogeOS.");
  const balance = BigInt(await read("eth_getBalance",[tx.from,"pending"]));
  const value = BigInt(tx.value || "0x0");
  if (balance <= value) throw new Error(`This wallet has ${formatEther(balance)} DOGE. Use the faucet for this connected address, or connect the wallet you already funded. Leave some DOGE for gas.`);
  let estimate;
  try { estimate = BigInt(await read("eth_estimateGas",[tx])); }
  catch (error) {
    let reason;
    try {reason=decodeErrorResult({abi,data:typeof error.data==="string"?error.data:error.data?.data}).errorName;} catch {}
    throw new Error(reason ? `The vault cannot accept this request: ${reason}. Please refresh and retry.` : error.message);
  }
  const gas = estimate * 12n / 10n;
  const gasPrice = BigInt(await read("eth_gasPrice"));
  const nonce = await read("eth_getTransactionCount",[tx.from,"pending"]);
  const transaction={...tx,type:"0x0",nonce,gas:`0x${gas.toString(16)}`,gasPrice:`0x${gasPrice.toString(16)}`};
  const fees=await estimateFees(transaction,read);
  if (balance < value + fees.allowance) throw new Error(`This transfer needs about ${formatEther(fees.total)} DOGE in network fees, including data/finality fees. Reduce the amount or add DOGE; leave ${formatEther(fees.allowance)} DOGE for fee changes.`);
  return {transaction,fees,balance};
}

export async function submitTransaction(provider, tx, embedded, onSigned, read=rpc, approvedAllowance=null) {
  if (!embedded) return provider.request({method:"eth_sendTransaction",params:[tx]});
  // The email wallet signs once; broadcast directly to the official chain RPC.
  // Its hosted send-transaction wrapper can fail after signing.
  const raw = await provider.request({method:"eth_signTransaction",params:[tx]});
  if (typeof raw !== "string" || !/^0x[0-9a-f]+$/i.test(raw)) throw new Error("The wallet did not return a signed transaction.");
  const signed = parseTransaction(raw), signer = await recoverTransactionAddress({serializedTransaction:raw});
  if (signer.toLowerCase()!==tx.from.toLowerCase() || signed.to?.toLowerCase()!==tx.to.toLowerCase() ||
      BigInt(signed.chainId)!==BigInt(chain.id) || (signed.value||0n)!==BigInt(tx.value) ||
      (signed.data||"0x").toLowerCase()!==tx.data.toLowerCase() || BigInt(signed.nonce)!==BigInt(tx.nonce) ||
      signed.gas!==BigInt(tx.gas) || signed.gasPrice!==BigInt(tx.gasPrice)) {
    throw new Error("The signed transaction does not match your request. Nothing was broadcast by Vela.");
  }
  const hash=keccak256(raw);
  if(approvedAllowance!==null){
    const fresh=await estimateFees(tx,read,raw);
    if(fresh.total>approvedAllowance)throw new Error("The network fee rose above your reviewed allowance. Nothing was broadcast by Vela. Review again.");
    const balance=BigInt(await read("eth_getBalance",[tx.from,"pending"]));
    if(balance<BigInt(tx.value)+fresh.allowance)throw new Error("The wallet balance changed. Nothing was broadcast by Vela. Review again.");
  }
  onSigned?.(hash); // Retain identity/hash before a potentially ambiguous network response.
  try {
    const result=await read("eth_sendRawTransaction",[raw]);
    if (result.toLowerCase()!==hash.toLowerCase()) throw new Error("Transaction hash mismatch.");
  } catch (error) {
    if (error.code && !/already known|known transaction/i.test(error.message)) {
      let exists;
      try {exists=await read("eth_getTransactionByHash",[hash]);} catch {}
      if (exists===null) {
        onSigned?.(null);
        throw new Error(`The network rejected the signed request: ${error.message}. Refresh your balance and retry.`);
      }
    }
    // A send timeout cannot prove failure. Track this exact hash without a new signature.
    // The dashboard keeps it pending and checks the receipt; no second signature.
  }
  return hash;
}

export function walletError(error) {
  if (Number(error?.code)===4001) return "Cancelled in your wallet. No transaction submitted.";
  const message = error?.shortMessage || error?.message || "Transaction could not be submitted.";
  if (/Wallet transaction is temporarily unavailable/i.test(message)) {
    return "Your email wallet logged in, but MyDoge’s transaction service cannot sign right now. Reconnect and retry, or choose MyDoge mobile / MetaMask below. No transaction hash was returned.";
  }
  return message;
}
