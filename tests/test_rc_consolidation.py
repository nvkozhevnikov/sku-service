import hashlib
import json
from unittest.mock import patch

import pytest

from scripts.import_stage4_commercial_to_rc import _backup_verified, _copy


def test_import_backup_must_be_bound_to_target_identity(tmp_path):
    archive = tmp_path / "baseline.dump"
    archive.write_bytes(b"PGDMPfixture")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar = tmp_path / "baseline.identity.json"
    record = {"source": "dedicated_local_rc_pg17", "database": "universal_supplier_server",
              "port": 55449, "system_identifier": 123456, "dump_sha256": digest,
              "dump_bytes": archive.stat().st_size, "migration_count": 15}
    sidecar.write_text(json.dumps(record), encoding="utf-8")
    assert _backup_verified(archive, digest, sidecar, target_system_id=123456, target_port=55449)["sha256"] == digest
    with pytest.raises(RuntimeError, match="target identity"):
        _backup_verified(archive, digest, sidecar, target_system_id=999999, target_port=55449)


def test_commercial_import_remaps_every_fk_without_reusing_stage4_pk():
    source = {
        "suppliers": [{"id": 10, "code": "intervesp", "base_url": "https://intervesp.ru/",
                       "adapter_name": "intervesp", "enabled": False}],
        "source_products": [{"id": 20, "supplier_id": 10, "external_id": "8992"}],
        "offers": [{"id": 30, "supplier_id": 10, "source_product_id": 20}],
        "supplier_http_captures": [{"id": 6, "supplier_id": 10, "source_product_id": 20,
                                    "diagnostics": {"codes": []}}],
        "offer_commercial_observations": [{"id": 50, "supplier_id": 10, "source_product_id": 20,
                                           "offer_id": 30, "capture_id": 6}],
    }
    inserted = []

    class Cursor:
        def execute(self, *_): pass
        def fetchone(self): return None

    def new_id(_cursor, table, row, *, replace=None, exclude=None):
        identity = {"suppliers": 110, "source_products": 120, "offers": 130,
                    "supplier_http_captures": 140, "offer_commercial_observations": 150}[table]
        inserted.append((table, row, replace or {}))
        return identity

    with patch("scripts.import_stage4_commercial_to_rc._insert", side_effect=new_id):
        counts = _copy(Cursor(), source)
    assert counts["captures"] == counts["observations"] == 1
    replacements = {table: replace for table, _, replace in inserted}
    assert replacements["source_products"]["supplier_id"] == 110
    assert replacements["offers"] == {"supplier_id": 110, "source_product_id": 120}
    assert replacements["supplier_http_captures"]["source_product_id"] == 120
    assert replacements["supplier_http_captures"]["diagnostics"]["rc_import_evidence_integrity"] == "synthetic_test_hash_not_verified"
    assert replacements["offer_commercial_observations"] == {
        "supplier_id": 110, "source_product_id": 120, "offer_id": 130, "capture_id": 140}
