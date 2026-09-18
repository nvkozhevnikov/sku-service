#!/usr/bin/env python3
"""Refuse to use the working PostgreSQL volume as a preview clone target."""
from __future__ import annotations
import sys

WORKING_VOLUME = "universal_supplier_pgdata"
PREVIEW_VOLUME = "universal_supplier_admin_preview_pgdata"


def assert_preview_volume_safe(name: str) -> None:
    value = (name or "").strip()
    if not value:
        raise SystemExit("preview volume name is empty")
    if value == WORKING_VOLUME or value.endswith("/" + WORKING_VOLUME):
        raise SystemExit(f"REFUSING to use working volume {WORKING_VOLUME} as preview data")
    if "universal_supplier_pgdata" == value:
        raise SystemExit("REFUSING working PostgreSQL volume")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: preview_volume_guard.py <volume-name>")
    assert_preview_volume_safe(sys.argv[1])
    print("PREVIEW_VOLUME_SAFE = PASS")


if __name__ == "__main__":
    main()
