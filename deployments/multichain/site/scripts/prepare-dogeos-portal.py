"""Apply three reviewed copy changes to the exact deployed portal bundle.

The homepage entrypoint and every shared asset remain untouched. This avoids
publishing unrelated prepared frontend changes alongside a small copy update.
"""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('portal-built'))
args = parser.parse_args()
script = args.source/'assets/app-ZKNVLTWK.js'
original = script.read_bytes()
assert hashlib.sha256(original).hexdigest() == '6c1694ccc0980d4785d15d1fcfaec9d849a96c0609ce6956da85fefe2f76b931', 'Reconcile newer deployed portal first'
copy = original.decode()
replacements = [
    (r'Chiky\u016B Testnet \xB7 Demo', r'Featured \xB7 Chiky\u016B Testnet \xB7 Demo', 2),
    ('createElement("h2",null,"Testnet vault")', 'createElement("h2",null,"DogeOS testnet vault")', 1),
]
for old, new, count in replacements:
    assert copy.count(old) == count
    copy = copy.replace(old, new)
data = copy.encode()
name = 'app-dogeos-'+hashlib.sha256(data).hexdigest()[:12]+'.js'
html = (args.source/'index.html').read_text()
assert html.count('/ui-assets/simple-tabs-20261101T085948Z/app-ZKNVLTWK.js') == 1
html = html.replace('/ui-assets/simple-tabs-20261101T085948Z/app-ZKNVLTWK.js', '/portal-assets/dogeos-20261103/'+name)
args.output.mkdir(parents=True, exist_ok=True)
(args.output/name).write_bytes(data)
(args.output/'index.html').write_text(html)
manifest = dict(homepageModified=False, layoutModified=False, copyChanges=3,
    sharedAssets='/ui-assets/simple-tabs-20261101T085948Z/',
    files={name:hashlib.sha256(data).hexdigest(), 'index.html':hashlib.sha256(html.encode()).hexdigest()})
(args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps(manifest))
