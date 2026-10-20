import { chain, networkConfig } from "./network.js";
import { decodeErrorResult, formatEther, keccak256, parseTransaction, recoverTransactionAddress } from "viem";

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
  if (balance < value + gas * gasPrice) throw new Error("Leave more DOGE in your wallet for gas, or reduce the amount.");
  const nonce = await read("eth_getTransactionCount",[tx.from,"pending"]);
  return {...tx,type:"0x0",nonce,gas:`0x${gas.toString(16)}`,gasPrice:`0x${gasPrice.toString(16)}`};
}

export async function submitTransaction(provider, tx, embedded, onSigned, read=rpc) {
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
