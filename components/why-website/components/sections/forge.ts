// ─────────────────────────────────────────────────────────────────────────────
// Client half of THE FORGE. Solves the proof-of-work challenge the server
// signed (see app/api/waitlist/pow.ts for what this is buying).
//
// WHY A HAND-ROLLED SHA-256 RATHER THAN crypto.subtle. `crypto.subtle.digest`
// is async: scanning a 400k window through it means 400k promises, and the
// microtask overhead dwarfs the hashing entirely. A synchronous
// implementation does the same work in a fraction of the time, and the input
// here is always short and fixed-shape, which removes most of the usual
// footguns — see the single-block note below.
// ─────────────────────────────────────────────────────────────────────────────

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const W = new Uint32Array(64);
const block = new Uint8Array(64);
const view = new DataView(block.buffer);

/**
 * First 32 bits of SHA-256 over an ASCII string of fewer than 56 bytes.
 *
 * SINGLE BLOCK ONLY, on purpose. The only thing ever hashed here is
 * `${salt}.${i}` — 32 hex characters, a dot, and a decimal index, so 39 bytes
 * at the very outside. Under 56 bytes the padding and the length word
 * both fit in one block, which removes the multi-block loop entirely. Callers
 * outside that shape would get a wrong answer, so it is asserted, not assumed.
 */
export function sha256FirstWord(ascii: string): number {
  const len = ascii.length;
  if (len >= 56) throw new Error("forge: input must be under 56 bytes");

  block.fill(0);
  for (let i = 0; i < len; i++) block[i] = ascii.charCodeAt(i) & 0xff;
  block[len] = 0x80;
  view.setUint32(60, len << 3, false); // bit length; len < 56 so the high word is 0

  for (let i = 0; i < 16; i++) W[i] = view.getUint32(i << 2, false);
  for (let i = 16; i < 64; i++) {
    const a = W[i - 15], b = W[i - 2];
    const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
    const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
    W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
  }

  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;

  for (let i = 0; i < 64; i++) {
    const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
    const ch = (e & f) ^ (~e & g);
    const t1 = (h + S1 + ch + K[i] + W[i]) | 0;
    const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
    const maj = (a & b) ^ (a & c) ^ (b & c);
    const t2 = (S0 + maj) | 0;
    h = g; g = f; f = e; e = (d + t1) | 0;
    d = c; c = b; b = a; a = (t1 + t2) | 0;
  }
  return ((h0 + a) | 0) >>> 0;
}

export type Challenge = { s: string; r: number; t: string; e: number; sig: string };

/**
 * Scan [0, r) for the integer whose digest prefix matches the target.
 *
 * BOUNDED, which is the whole reason the challenge has this shape — see
 * app/api/waitlist/pow.ts. Progress here is literally position in the scan, so
 * the bar the visitor watches is the work itself rather than a timer dressed
 * up as one.
 *
 * Runs in slices with a yield between them so the forge keeps painting: a
 * frozen bar during the one moment someone is holding the button reads as a
 * broken page, which is the opposite of the point.
 */
export async function solve(
  ch: Challenge,
  onProgress: (tried: number) => void,
  aborted: () => boolean,
): Promise<string | null> {
  const SLICE = 4000;
  const target = parseInt(ch.t, 16) >>> 0;
  for (let i = 0; i < ch.r; ) {
    if (aborted()) return null;
    const end = Math.min(i + SLICE, ch.r);
    for (; i < end; i++) {
      if (sha256FirstWord(`${ch.s}.${i}`) === target) {
        onProgress(i);
        return String(i);
      }
    }
    onProgress(i);
    await new Promise((r) => setTimeout(r, 0));
  }
  // Unreachable for a challenge this server issued: k was drawn from exactly
  // this window. Reached only if the response was tampered with in flight.
  return null;
}
