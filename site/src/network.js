import config from "./chain-config.json";
export const networkConfig = config;
export const chain = Object.freeze({
  id: config.chainId,
  name: config.chainName,
  nativeCurrency: config.nativeCurrency,
  rpcUrls: { default: { http: config.rpcUrls } },
  blockExplorers: { default: { name: "DogeOS Explorer", url: config.blockExplorerUrls[0] } },
  testnet: true
});
