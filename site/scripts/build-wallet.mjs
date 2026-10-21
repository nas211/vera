import { build } from "esbuild";
import { mkdir, readFile, writeFile, rm } from "node:fs/promises";
await rm("dist/assets", { recursive: true, force: true });
await mkdir("dist/assets", { recursive: true });
// Canonical repo configuration is copied into the standalone Sites source.
let network;
try {
  network = JSON.parse(await readFile("../config/dogeos-testnet.json", "utf8"));
  await writeFile("src/chain-config.json", JSON.stringify(network, null, 2) + "\n");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  network = JSON.parse(await readFile("src/chain-config.json", "utf8"));
}
await writeFile("dist/network-config.js", `window.VELA_NETWORK = Object.freeze(${JSON.stringify({
  chainId:`0x${network.chainId.toString(16)}`, chainName:network.chainName,
  nativeCurrency:network.nativeCurrency, rpcUrls:network.rpcUrls, blockExplorerUrls:network.blockExplorerUrls
})});\n`);
await build({
  entryPoints: ["src/mydoge-entry.jsx"],
  outdir: "dist/assets",
  entryNames: "mydoge",
  chunkNames: "wallet-[name]-[hash]",
  assetNames: "wallet-[name]-[hash]",
  bundle: true,
  format: "esm",
  splitting: true,
  platform: "browser",
  alias: { "wagmi/connectors": "./src/injected-connector.js" },
  target: ["es2022"],
  jsx: "automatic",
  minify: true,
  legalComments: "linked",
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".svg": "dataurl", ".png": "dataurl", ".woff": "file", ".woff2": "file" },
  logLevel: "info",
  logLimit: 12
});
// The wallet-free paper desk shares React and Vela's design, with no wallet SDK.
await build({
  entryPoints: ["src/paper-entry.jsx"], outfile: "dist/assets/paper.js",
  bundle: true, format: "esm", platform: "browser", target: ["es2022"],
  jsx: "automatic", minify: true, legalComments: "linked",
  define: { "process.env.NODE_ENV": '"production"' }, logLevel: "info"
});
// SDK CSS is needed only on the MyDoge screen, never on the marketing page.
await build({
  entryPoints: ['src/portal-entry.jsx'], outdir:'dist/assets', entryNames:'portal',
  chunkNames:'portal-[name]-[hash]', assetNames:'portal-[name]-[hash]',
  bundle:true, format:'esm', splitting:true, platform:'browser', target:['es2022'],
  alias:{'wagmi/connectors':'./src/injected-connector.js'}, jsx:'automatic',
  minify:true, legalComments:'linked', define:{'process.env.NODE_ENV':'"production"'},
  loader:{'.svg':'dataurl','.png':'dataurl','.woff':'file','.woff2':'file'},logLevel:'info',logLimit:5
});
const page = await readFile("dist/mydoge.html", "utf8");
await writeFile("dist/mydoge.html", page.replace(
  /(?:  <link rel="stylesheet" href="\/assets\/mydoge.css">\n)?  <link rel="stylesheet" href="\/mydoge.css">/,
  '  <link rel="stylesheet" href="/assets/mydoge.css">\n  <link rel="stylesheet" href="/mydoge.css">'
));
