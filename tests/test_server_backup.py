from pathlib import Path
from types import SimpleNamespace

from scripts import server_backup


def test_server_backup_is_atomic_and_uses_only_dedicated_compose(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.server.yml"
    env = tmp_path / ".env.server"
    compose.write_text("services: {}", encoding="utf-8")
    env.write_text("DB_PASSWORD=not-output", encoding="utf-8")
    monkeypatch.setattr(server_backup, "ROOT", tmp_path)
    monkeypatch.setattr(server_backup, "COMPOSE", compose)
    monkeypatch.setattr(server_backup, "ENV", env)
    monkeypatch.setattr(server_backup, "BACKUPS", tmp_path / "backups")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(b"PGDMP-test-data")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(server_backup.subprocess, "run", fake_run)
    target = server_backup.backup()
    assert target.read_bytes() == b"PGDMP-test-data"
    assert target.with_name(target.name + ".json").is_file()
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert str(compose) in commands[0]
    assert "not-output" not in repr(commands)
