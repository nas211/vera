/** Export the existing sample-only Noah UI for review by opening a local file. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { Script } from 'node:vm';
import { build } from '../site/node_modules/esbuild/lib/main.js';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const app = path.join(repo, 'site/vela-dashboard');
const output = path.resolve(process.argv[2] || path.join(repo, '.runtime/Noah-preview.html'));
const hash = value => createHash('sha256').update(value).digest('hex');
const manifest = JSON.parse(fs.readFileSync(path.join(app, 'dist/source-manifest.json'), 'utf8'));
if (hash(JSON.stringify(manifest.files)) !== manifest.sourceId) throw Error('Invalid source identity');
for (const [name, digest] of Object.entries(manifest.files)) {
  if (hash(fs.readFileSync(path.join(app, name))) !== digest) throw Error(`Stale source: ${name}`);
}
const template = fs.readFileSync(path.join(app, 'dist/preview.html'), 'utf8');
if (!template.includes(manifest.sourceId)) throw Error('Preview source identity differs');
const sheets = [...template.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="(\.\/[^\"]+)"[^>]*>/g)].map(m => m[1]);
if (sheets.length !== 1) throw Error('Expected one compiled preview stylesheet');
const css = fs.readFileSync(path.join(app, 'dist', sheets[0]), 'utf8');
if (/@import\b|url\(\s*["']?(?!data:)[a-zA-Z/.]/.test(css)) throw Error('Unbundled CSS asset');
const options = {
  absWorkingDir: app,
  bundle: true, write: false, minify: true, metafile: true,
  jsx: 'automatic', target: 'es2022', legalComments: 'inline',
  alias: { '@': path.join(app, 'src'), react: path.join(app, 'node_modules/react'), 'react-dom': path.join(app, 'node_modules/react-dom') },
  define: { __VELA_SOURCE_ID__: JSON.stringify(manifest.sourceId), 'import.meta.env': '{}', 'process.env.NODE_ENV': '"production"' },
  loader: { '.css': 'empty', '.svg': 'dataurl', '.png': 'dataurl', '.jpg': 'dataurl' },
  // The downloadable preview must never include a wallet SDK or connector.
  // Match the existing preview tests: any unexpected wallet hook fails closed.
  plugins: [{ name: 'offline-wallet-boundary', setup(plugin) {
    plugin.onResolve({ filter: /^(wagmi|@rainbow-me\/rainbowkit)$/ }, args => ({ path: args.path, namespace: 'offline-wallet' }));
    plugin.onLoad({ filter: /.*/, namespace: 'offline-wallet' }, args => ({ contents: args.path === 'wagmi'
      ? `function disabled() { throw Error('Wallet access is disabled in the Noah HTML preview'); } export { disabled as useAccount, disabled as useBalance, disabled as useDisconnect };`
      : `function disabled() { throw Error('Wallet connections are disabled in the Noah HTML preview'); } export const ConnectButton = { Custom: disabled }; export { disabled as useConnectModal };`
    }));
  } }],
};
const client = await build({ ...options, entryPoints: ['src/preview/main.tsx'], platform: 'browser', format: 'iife' });
if (client.outputFiles.length !== 1) throw Error('Preview needs additional assets');
const inputs = Object.keys(client.metafile.inputs);
if (inputs.some(name => /src\/(?:service\/|lib\/wagmi\.ts|.*Web3Provider)/.test(name))) throw Error('Service or wallet bootstrap in preview');
const js = client.outputFiles[0].text;
new Script(js); // Syntax check only; this does not execute browser code.

// Include the real Overview markup so a script-disabled HTML viewer can show it.
const server = await build({
  ...options, platform: 'node', format: 'cjs',
  stdin: { resolveDir: app, loader: 'tsx', contents: `
    import { renderToStaticMarkup } from 'react-dom/server';
    import { MemoryRouter } from 'react-router-dom';
    import App from './src/App';
    export function render(route = '/') {
      return renderToStaticMarkup(<MemoryRouter initialEntries={[route]}><App preview /></MemoryRouter>);
    }
  ` },
});
const mod = { exports: {} };
new Function('module', 'exports', 'require', server.outputFiles[0].text)(mod, mod.exports, createRequire(path.join(app, 'package.json')));
const routes = ['/', '/vault', '/borrow', '/trading', '/risk'];
const rendered = routes.map(route => {
  const html = mod.exports.render(route);
  for (const expected of ['Noah UI preview', 'sample balances and actions', 'Preview · no wallet', manifest.sourceId.slice(0, 12)]) {
    if (!html.includes(expected)) throw Error(`Missing ${expected} on ${route}`);
  }
  if (html.includes('Connect wallet')) throw Error(`Wallet control enabled on ${route}`);
  return html;
});
const overview = rendered[0].replace(/href="\//g, 'href="#/');
const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'";
let html = template
  .replace(/<script\b[^>]*src="[^\"]+"[^>]*><\/script>\s*/g, '')
  .replace(/<link\b[^>]*rel="(?:modulepreload|preconnect)"[^>]*>\s*/g, '')
  .replace(/<link\b[^>]*rel="stylesheet"[^>]*href="\.\/[^\"]+"[^>]*>\s*/g, '')
  .replace('<meta charset="UTF-8" />', `<meta charset="UTF-8" />\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />`)
  .replace('</head>', () => `<style>${css.replace(/<\/style/gi, '<\\/style')}</style>\n  </head>`)
  .replace('<div id="root"></div>', () => `<div id="root">${overview}</div>\n    <noscript>Open this file with JavaScript enabled to navigate the five sample screens.</noscript>`)
  .replace('</body>', () => `<script>${js.replace(/<\/script/gi, '<\\/script')}</script>\n  </body>`);
const documentWithoutScript = html.replace(/<script>[\s\S]*?<\/script>/g, '');
if (/<script\b[^>]*\bsrc=|rel="modulepreload"|(?:src|href)="\.\//.test(documentWithoutScript)) throw Error('External application asset remains');
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, html);
const report = {
  artifact: path.relative(repo, output), sourceId: manifest.sourceId,
  sha256: hash(html), bytes: Buffer.byteLength(html),
  checks: { sourceHashes: true, javascriptSyntax: true, renderedSampleRoutes: routes, inlineApplicationAssets: true, serviceAndWalletBootstrapAbsent: true, networkConnectionsBlockedByCsp: true },
  optionalExternalAssets: ['Google Fonts styles and font files; system fonts are the offline fallback'],
  walletSdk: 'Excluded from this HTML export; all wallet entry points throw if reached',
  browserVerified: false, published: false, liveProtectionVerified: false,
};
fs.writeFileSync(path.join(repo, 'verification/noah/standalone-html.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
