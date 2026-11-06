import fs from 'node:fs';
import { createHash } from 'node:crypto';

export const digest = value => createHash('sha256').update(value).digest('hex');

export function checkedSnapshot(snapshotBytes, provenance, lockfiles) {
  if (provenance.snapshotSha256 !== digest(snapshotBytes)) {
    throw new Error('Advisory snapshot does not match its recorded provenance; refresh required');
  }
  const snapshot = JSON.parse(snapshotBytes);
  for (const [directory, bytes] of Object.entries(lockfiles)) {
    if (provenance.lockfiles?.[directory] !== digest(bytes)) {
      throw new Error(`Dependency lock changed or was not queried: ${directory}; refresh required`);
    }
    const response = snapshot.responses?.[directory];
    if (!response || typeof response !== 'object' || Array.isArray(response)) {
      throw new Error(`Missing advisory response: ${directory}`);
    }
  }
  return snapshot;
}

export function saveSnapshot(filename, snapshot, lockfiles) {
  const bytes = JSON.stringify(snapshot, null, 2)+'\n';
  const provenance = {
    snapshotSha256: digest(bytes),
    lockfiles: Object.fromEntries(Object.entries(lockfiles).map(([name, data]) => [name, digest(data)])),
  };
  // A partial write fails provenance validation; it cannot produce a green scan.
  fs.writeFileSync(filename+'.tmp', bytes);
  fs.renameSync(filename+'.tmp', filename);
  fs.writeFileSync(filename+'.inputs.json.tmp', JSON.stringify(provenance, null, 2)+'\n');
  fs.renameSync(filename+'.inputs.json.tmp', filename+'.inputs.json');
}
