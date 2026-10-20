export type SupportTurn = { role: "user" | "assistant"; content: string };
export const MAX_QUESTION = 800;

// Shared on client and server; a second check on the server protects direct callers.
// Labelled credentials, standard key prefixes, long opaque tokens and seed phrases.
export function containsCredentials(text: string): boolean {
  return /\b(?:api[ _-]?(?:key|secret)|secret[ _-]?key|password|passphrase|access[ _-]?token|authorization)\s*(?:[:=]\s*|["']\s*:\s*)["']?[^\s"']{6,}/i.test(text)
    || /\b(?:sk-|gsk_|ghp_|gho_|eyJ)[a-zA-Z0-9_-]{12,}/.test(text)
    || /\b(?=[a-zA-Z0-9_-]{32,}\b)(?=[a-zA-Z0-9_-]*\d)(?=[a-zA-Z0-9_-]*[a-zA-Z])[a-zA-Z0-9_-]{32,}\b/.test(text)
    || /\b(?:seed|recovery|mnemonic)\s+(?:phrase|words)\s*[:=]/i.test(text);
}
