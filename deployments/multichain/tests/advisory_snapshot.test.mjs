import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkedSnapshot, digest, saveSnapshot } from '../tools/advisory_snapshot.mjs';

const locks = { 'site': '{"packages":{"node_modules/a":{"version":"1.0.0"}}}' };
const snapshot = JSON.stringify({ responses: { site: {} } });
const provenance = { snapshotSha256: digest(snapshot), lockfiles: { site: digest(locks.site) } };

test('an empty advisory result is accepted only for its exact queried lock', () => {
  assert.deepEqual(checkedSnapshot(snapshot, provenance, locks).responses.site, {});
});

test('adding or changing dependencies requires a new query', () => {
  assert.throws(() => checkedSnapshot(snapshot, provenance, { site: locks.site+' ' }), /lock changed/);
  assert.throws(() => checkedSnapshot(snapshot, provenance, { ...locks, dashboard: '{}' }), /not queried/);
});

test('omitted lock binding and altered responses fail closed', () => {
  assert.throws(() => checkedSnapshot(snapshot, { snapshotSha256: digest(snapshot) }, locks), /not queried/);
  assert.throws(() => checkedSnapshot(snapshot+' ', provenance, locks), /provenance/);
});

test('a missing or malformed registry response cannot be treated as no findings', () => {
  for (const value of [null, [], 'ok', undefined]) {
    const bytes = JSON.stringify({ responses: { site: value } });
    assert.throws(() => checkedSnapshot(bytes, { ...provenance, snapshotSha256: digest(bytes) }, locks), /Missing advisory/);
  }
});

test('persisted responses round-trip with exact dependency provenance', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vela-advisory-'));
  try {
    const file = path.join(directory, 'snapshot.json');
    saveSnapshot(file, JSON.parse(snapshot), locks);
    const bytes = fs.readFileSync(file);
    const binding = JSON.parse(fs.readFileSync(file+'.inputs.json'));
    assert.deepEqual(checkedSnapshot(bytes, binding, locks).responses.site, {});
    assert.throws(() => checkedSnapshot(bytes, provenance, locks), /provenance/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
