"""Active account packaging is distinct from the approved inactive preview."""
import json
import pytest

from tools.build_noah_testnet import active_source, build, verify
from tools.build_noah_preview import build as preview_build
from test_noah_preview_release import fixture_dashboard


def fixture_account(tmp_path):
    dashboard=fixture_dashboard(tmp_path)
    shared=dashboard.parent/'src';shared.mkdir();(shared/'wallet.js').write_text('/* shared wallet fixture */')
    source=active_source(dashboard)
    output=dashboard/'dist-testnet';(output/'assets').mkdir(parents=True)
    (output/'source-manifest.json').write_text(json.dumps(source))
    (output/'vela.html').write_text(f'<meta name="vela-source-id" content="{source["sourceId"]}"><meta name="robots" content="noindex, nofollow"><script type="module" src="./assets/account.js"></script>')
    (output/'assets/account.js').write_text('/* testnet account fixture */')
    return dashboard


def test_account_package_has_distinct_identity_same_origin_api_and_no_sample_entry(tmp_path):
    dashboard=fixture_account(tmp_path);out=tmp_path/'account';second=tmp_path/'again'
    inactive=preview_build(dashboard,tmp_path/'preview')
    manifest=build(dashboard,out);again=build(dashboard,second)
    assert manifest['archiveSha256']==again['archiveSha256']
    assert manifest['release']['sourceId']!=inactive['release']['sourceId']
    assert manifest['release']['backendGateRequired'] and manifest['release']['walletActionsBuilt']
    assert verify(out)['verified'] and not verify(out)['published']
    assert not (out/'website/preview.html').exists() and not (out/'website/index.html').exists()
    nginx=(out/'nginx-testnet.conf.pending').read_text()
    assert 'location = /app/' in nginx and "connect-src 'self'" in nginx
    assert 'location = / {' not in nginx and 'proxy_pass' not in nginx
    with pytest.raises(FileExistsError):build(dashboard,out)


@pytest.mark.parametrize('change',['shared_wallet','source','profile','missing_asset','extra','symlink'])
def test_account_release_rejects_stale_or_unsafe_inputs(tmp_path,change):
    dashboard=fixture_account(tmp_path);out=tmp_path/'refused'
    if change=='shared_wallet':(dashboard.parent/'src/wallet.js').write_text('changed wallet')
    if change=='source':(dashboard/'src/new.ts').write_text('changed source')
    if change=='profile':
        p=dashboard/'dist-testnet/source-manifest.json';s=json.loads(p.read_text());s['profile']='PREVIEW';p.write_text(json.dumps(s))
    if change=='missing_asset':(dashboard/'dist-testnet/assets/account.js').unlink()
    if change=='extra':(dashboard/'dist-testnet/runtime.env').write_text('fixture')
    if change=='symlink':
        p=dashboard/'dist-testnet/assets/account.js';p.unlink();p.symlink_to(dashboard/'package.json')
    with pytest.raises(ValueError):build(dashboard,out)
    assert not out.exists()


@pytest.mark.parametrize('path',['website/assets/account.js','website/source-manifest.json','nginx-testnet.conf.pending','website.tar.gz'])
def test_account_release_rejects_tampered_artifact(tmp_path,path):
    dashboard=fixture_account(tmp_path);out=tmp_path/'account';build(dashboard,out)
    (out/path).write_bytes(b'changed')
    with pytest.raises(ValueError):verify(out)


def test_app_upgrade_preserves_homepage_api_and_entry_routing():
    from tools.deploy_noah_testnet import render, block, REDIRECTS
    existing = 'server {\n    location /api { proxy_pass http://upstream; }\n'
    existing += block('old-app\n'+REDIRECTS)
    existing += '    location = /trading { return 302 /app/#/trading; }\n}\n'
    updated = render(existing, 'new-app\n', previous_fragment='old-app\n')
    assert updated == existing.replace(block('old-app\n'+REDIRECTS), block('new-app\n'+REDIRECTS))
    with pytest.raises(RuntimeError, match='differs'):
        render(existing, 'new-app\n', previous_fragment='different-app\n')
    with pytest.raises(RuntimeError, match='differs'):
        render(existing+block('other\n'), 'new-app\n', previous_fragment='old-app\n')
