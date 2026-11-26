"""Hash-pinned target portability; local pins remain unchanged by default."""
import hashlib
import json
import pytest
from universal_supplier.deployment_contract import server_contract


def contract(tmp_path,monkeypatch,**changes):
    value=dict(format="sterbrust-deployment/v1",host="127.0.0.1",port=55461,
               database="rc_verify_vi_20261019",version="17.11",
               system_identifier="1234567890123456789",pgdata=str(tmp_path.resolve()))
    value.update(changes)
    path=tmp_path/"contract.json";path.write_text(json.dumps(value),encoding="utf-8")
    monkeypatch.setenv("STERBRUST_DEPLOYMENT_CONTRACT",str(path))
    monkeypatch.setenv("STERBRUST_DEPLOYMENT_CONTRACT_SHA256",hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setenv("STERBRUST_DEPLOYMENT_CONFIRMED","YES")
    return path


def test_local_mode_does_not_gain_a_new_target(monkeypatch):
    monkeypatch.delenv("STERBRUST_DEPLOYMENT_CONTRACT",raising=False)
    monkeypatch.delenv("STERBRUST_DEPLOYMENT_CONTRACT_SHA256",raising=False)
    assert server_contract() is None


def test_hash_and_explicit_confirmation_required(tmp_path,monkeypatch):
    path=contract(tmp_path,monkeypatch)
    assert server_contract()["system_identifier"]=="1234567890123456789"
    path.write_text("{}",encoding="utf-8")
    with pytest.raises(PermissionError):server_contract()


def test_missing_confirmation(tmp_path,monkeypatch):
    contract(tmp_path,monkeypatch)
    monkeypatch.delenv("STERBRUST_DEPLOYMENT_CONFIRMED")
    with pytest.raises(PermissionError):server_contract()


@pytest.mark.parametrize("changes",[
    {"host":"example.org"},{"port":5432},{"database":"production"},{"version":"17.10"},
    {"pgdata":"relative/path"},{"system_identifier":"unknown"},{"format":"unknown"},
])
def test_target_contract_cannot_silently_change_identity(tmp_path,monkeypatch,changes):
    contract(tmp_path,monkeypatch,**changes)
    with pytest.raises(PermissionError):server_contract()
