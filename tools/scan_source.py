"""Scan the staged Git tree; optionally compare a secret supplied without echo."""
import argparse
import getpass
import json
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--check-secret", action="store_true")
args = parser.parse_args()
secret = getpass.getpass("Secret to check against staged source (hidden): ").strip().encode() if args.check_secret else None
patterns = {
    "private_key_block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})"),
    "literal_evm_private_key": re.compile(rb"(?:private[_-]?key|operator[_-]?key)\s*[:=]\s*['\"](?:0x)?[0-9a-f]{64}['\"]", re.I),
}
findings = []
paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
count = 0
for raw_path in paths:
    if not raw_path:
        continue
    path = raw_path.decode()
    data = subprocess.check_output(["git", "show", ":" + path])
    count += 1
    for label, pattern in patterns.items():
        if pattern.search(data):
            findings.append({"path": path, "kind": label})
    if secret and (secret in data or secret.lower() in data.lower()):
        findings.append({"path": path, "kind": "supplied_secret"})
    if re.search(r"(?:^|/)(?:\.env|secrets)(?:$|/)", path):
        findings.append({"path": path, "kind": "secret_path"})
print(json.dumps({"staged_files": count, "findings": findings,
    "supplied_secret_checked": bool(secret), "scope": "Staged source only; patterns are not a security audit"}, indent=2))
raise SystemExit(bool(findings))
