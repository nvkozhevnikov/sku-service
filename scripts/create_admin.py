#!/usr/bin/env python3
from __future__ import annotations

import getpass
import argparse
import base64
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first Universal Supplier administrator")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    args = parser.parse_args()
    store = PostgresAdminStore(PostgresConfig.from_env())
    username = args.username or os.environ.get("FIRST_ADMIN_USERNAME") or input("Имя пользователя: ").strip()
    encoded_name = os.environ.get("FIRST_ADMIN_DISPLAY_NAME_B64")
    display_name = (
        base64.b64decode(encoded_name).decode("utf-8") if encoded_name
        else args.display_name or os.environ.get("FIRST_ADMIN_DISPLAY_NAME") or input("Отображаемое имя: ").strip()
    )
    password = os.environ.get("FIRST_ADMIN_PASSWORD") or getpass.getpass("Пароль (минимум 12 символов): ")
    if not os.environ.get("FIRST_ADMIN_PASSWORD"):
        confirmation = getpass.getpass("Повторите пароль: ")
        if password != confirmation:
            raise SystemExit("Пароли не совпадают")
    user_id = store.create_user(username, display_name, hash_password(password), "ADMIN", actor_id=None)
    print(f"Администратор создан. ID: {user_id}")


if __name__ == "__main__":
    main()
