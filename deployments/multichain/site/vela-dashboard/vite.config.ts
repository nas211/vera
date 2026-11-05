import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import fs from "node:fs";
import { createHash } from "node:crypto";
import componentTagger from "./plugins/component-tagger";

function sourceManifest(testnet: boolean) {
  const names: string[] = [];
  const collect = (relative: string) => {
    const file = path.join(__dirname, relative);
    if (fs.lstatSync(file).isSymbolicLink()) throw new Error(`Redirected build input: ${relative}`);
    if (fs.statSync(file).isDirectory()) {
      for (const child of fs.readdirSync(file)) collect(`${relative}/${child}`);
    } else names.push(relative);
  };
  for (const name of ['src', 'public', 'index.html', 'preview.html', 'vela.html', 'vite.config.ts', 'package.json', 'package-lock.json', 'tailwind.config.js', 'postcss.config.js', 'tsconfig.app.json']) collect(name);
  const files: Record<string, string> = {};
  for (const name of names.sort()) files[name] = createHash('sha256').update(fs.readFileSync(path.join(__dirname, name))).digest('hex');
  if (!testnet) {
    const sourceId = createHash('sha256').update(JSON.stringify(files)).digest('hex');
    return { schemaVersion: 1, sourceId, files };
  }
  // Bind the shared wallet/auth code and release mode into the active build identity.
  const sharedFiles: Record<string, string> = {};
  const shared = (relative: string) => {
    const file = path.join(__dirname, '../src', relative);
    if (fs.lstatSync(file).isSymbolicLink()) throw new Error(`Redirected wallet input: ${relative}`);
    if (fs.statSync(file).isDirectory()) for (const child of fs.readdirSync(file).sort()) shared(relative ? `${relative}/${child}` : child);
    else sharedFiles[relative] = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  };
  shared('');
  const sortedShared = Object.fromEntries(Object.entries(sharedFiles).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
  const content = { files, sharedFiles: sortedShared, profile: 'DOGEOS_TESTNET' };
  return { schemaVersion: 2, sourceId: createHash('sha256').update(JSON.stringify(content)).digest('hex'), ...content };
}

export default defineConfig(({ mode }) => {
const testnet = mode === 'testnet';
const provenance = sourceManifest(testnet);
const input: Record<string, string> = testnet ? { vela: path.resolve(__dirname, 'vela.html') }
  : { fixture: path.resolve(__dirname, 'index.html'), preview: path.resolve(__dirname, 'preview.html'), vela: path.resolve(__dirname, 'vela.html') };
return {
  define: { __VELA_SOURCE_ID__: JSON.stringify(provenance.sourceId), __VELA_TESTNET_RELEASE__: JSON.stringify(testnet) },
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: true,
    hmr: {
      overlay: false,
      timeout: 15000,
    },
    watch: {
      usePolling: true,
      interval: 500,
      binaryInterval: 500,
    },
  },
  plugins: [react(), mode === "development" && componentTagger(), {
    name: 'vela-source-provenance',
    transformIndexHtml: () => [{ tag: 'meta', attrs: { name: 'vela-source-id', content: provenance.sourceId }, injectTo: 'head' }],
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'source-manifest.json', source: JSON.stringify(provenance, null, 2) + '\n' });
    },
  }],
  base: './',
  build: {
    outDir: testnet ? 'dist-testnet' : 'dist',
    rollupOptions: { input },
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
};
});
