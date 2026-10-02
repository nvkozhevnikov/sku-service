"""Stream a consistent logical backup of the dedicated server PostgreSQL.

The script never stops a service and never reads or prints database secrets.
Use only with docker-compose.server.yml and an already initialized server DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.server.yml"
ENV = ROOT / ".env.server"
BACKUPS = ROOT / "backups" / "server"


def backup() -> Path:
    if not COMPOSE.is_file() or not ENV.is_file():
        raise RuntimeError("server compose or .env.server is missing")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUPS / f"universal_supplier_server_{stamp}.dump"
    pending = BACKUPS / f".{target.name}.partial"
    if target.exists() or pending.exists():
        raise RuntimeError("backup target already exists")
    command = ["docker", "compose", "--env-file", str(ENV), "-f", str(COMPOSE),
               "exec", "-T", "postgres", "sh", "-c",
               'exec pg_dump -U "$POSTGRES_USER" -d "$APP_DB_NAME" -Fc --no-owner --no-privileges']
    try:
        with pending.open("wb") as handle:
            result = subprocess.run(command, cwd=ROOT, stdout=handle,
                                    stderr=subprocess.PIPE, timeout=1800, check=False)
            handle.flush()
            os.fsync(handle.fileno())
        if result.returncode != 0 or pending.stat().st_size == 0:
            raise RuntimeError(f"pg_dump failed (exit {result.returncode}); inspect server logs")
        checksum = hashlib.sha256()
        with pending.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
        digest = checksum.hexdigest()
        os.replace(pending, target)
        metadata = {"file": target.name, "sha256": digest, "bytes": target.stat().st_size,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "format": "pg_dump custom", "source": "docker-compose.server.yml postgres"}
        (BACKUPS / f"{target.name}.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return target
    finally:
        if pending.exists():
            pending.unlink()


if __name__ == "__main__":
    try:
        result = backup()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"BACKUP_FAILED: {error}") from error
    print(f"BACKUP_OK: {result}")
