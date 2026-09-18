#!/usr/bin/env python3
"""Synchronize deterministic review cases from persisted matching evidence."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.postgres import PostgresConfig

if __name__ == "__main__":
    print(PostgresAdminStore(PostgresConfig.from_env()).generate_review_cases())
