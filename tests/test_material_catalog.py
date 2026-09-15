from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import database as db
from app.main import app
from app.models.material import MaterialRegistration
from app.services import material_catalog_service as catalog
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


def source_folder(tmp_path, count=10):
    folder = tmp_path/'local-originals'
    folder.mkdir()
    for i in range(count):
        (folder/f'EP{i+1:03d}.mp4').write_bytes(f'private-original-{i}'.encode())
    return folder


def registration(scan, **overrides):
    data = dict(scan_id=scan['id'], manifest_sha256=scan['manifest_sha256'],
                source_keys=[r['source_key'] for r in scan['manifest']['entries']], request_key=uuid4(), confirmed=True)
    data.update(overrides)
    return MaterialRegistration(**data)


def test_scan_register_is_explicit_idempotent_and_creates_no_tasks(human_db, tmp_path):
    folder = source_folder(tmp_path)
    originals = {p.name:p.read_bytes() for p in folder.iterdir()}
    (folder/'subdirectory').mkdir()
    (folder/'subdirectory/hidden.mp4').write_bytes(b'not scanned')
    (folder/'readme.txt').write_text('not media')
    (folder/'empty.mp4').touch()
    scan = catalog.scan_directory(str(folder))
    assert len(scan['manifest']['entries']) == 10 and len(scan['manifest']['excluded']) == 3
    assert catalog.list_materials()['total'] == 0
    with pytest.raises(catalog.MaterialError, match='确认'):
        catalog.register_materials(registration(scan, confirmed=False))
    payload = registration(scan)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _:catalog.register_materials(payload), range(6)))
    assert len({r['id'] for r in results}) == 1
    assert sum(not r['reused'] for r in results) == 1
    second = catalog.register_materials(registration(catalog.scan_directory(str(folder))))
    assert set(second['material_ids']) == set(results[0]['material_ids'])
    assert catalog.list_materials()['total'] == 10
    assert len(catalog.list_materials(3,3)['materials']) == 3
    for name, content in originals.items():
        assert (folder/name).read_bytes() == content
    with db.get_connection() as c:
        for table in ('tasks','workflow_jobs','publish_jobs','content_policy_events'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM material_registrations').fetchone()[0] == 2
    with pytest.raises(catalog.MaterialError, match='请求编号'):
        catalog.register_materials(registration(scan, request_key=payload.request_key, source_keys=payload.source_keys[:1]))


def test_changed_source_rolls_back_whole_selection_then_new_version_is_distinct(human_db, tmp_path):
    folder = source_folder(tmp_path,2)
    scan = catalog.scan_directory(str(folder))
    payload = registration(scan)
    # Change whichever entry sorts last by identity, so a prior INSERT must roll back.
    sources = {r['source_key']:r['source'] for r in scan['manifest']['entries']}
    changed = Path(sources[sorted(sources)[-1]]['path'])
    changed.write_bytes(b'changed-original')
    with pytest.raises(catalog.MaterialError, match='已变化'):
        catalog.register_materials(payload)
    assert catalog.list_materials()['total'] == 0
    fresh = catalog.scan_directory(str(folder))
    first = catalog.register_materials(registration(fresh))
    changed.write_bytes(b'changed-another-version')
    catalog.register_materials(registration(catalog.scan_directory(str(folder))))
    assert catalog.list_materials()['total'] == 3
    with pytest.raises(catalog.MaterialError, match='已变化'):
        catalog.validate_source(next(catalog.get_material(mid)['source'] for mid in first['material_ids'] if catalog.get_material(mid)['source_path'] == str(changed)))


@pytest.mark.parametrize('raw', ['relative/folder', '../escape', '//server/share', r'\\server\share', r'\\?\C:\Windows'])
def test_scan_rejects_nonlocal_and_traversal(human_db, raw):
    with pytest.raises(catalog.MaterialError):
        catalog.scan_directory(raw)


def test_links_caps_unknown_selection_expiry_and_version_evidence(human_db, tmp_path, monkeypatch):
    folder = source_folder(tmp_path,2)
    link = folder/'linked.mp4'
    try:
        link.symlink_to(folder/'EP001.mp4')
    except OSError:
        pass  # Real reparse behavior is tested on hosts with link permission below.
    scan = catalog.scan_directory(str(folder))
    assert len(scan['manifest']['entries']) == 2
    with monkeypatch.context() as scope:
        scope.setattr(catalog, 'MAX_ENTRIES', 1)
        with pytest.raises(catalog.MaterialError, match='超过'):
            catalog.scan_directory(str(folder))
    with monkeypatch.context() as scope:
        scope.setattr(catalog, 'MAX_VIDEOS', 1)
        with pytest.raises(catalog.MaterialError, match='最多'):
            catalog.scan_directory(str(folder))
    with pytest.raises(catalog.MaterialError, match='不属于'):
        catalog.register_materials(registration(scan, source_keys=['a'*64]))
    with pytest.raises(catalog.MaterialError, match='校验失败'):
        catalog.register_materials(registration(scan, manifest_sha256='b'*64))
    # Expired immutable preview fixture; no need to alter production constraints.
    expired_id = uuid4().hex
    with db.get_connection() as c:
        c.execute('INSERT INTO material_scans SELECT ?,directory,manifest_json,manifest_sha256,created_at,? FROM material_scans WHERE id=?',
                  (expired_id,(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),scan['id']))
        c.commit()
    with pytest.raises(catalog.MaterialError, match='30 分钟'):
        catalog.register_materials(registration(scan, scan_id=expired_id))
    result = catalog.register_materials(registration(scan))
    item = catalog.get_material(result['material_ids'][0])
    assert item['identity_sha256'] == catalog.digest(item['source'])
    assert 'content_sha256' not in item  # Metadata identity is not a video content digest.
    with db.get_connection() as c:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute("UPDATE source_materials SET source_path='other' WHERE id=?", (item['id'],))


def test_replaced_directory_and_file_links_are_not_authorized_by_preview(human_db, tmp_path):
    folder = source_folder(tmp_path,1)
    scan = catalog.scan_directory(str(folder))
    renamed = tmp_path/'preserved-originals'
    folder.rename(renamed)
    folder.mkdir()
    (folder/'EP001.mp4').write_bytes(b'replacement')
    with pytest.raises(catalog.MaterialError, match='目录已替换'):
        catalog.register_materials(registration(scan))
    assert catalog.list_materials()['total'] == 0
    assert (renamed/'EP001.mp4').read_bytes() == b'private-original-0'


def test_material_api_uses_existing_local_admin_boundary(human_db, tmp_path):
    folder = source_folder(tmp_path,1)
    client = TestClient(app)
    denied = client.post('/api/materials/scan', json={'directory':str(folder)}, headers={'Origin':'https://untrusted.invalid'})
    assert denied.status_code == 403
    scan_response = client.post('/api/materials/scan', json={'directory':str(folder)})
    assert scan_response.status_code == 200
    payload = registration(scan_response.json()).model_dump(mode='json')
    registered = client.post('/api/materials/register', json=payload)
    assert registered.status_code == 200
    assert client.get('/api/materials').json()['total'] == 1
    assert client.get('/api/materials?limit=1000').status_code == 422
    assert client.get('/api/materials/'+registered.json()['material_ids'][0]).status_code == 200
    assert client.get('/api/materials/missing').status_code == 404
    assert 'private-original' not in json.dumps(scan_response.json())
