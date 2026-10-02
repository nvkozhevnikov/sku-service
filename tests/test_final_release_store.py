import hashlib
import json
from pathlib import Path
import pytest
from universal_supplier.control_plane.final_release_store import FinalReleaseStore

def fixture(tmp_path):
    accepted=tmp_path/'accepted';accepted.mkdir()
    body=json.dumps({'rows':[{'source':'intervesp','external_id':'one','classification':'REVIEW'}]}).encode()
    (accepted/'MATCHING_ACCEPTED.json').write_bytes(body)
    freeze=json.dumps({'input_sha256':{'MATCHING_ACCEPTED.json':hashlib.sha256(body).hexdigest()},'summary':{'Review':1}}).encode()
    (tmp_path/'FREEZE_MANIFEST.json').write_bytes(freeze)
    (tmp_path/'TEST.csv').write_bytes(b'literal')
    manifest={n:{'sha256':hashlib.sha256((tmp_path/n).read_bytes()).hexdigest()} for n in ('FREEZE_MANIFEST.json','TEST.csv')}
    m=json.dumps(manifest).encode();(tmp_path/'SHA256_MANIFEST.json').write_bytes(m)
    return hashlib.sha256(m).hexdigest()

def test_read_only_scoped_page_and_download(tmp_path):
    sha=fixture(tmp_path);s=FinalReleaseStore(tmp_path,sha)
    assert s.page('REVIEW')['total']==1
    assert s.file('TEST.csv')==b'literal'
    with pytest.raises(ValueError):s.file('../TEST.csv')

def test_changed_manifest_and_payload_fail_closed(tmp_path):
    sha=fixture(tmp_path)
    with pytest.raises(ValueError):FinalReleaseStore(tmp_path,'0'*64)
    (tmp_path/'accepted/MATCHING_ACCEPTED.json').write_text('{}')
    with pytest.raises(ValueError):FinalReleaseStore(tmp_path,sha)

def test_changed_download_fails_closed(tmp_path):
    sha=fixture(tmp_path);s=FinalReleaseStore(tmp_path,sha)
    (tmp_path/'TEST.csv').write_bytes(b'changed')
    with pytest.raises(ValueError):s.file('TEST.csv')

def test_default_scheduler_and_server_importer_off():
    root=Path(__file__).resolve().parents[1]
    assert 'get("SCHEDULER_ENABLED", "NO")' in (root/'universal_supplier/control_plane/scheduler_main.py').read_text()
    compose=(root/'docker-compose.server.yml').read_text()
    assert 'ESOL_WRITE_ENABLE: "NO"' in compose
    assert 'STERBRUST_WRITE_ENABLE: "NO"' in compose
    assert 'profiles: [worker]' in compose and 'profiles: [scheduler]' in compose

def test_final_release_http_is_get_only_and_sha_guarded(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from universal_supplier.control_plane.web import create_app
    from universal_supplier.control_plane.store import UnavailableControlPlaneStore
    sha=fixture(tmp_path)
    monkeypatch.setenv('FINAL_RC_ARTIFACTS_DIR',str(tmp_path))
    monkeypatch.setenv('FINAL_RC_MANIFEST_SHA256',sha)
    app=create_app(store=UnavailableControlPlaneStore('offline'),auth_required=False)
    with TestClient(app) as client:
        assert client.get('/rc-final').status_code==200
        assert client.get('/rc-final/files/TEST.csv').content==b'literal'
        assert client.post('/rc-final').status_code==405
        monkeypatch.setenv('FINAL_RC_MANIFEST_SHA256','0'*64)
        assert client.get('/rc-final').status_code==503
