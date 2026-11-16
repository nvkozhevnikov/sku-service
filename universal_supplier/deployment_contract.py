"""Explicit hash-pinned server target; local historical identity stays the default."""
import hashlib
import json
import os
from pathlib import Path
import re


def server_contract():
    path = os.environ.get("STERBRUST_DEPLOYMENT_CONTRACT", "")
    digest = os.environ.get("STERBRUST_DEPLOYMENT_CONTRACT_SHA256", "")
    if not path and not digest:
        return None
    if not path or not re.fullmatch(r"[0-9a-f]{64}", digest) or os.environ.get("STERBRUST_DEPLOYMENT_CONFIRMED") != "YES":
        raise PermissionError("DEPLOYMENT_CONTRACT_CONFIRMATION_REQUIRED")
    body = Path(path).read_bytes()
    if len(body) > 16384 or hashlib.sha256(body).hexdigest() != digest:
        raise PermissionError("DEPLOYMENT_CONTRACT_HASH_MISMATCH")
    value = json.loads(body)
    if (value.get("format") != "sterbrust-deployment/v1"
            or value.get("host") != "127.0.0.1" or type(value.get("port")) is not int or value["port"] != 55461
            or value.get("database") != "rc_verify_vi_20261019" or value.get("version") != "17.11"
            or not re.fullmatch(r"[0-9]{15,20}", str(value.get("system_identifier", "")))
            or not isinstance(value.get("pgdata"), str) or not Path(value["pgdata"]).is_absolute()):
        raise PermissionError("DEPLOYMENT_CONTRACT_INVALID")
    return value
