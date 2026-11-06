"""Publish the approved homepage colours and browser-local theme preference."""
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.deploy_noah_testnet import SITE, atomic, fetch, run, sha


def publish():
    before = SITE.read_bytes()
    text = before.decode()
    matches = re.findall(r'root (/var/www/defivela-home/simple-doge-[a-f0-9]{12});', text)
    if len(matches) != 1:
        raise RuntimeError('Homepage route changed; preserve existing routing')
    old = Path(matches[0]) / 'index.html'
    original = old.read_bytes()
    if sha(original) != '41a370aaa3e08fd98afd8090d2e627f9755d578af493d29ef2a5a80a5941b291':
        raise RuntimeError('Homepage differs from the approved design baseline')
    data = (ROOT / 'design/vela-home.html').read_bytes()
    if data.split(b'<body>', 1)[1] != original.split(b'<body>', 1)[1]:
        raise RuntimeError('Homepage content or structure changed')
    scripts = re.findall(rb'<script[^>]*>(.*?)</script>', data, re.S)
    if len(scripts) != 1 or scripts[0] != (ROOT / 'design/home-theme.js').read_bytes().strip():
        raise RuntimeError('Unexpected homepage script')
    script_hash = base64.b64encode(hashlib.sha256(scripts[0]).digest()).decode()
    start = text.index('    # BEGIN VELA SIMPLE HOME\n')
    end = text.index('    # END VELA SIMPLE HOME\n', start)
    fragment = text[start:end]
    if fragment.count("script-src 'none'") != 1:
        raise RuntimeError('Unexpected homepage script policy')
    directory = Path('/var/www/defivela-home') / ('light-' + sha(data)[:12])
    replacement = fragment.replace(str(old.parent), str(directory), 1).replace("script-src 'none'", "script-src 'sha256-" + script_hash + "'", 1)
    proposed = (text[:start] + replacement + text[end:]).encode()
    app_hash = sha(fetch('/app/')[0])
    preview_hash = sha(fetch('/preview/light/')[0])
    pid = subprocess.check_output(['systemctl', 'show', 'vela-multichain-candidate.service', '-p', 'MainPID', '--value'], text=True).strip()
    directory.mkdir(mode=0o755, exist_ok=True)
    if directory.is_symlink() or (directory / 'index.html').is_symlink():
        raise RuntimeError('Redirected publication target')
    if (directory / 'index.html').exists() and (directory / 'index.html').read_bytes() != data:
        raise RuntimeError('Immutable homepage artifact changed')
    atomic(directory / 'index.html', data, 0o644)
    mode = SITE.stat().st_mode & 0o777
    changed = False
    try:
        if SITE.read_bytes() != before:
            raise RuntimeError('Concurrent routing update')
        atomic(SITE, proposed, mode)
        changed = True
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        deadline = time.monotonic() + 15
        while True:
            served, headers = fetch('/')
            if sha(served) == sha(data):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Public homepage did not update')
            time.sleep(.25)
        if "script-src 'sha256-" + script_hash + "'" not in headers.get('Content-Security-Policy', ''):
            raise RuntimeError('Theme script policy mismatch')
        if sha(fetch('/app/')[0]) != app_hash or sha(fetch('/preview/light/')[0]) != preview_hash:
            raise RuntimeError('App or approved preview changed during homepage publication')
        after_pid = subprocess.check_output(['systemctl', 'show', 'vela-multichain-candidate.service', '-p', 'MainPID', '--value'], text=True).strip()
        if after_pid != pid:
            raise RuntimeError('Trading service PID changed')
    except BaseException:
        if changed and SITE.read_bytes() == proposed:
            atomic(SITE, before, mode)
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx')
        raise
    result = dict(homepageDeployed=True, url='https://defivela.xyz/', homepageSha256=sha(data),
                  homepageBodyUnchanged=True, appUnchanged=True, previewUnchanged=True,
                  backendPid=pid, backendRestarted=False, walletTransactionsSent=False,
                  liveProtectionVerified=False, themeScriptSha256=script_hash)
    (ROOT / 'verification/noah/light-theme-home-deployment.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    publish()
