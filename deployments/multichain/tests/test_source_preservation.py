import hashlib
import json

import pytest

from tools.verify_source import verify


@pytest.mark.parametrize('mutation', [None, 'local', 'original'])
def test_recorded_adaptation_preserves_both_provenances_and_detects_new_edits(tmp_path, mutation):
    local, original = tmp_path/'local', tmp_path/'original'
    for root in (local, original):
        (root/'backend').mkdir(parents=True)
    (local/'config').mkdir()
    (local/'snapshot').mkdir()
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    (original/'backend/app.py').write_text('original')
    (local/'backend/app.py').write_text('existing adaptation')
    (local/'config/reuse-manifest.json').write_text(json.dumps({'files': {'backend/app.py': sha('original')}}))
    (local/'snapshot/reuse-reconciliation-20261103.json').write_text(json.dumps({'adaptations': {'backend/app.py': {'sha256': sha('existing adaptation')}}}))
    if mutation:
        ((local if mutation == 'local' else original)/'backend/app.py').write_text('unexpected edit')
    report = verify(local, original)
    assert report['allMatched'] is (mutation is None)
    assert report['files'][0]['copiedSourceUnchanged'] is False
