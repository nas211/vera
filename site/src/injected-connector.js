// DogeOS SDK 4.0.4 imports only `injected` from wagmi/connectors.
// That barrel re-exports this exact connector from @wagmi/core, but eagerly
// resolving it also pulls optional, unused native-wallet SDKs into the build.
export { injected } from "@wagmi/core";
