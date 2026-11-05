"""Static preview packaging integrity and isolation. No server or browser launch."""
import json
import tarfile
import pytest
from tools.build_noah_preview import build, verify, source_state, sha, INPUTS, GATES


def fixture_dashboard(tmp_path):
    dashboard=tmp_path/'dashboard'
    (dashboard/'src').mkdir(parents=True)
    (dashboard/'public').mkdir()
    for name in INPUTS:
        if name in ('src','public'):continue
        (dashboard/name).write_text('{}\n')
    gate=dashboard/GATES;gate.parent.mkdir(parents=True)
    gate.write_text('export const INTEGRATION = { serviceEnabled: false as const, writesEnabled: false as const };\n')
    compile_fixture(dashboard)
    return dashboard


def compile_fixture(dashboard):
    manifest=source_state(dashboard)
    dist=dashboard/'dist';(dist/'assets').mkdir(parents=True,exist_ok=True)
    (dist/'source-manifest.json').write_text(json.dumps(manifest))
    html=f'''<!doctype html><head><meta name="vela-source-id" content="{manifest['sourceId']}"><meta name="robots" content="noindex, nofollow"><script type="module" src="./assets/ui.js"></script><link href="./assets/ui.css" rel="stylesheet"></head><body>preview</body>'''
    for name in ('preview.html','vela.html','index.html'):(dist/name).write_text(html)
    (dist/'assets/ui.js').write_text('/* synthetic asset */')
    (dist/'assets/ui.css').write_text('body { color: white }')
    (dist/'robots.txt').write_text('User-agent: *\nAllow: /\n')


def test_package_separates_preview_from_published_app_and_has_reproducible_archive(tmp_path):
    dashboard=fixture_dashboard(tmp_path);one=tmp_path/'one';two=tmp_path/'two'
    manifest=build(dashboard,one);again=build(dashboard,two)
    assert manifest['archiveSha256']==again['archiveSha256']
    assert verify(one)['verified'] and not verify(one)['published']
    assert not (one/'website/index.html').exists()
    assert not (one/'website/robots.txt').exists()
    config=(one/'nginx-preview.conf.pending').read_text()
    assert 'location = /vela-preview/' in config and "connect-src 'none'" in config
    assert 'location = / {' not in config and 'proxy_pass' not in config and '/api/' not in config
    assert not (dashboard/'dist/review-manifest.json').exists() # Packaging never rewrites the build.
    with pytest.raises(FileExistsError):build(dashboard,one)


@pytest.mark.parametrize('change',['source','gate','missing_asset','absolute_asset','foreign_asset','unexpected_output','symlink'])
def test_rejects_stale_unsafe_or_active_input_before_output_exists(tmp_path,change):
    dashboard=fixture_dashboard(tmp_path);destination=tmp_path/'blocked'
    if change=='source':(dashboard/'src/later.ts').write_text('changed')
    if change=='gate':
        p=dashboard/GATES;p.write_text(p.read_text().replace('writesEnabled: false','writesEnabled: true'));compile_fixture(dashboard)
    if change=='missing_asset':(dashboard/'dist/assets/ui.js').unlink()
    if change=='absolute_asset':
        p=dashboard/'dist/preview.html';p.write_text(p.read_text().replace('./assets/ui.js','/assets/ui.js'))
    if change=='foreign_asset':
        p=dashboard/'dist/preview.html';p.write_text(p.read_text().replace('./assets/ui.js','https://fonts.googleapis.com.example.invalid/ui.js'))
    if change=='unexpected_output':(dashboard/'dist/secrets.env').write_text('synthetic')
    if change=='symlink':
        p=dashboard/'dist/assets/ui.js';p.unlink();p.symlink_to(dashboard/'package.json')
    with pytest.raises(ValueError):build(dashboard,destination)
    assert not destination.exists()


@pytest.mark.parametrize('target',['website/assets/ui.js','nginx-preview.conf.pending','website.tar.gz'])
def test_modified_package_is_not_verified(tmp_path,target):
    dashboard=fixture_dashboard(tmp_path);out=tmp_path/'release';build(dashboard,out)
    (out/target).write_bytes(b'changed')
    with pytest.raises(ValueError,match='mismatch'):verify(out)


def test_archive_symlink_is_rejected_even_if_outer_checksum_is_updated(tmp_path):
    dashboard=fixture_dashboard(tmp_path);out=tmp_path/'release';build(dashboard,out)
    with tarfile.open(out/'website.tar.gz','w:gz') as archive:
        member=tarfile.TarInfo('assets/ui.js');member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';archive.addfile(member)
    p=out/'package.json';m=json.loads(p.read_text());m['archiveSha256']=sha((out/'website.tar.gz').read_bytes());p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='Unsafe'):verify(out)


def test_source_manifest_cannot_escape_dashboard(tmp_path):
    dashboard=fixture_dashboard(tmp_path);p=dashboard/'dist/source-manifest.json';m=json.loads(p.read_text())
    m['files']['../outside']='0'*64;p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='stale'):build(dashboard,tmp_path/'blocked')
