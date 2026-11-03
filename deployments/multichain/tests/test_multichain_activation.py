"""Verification must never prepare or activate a process; no real sockets here."""
import json
from unittest.mock import Mock

import pytest
from tools import multichain_preview as preview


@pytest.mark.parametrize('blocked',[False,True])
def test_verification_preserves_activation_record_and_never_prepares(tmp_path,monkeypatch,blocked):
    previous=tmp_path/'verification.json';previous.write_text('previous activation evidence')
    monkeypatch.setattr(preview,'RUNTIME',tmp_path)
    monkeypatch.setattr(preview.sys,'argv',['multichain_preview.py','--verify'])
    prepare=Mock(side_effect=AssertionError('Verification must not prepare or activate'))
    monkeypatch.setattr(preview,'prepare',prepare)
    verify=Mock(side_effect=PermissionError('Socket access denied')) if blocked else Mock(return_value={'http':'PASS','liveProtectionVerified':False})
    monkeypatch.setattr(preview,'verify',verify)
    if blocked:
        with pytest.raises(PermissionError):preview.main()
    else:preview.main()
    prepare.assert_not_called()
    assert previous.read_text()=='previous activation evidence'
    result=json.loads((tmp_path/'verification-current.json').read_text())
    assert result['status']==('UNVERIFIED' if blocked else 'HTTP VERIFIED')
    assert result['liveProtectionVerified'] is False
