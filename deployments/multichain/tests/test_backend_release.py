import json
import subprocess

import pytest

from tools.build_backend_release import build, verify


def command(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def commit(root):
    command(root, 'add', '.')
    command(root, '-c', 'user.name=Release fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    return command(root, 'rev-parse', 'HEAD').decode().strip()


def repository(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    command(root, 'init', '-q')
    (root/'backend').mkdir()
    (root/'backend/app.py').write_text('old\n')
    return root, commit(root)


def test_release_contains_rollback_and_refuses_newer_target_or_corruption(tmp_path):
    root, base = repository(tmp_path)
    target = tmp_path/'active'
    (target/'backend').mkdir(parents=True)
    (target/'backend/app.py').write_text('old\n')
    (root/'backend/app.py').write_text('new\n')
    (root/'backend/addition.py').write_text('addition\n')
    commit(root)
    output = tmp_path/'release'
    build(root, output, base)
    assert verify(output, target)['targetMatched']
    (target/'backend/app.py').write_text('newer unpublished work\n')
    with pytest.raises(ValueError, match='newer'):
        verify(output, target)
    (output/'backend-overlay.tar.gz').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        verify(output)


@pytest.mark.parametrize('name', ['site/app.jsx', '.runtime/customer.sqlite3', '.env', 'backend/secret.env'])
def test_release_refuses_ui_runtime_and_secret_paths_before_creating_artifacts(tmp_path, name):
    root, base = repository(tmp_path)
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture')
    commit(root)
    output = tmp_path/'release'
    with pytest.raises(ValueError, match='backend'):
        build(root, output, base)
    assert not output.exists()


def test_empty_manifest_cannot_be_reported_as_a_verified_release(tmp_path):
    (tmp_path/'manifest.json').write_text(json.dumps(dict(archives={}, files=[])))
    with pytest.raises(ValueError, match='Incomplete'):
        verify(tmp_path)
