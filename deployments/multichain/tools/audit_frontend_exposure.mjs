// Match registry advisory ranges to exact locked versions and rendered modules.
// Run after refreshing the bulk advisory responses. This is a reachability check,
// not an independent audit or proof that an affected function is exploitable.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dashboard = path.join(root, 'site/vela-dashboard');
const require = createRequire(path.join(dashboard, 'package.json'));
const semver = require('semver');
const { build } = await import(require.resolve('vite'));
const evidence = path.join(root, 'verification/noah');
const snapshotPath = path.join(root, 'config/security/npm-advisories-20261105.json');
const snapshot = JSON.parse(fs.readFileSync(snapshotPath));
const findings = [];
const inputs = {};
for (const directory of ['site', 'site/vela-dashboard', 'site/products-toolchain']) {
  const lockName = path.join(root, directory, 'package-lock.json');
  for (const file of [lockName, snapshotPath]) {
    inputs[path.relative(root, file)] = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }
  const lock = JSON.parse(fs.readFileSync(lockName));
  let advisories = snapshot.responses[directory];
  if (process.argv.includes('--refresh')) {
    const versions = {};
    for (const [node, metadata] of Object.entries(lock.packages)) {
      if (!node || !metadata.version) continue;
      const name = metadata.name || node.split('node_modules/').at(-1);
      (versions[name] ||= new Set()).add(metadata.version);
    }
    const response = await fetch(snapshot.endpoint, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(Object.entries(versions).map(([name, values]) => [name, [...values]]))),
      signal: AbortSignal.timeout(45000),
    });
    if (!response.ok) throw new Error(`Advisory service returned ${response.status}`);
    advisories = await response.json();
  }
  if (!advisories || typeof advisories !== 'object' || Array.isArray(advisories)) throw new Error('Invalid advisory snapshot');
  for (const [node, metadata] of Object.entries(lock.packages)) {
    if (!node || !metadata.version) continue;
    const name = metadata.name || node.split('node_modules/').at(-1);
    for (const advisory of advisories[name] || []) {
      if (!semver.satisfies(metadata.version, advisory.vulnerable_versions, { includePrerelease: true })) continue;
      findings.push({ directory, node, name, version: metadata.version,
        development: Boolean(metadata.dev), severity: advisory.severity,
        advisory: advisory.url, title: advisory.title, affectedRange: advisory.vulnerable_versions,
        absolute: path.join(root, directory, node), rendered: {} });
    }
  }
}
process.chdir(dashboard);
const builds = {};
for (const mode of ['testnet', 'production']) {
  await build({ mode, build: { write: false }, plugins: [{
    name: 'audit-exact-package-modules',
    generateBundle(_options, bundle) {
      const rendered = [];
      for (const chunk of Object.values(bundle)) {
        if (chunk.type !== 'chunk') continue;
        for (const [id, info] of Object.entries(chunk.modules)) {
          if (info.renderedLength > 0) rendered.push({
            id, bytes: info.renderedLength, chunk: chunk.fileName,
          });
        }
      }
      for (const finding of findings) {
        finding.rendered[mode] = rendered.filter(m =>
          m.id.startsWith(finding.absolute+'/') || m.id.includes('\0'+finding.absolute+'/'))
          .map(m => ({ ...m, id: path.relative(root, m.id.replace(/^\0/, '')) }));
      }
      builds[mode] = {
        entries: Object.values(bundle).filter(c => c.type === 'chunk' && c.isEntry).map(c => c.fileName),
        modules: rendered.length,
        affectedPackages: [...new Set(findings.filter(f => f.rendered[mode].length).map(f => f.name))],
      };
    },
  }] });
}
for (const finding of findings) delete finding.absolute;
// The legacy fixture imports UUID v4 through MetaMask. This reviewed advisory
// concerns v3/v5/v6 buffer bounds; reject any expansion beyond the exact v4 files.
const uuidV4Files = new Set(['rng.js', 'regex.js', 'validate.js', 'stringify.js', 'v4.js']);
const fixtureUnassessed = findings.filter(f => f.rendered.production.length && !(
  f.name === 'uuid' && f.advisory === 'https://github.com/advisories/GHSA-w5hq-g745-h8pq'
  && f.rendered.production.every(m => m.id.includes('/uuid/dist/esm-browser/') && uuidV4Files.has(path.basename(m.id)))
));
const result = {
  observedAt: new Date().toISOString(), advisorySource: 'npm registry bulk advisory API',
  advisoryMode: process.argv.includes('--refresh') ? 'Fresh registry query for each lockfile' : 'Saved registry snapshot',
  advisorySnapshotAt: snapshot.observedAt,
  inputs, builds, findings,
  scope: 'Three maintained npm lockfiles; exact versions and browser module reachability. No backend, contract or whole-repository security certification.',
  connectedBuildGate: builds.testnet.affectedPackages.length ? 'FAIL' : 'PASS',
  fixtureBuildGate: fixtureUnassessed.length ? 'FAIL' : 'PASS_WITH_UUID_V4_ASSESSMENT',
  fixtureUnassessed,
  review: 'docs/VELA-HACKATHON-SECURITY-REVIEW.txt',
};
const output = path.join(evidence, 'green-frontend-exposure.json');
fs.mkdirSync(evidence, { recursive: true });
fs.writeFileSync(output, JSON.stringify(result, null, 2)+'\n');
console.log(JSON.stringify({ output, gate: result.connectedBuildGate, builds,
  fixtureGate: result.fixtureBuildGate, advisoryInstances: findings.length }));
if (result.connectedBuildGate !== 'PASS' || fixtureUnassessed.length) process.exitCode = 1;
