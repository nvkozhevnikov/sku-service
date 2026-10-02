"""Fail-closed local UI bridge for bounded or full manual QA collection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from universal_supplier.commercial_collection import (
    CandidateManifest, CommercialCollectionResult, run_commercial_collection,
    write_collection_report, write_review_report,
)
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.full_supplier_collection import (
    FullManifest, PublicPacer, discover_full_manifest, run_full_collection, verify_qa_identity,
)
from .stage4_qa_store import STAGE4_DATABASE, STAGE4_HOST, STAGE4_PORT, STAGE4_ROLE


ALLOWED_SOURCES = frozenset({"intervesp", "beka_mak"})
FULL_SOURCE_CODES = ("intervesp", "beka_mak", "beka_mak_tr")
FULL_ALLOWED_SOURCES = frozenset((*FULL_SOURCE_CODES, "all"))
MAX_UI_LIMIT = 3
MAX_MANUAL_LIMIT = 100
MIN_PAUSE_SECONDS = 20.0


class CommercialQaUnavailable(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CommercialQaRun:
    id: str
    source: str
    limit: int
    status: str
    started_at: str
    finished_at: str | None = None
    error: str = ""
    report_path: str = ""
    review_path: str = ""
    rows: list[dict[str, Any]] | None = None
    site_summary: dict[str, Any] | None = None
    mode: str = "dry_run"
    target: str = "без БД"
    processed: int = 0
    total: int = 0
    manifest_sha256: str = ""
    counts_before: dict[str, int] | None = None
    counts_after: dict[str, int] | None = None
    xml_sha256: str = ""
    xml_products: int | None = None
    proposals_sha256: str = ""
    scope: str = "stage5d_bounded"

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["rows"] = data["rows"] or []
        return data


class DisabledCommercialQaService:
    """Visible state when the safe UI bridge has not been explicitly enabled."""

    enabled = False

    def __init__(self, reason: str = "Тестовый сбор не настроен для этого запуска панели.") -> None:
        self.reason = reason

    def overview(self) -> dict[str, Any]:
        return {"enabled": False, "reason": self.reason, "runs": [], "manifest": {}, "xml": None}

    def start(self, source: str, limit: int, dry_run: bool) -> CommercialQaRun:
        raise CommercialQaUnavailable(self.reason)

    def run(self, run_id: str) -> dict[str, Any] | None:
        return None

    def report_bytes(self, run_id: str) -> bytes:
        raise CommercialQaUnavailable(self.reason)

    def diagnostic_xml(self) -> tuple[bytes, dict[str, Any]]:
        raise CommercialQaUnavailable(self.reason)

    def proposals(self) -> dict[str, Any]:
        raise CommercialQaUnavailable(self.reason)

    def proposal_artifact(self, filename: str) -> bytes:
        raise CommercialQaUnavailable(self.reason)


class CommercialQaService:
    """Local-only manual collector with a durable per-run JSONL journal."""

    enabled = True

    def __init__(self, *, manifest_path: Path, artifacts_dir: Path, diagnostic_xml_path: Path,
                 full_manifest_path: Path | None = None, proposals_path: Path | None = None,
                 collector: Callable[..., CommercialCollectionResult] = run_commercial_collection,
                 write_repository_factory: Callable[[], Any] | None = None,
                 server_target: PostgresConfig | None = None,
                 server_system_identifier: int | None = None,
                 allow_missing_xml: bool = False,
                 server_mode: bool = False) -> None:
        self.manifest_path = manifest_path.resolve()
        self.artifacts_dir = artifacts_dir.resolve()
        self.diagnostic_xml_path = diagnostic_xml_path.resolve()
        if not self.manifest_path.is_file():
            raise CommercialQaUnavailable("Не найден защищённый candidate manifest.")
        if not allow_missing_xml and not self.diagnostic_xml_path.is_file():
            raise CommercialQaUnavailable("Не найден диагностический XML Stage 5D.")
        try:
            self.manifest = CandidateManifest.from_jsonable(json.loads(self.manifest_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CommercialQaUnavailable(f"Candidate manifest недействителен: {error}") from error
        if set(self.manifest.sites) != set(ALLOWED_SOURCES):
            raise CommercialQaUnavailable("Manifest должен содержать только Intervesp и Beka-Mak.")
        self.full_manifest_path = full_manifest_path.resolve() if full_manifest_path else None
        self.proposals_path = proposals_path.resolve() if proposals_path else None
        self.full_manifest = None
        if self.full_manifest_path is not None:
            try:
                self.full_manifest = FullManifest.from_jsonable(json.loads(self.full_manifest_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise CommercialQaUnavailable(f"Full manifest недействителен: {error}") from error
        self.collector = collector
        self.write_repository_factory = write_repository_factory
        self.server_target = server_target
        self.server_system_identifier = server_system_identifier
        self.server_mode = server_mode
        self._runs: dict[str, CommercialQaRun] = {}
        self._lock = Lock()
        self._load_journal()

    def _record_event(self, run: CommercialQaRun) -> None:
        """Append a complete state checkpoint without storing DB credentials."""
        run_dir = self.artifacts_dir / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        body = (json.dumps(run.public(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with (run_dir / "events.jsonl").open("ab") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())

    def _load_journal(self) -> None:
        if not self.artifacts_dir.is_dir():
            return
        fields = set(CommercialQaRun.__dataclass_fields__)
        for run_dir in self.artifacts_dir.iterdir():
            if not run_dir.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", run_dir.name):
                continue
            events = run_dir / "events.jsonl"
            if not events.is_file():
                continue
            last = None
            for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("id") == run_dir.name:
                    last = item
            if last is None:
                continue
            try:
                run = CommercialQaRun(**{key: value for key, value in last.items() if key in fields})
            except (TypeError, ValueError):
                continue
            if run.status in {"queued", "running"}:
                run.status = "interrupted"
                run.finished_at = _utc_now()
                run.error = "Панель была остановлена; автоматический повтор запрещён. Проверьте отчёт и БД."
                self._record_event(run)
            self._runs[run.id] = run

    @staticmethod
    def _database_counts(repository: Any) -> dict[str, int]:
        tables = ("source_products", "offers", "supplier_http_captures",
                  "offer_commercial_observations", "product_matches", "catalog_products")
        with repository.connection.cursor() as cursor:
            return {table: int(cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                    for table in tables}

    def _refresh_diagnostic_xml(self, repository: Any, run_id: str) -> tuple[str, int]:
        """Read the committed QA projection; atomically replace diagnostic XML."""
        from scripts.generate_stage5d_commercial_qa_xml import (
            CURRENT_COMMERCIAL_ROWS_SQL, rows_from_database_records,
        )
        from universal_supplier.xml_export import supplier_xml_bytes

        with repository.connection.cursor() as cursor:
            cursor.execute(CURRENT_COMMERCIAL_ROWS_SQL, ("intervesp", "beka_mak", "beka_mak_tr"))
            columns = [item.name for item in cursor.description]
            records = [dict(zip(columns, values)) for values in cursor.fetchall()]
        rows = rows_from_database_records(records)
        document = supplier_xml_bytes(rows)
        temporary = self.diagnostic_xml_path.with_name(f".{self.diagnostic_xml_path.name}.{run_id}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.diagnostic_xml_path)
        finally:
            temporary.unlink(missing_ok=True)
        return hashlib.sha256(document).hexdigest(), len(rows)

    def _refresh_proposals(self, run_id: str) -> str:
        """Regenerate diagnostic-only matching and files from committed QA SQL.

        No canonical DB link, offer selection, Bitrix call, or ESOL payload is
        created. Publish the JSON index last so the panel never advertises a
        partially built set of files.
        """
        if self.proposals_path is None or self.full_manifest is None:
            return ""
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("proposal publication requires a journal run ID")
        from scripts.generate_proposed_matching import (
            EXPECTED_REGISTRY_SHA256, classify_with_manifest, prior_unresolved_urls, read_qa_rows,
        )
        from scripts.generate_proposed_supplier_files import generate
        from universal_supplier.matching_service import load_registry_from_csv
        from universal_supplier.proposed_matching import proposal_summary

        root = Path(__file__).resolve().parents[2]
        if self.server_target:
            registry_path = Path(os.environ["COMMERCIAL_SERVER_REGISTRY_PATH"]).resolve()
            sections_path = Path(os.environ["COMMERCIAL_SERVER_SECTIONS_PATH"]).resolve()
            run1_path = Path(os.environ["COMMERCIAL_SERVER_RUN1_REPORT_PATH"]).resolve()
        else:
            registry_path = root / "reports" / "STERBRUST_REGISTRY.csv"
            sections_path = root / "reports" / "rest" / "STERBRUST_SECTIONS_RAW.json"
            run1_path = root / "reports" / "WORKING_V1_2026-09-29" / "RUN1" / "report.json"
        if hashlib.sha256(registry_path.read_bytes()).hexdigest() != EXPECTED_REGISTRY_SHA256:
            raise RuntimeError("canonical registry checksum changed")
        registry = load_registry_from_csv(registry_path)
        if len(registry.products) != 57478:
            raise RuntimeError("canonical registry active count changed")
        qa_identity, records = read_qa_rows(self.server_target,
                                            expected_system_identifier=self.server_system_identifier)
        prior_reports = ([Path(value).resolve() for value in
                          os.environ.get("COMMERCIAL_SERVER_PRIOR_REVIEW_REPORTS", "").split(os.pathsep)
                          if value] if self.server_target else [])
        if self.server_target and (not prior_reports or not all(path.is_file() for path in prior_reports)):
            raise RuntimeError("pinned prior REVIEW reports are unavailable")
        rows, out_of_manifest = classify_with_manifest(
            records, self.full_manifest, registry,
            prior_review_urls=prior_unresolved_urls(
                prior_reports, manifest_sha256=self.full_manifest.sha256))
        matching = {"generated_at": _utc_now(), "diagnostic_only": True,
                    "qa_identity": qa_identity, "manifest_sha256": self.full_manifest.sha256,
                    "registry_sha256": EXPECTED_REGISTRY_SHA256,
                    "registry_active_products": len(registry.products),
                    "summary": proposal_summary(rows), "out_of_manifest_review": out_of_manifest,
                    "rows": rows}
        working = self.artifacts_dir / run_id / "proposal_build"
        working.mkdir(parents=True, exist_ok=True)
        matching_path = working / "MATCHING_CURRENT.json"
        matching_path.write_text(json.dumps(matching, ensure_ascii=False, indent=2), encoding="utf-8")
        generated = working / "files"
        evidence_root = root / "reports" / "WORKING_V1_2026-09-29"
        report_candidates = ([] if self.server_target else [evidence_root / name / "report.json" for name in (
            "RUN1", "RUN1_BEKAMAK_RESUME", "RUN1_OFFICIAL_RESUME", "RUN1_INTERVESP", "RUN2")])
        if self.server_target:
            report_candidates.extend(Path(value).resolve() for value in
                                     os.environ.get("COMMERCIAL_SERVER_CAPTURE_REPORTS", "").split(os.pathsep) if value)
        report_candidates.append(self.artifacts_dir / run_id / "report.json")
        capture_reports = [path for path in report_candidates if path.is_file() and
                           json.loads(path.read_text(encoding="utf-8")).get("manifest_sha256")
                           == self.full_manifest.sha256]
        generate(matching_path=matching_path, registry_path=registry_path,
                 sections_path=sections_path, run1_report_path=run1_path,
                 output_dir=generated, manifest_path=self.full_manifest_path,
                 capture_report_paths=capture_reports)
        builder = (["python", str(root / "scripts" / "build_proposed_workbooks_server.py")]
                   if self.server_target else ["node", str(root / "scripts" / "build_proposed_workbooks.mjs")])
        subprocess.run([*builder, str(generated / "PROPOSAL_TABLES.json"), str(generated)],
                       cwd=root, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, check=True)
        required = ("STERBRUST_UPDATE_EXISTING_PROPOSED.xml", "STERBRUST_UPDATE_EXISTING.xlsx",
                    "STERBRUST_CREATE_NEW_CANDIDATES.xml", "STERBRUST_CREATE_NEW_CANDIDATES.xlsx",
                    "STERBRUST_REVIEW.xlsx", "MATCHING_REPORT.csv")
        for filename in required:
            if not (generated / filename).is_file():
                raise RuntimeError(f"diagnostic proposal file missing: {filename}")
        self.proposals_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_dir = f"files-{run_id}"
        versioned = self.proposals_path.parent / artifact_dir
        if versioned.exists():
            raise RuntimeError("proposal artifact version already exists")
        payload = json.loads((generated / "PROPOSAL_TABLES.json").read_text(encoding="utf-8"))
        payload["artifact_dir"] = artifact_dir
        pending_index = working / "PROPOSAL_TABLES.published.json"
        pending_index.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(generated, versioned)
        os.replace(pending_index, self.proposals_path)
        return hashlib.sha256(self.proposals_path.read_bytes()).hexdigest()

    @classmethod
    def from_env(cls) -> "CommercialQaService | DisabledCommercialQaService":
        if os.environ.get("COMMERCIAL_QA_UI_ENABLED", "NO").upper() != "YES":
            return DisabledCommercialQaService()
        required = {
            "COMMERCIAL_QA_UI_MANIFEST_PATH": os.environ.get("COMMERCIAL_QA_UI_MANIFEST_PATH"),
            "COMMERCIAL_QA_UI_ARTIFACTS_DIR": os.environ.get("COMMERCIAL_QA_UI_ARTIFACTS_DIR"),
            "COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH": os.environ.get("COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            return DisabledCommercialQaService("Не заданы: " + ", ".join(missing))
        try:
            factory = None
            server_target = None
            server_system_identifier = None
            if os.environ.get("COMMERCIAL_QA_WRITE_ENABLE") == "YES" and os.environ.get("COMMERCIAL_QA_WRITE_CONFIRM") == "STAGE4_QA_ONLY":
                password = os.environ.get("COMMERCIAL_QA_STAGE4_PASSWORD") or None
                factory = lambda: PostgresRepository(PostgresConfig(STAGE4_HOST, STAGE4_PORT, STAGE4_DATABASE, STAGE4_ROLE, password, "disable"))
            if os.environ.get("COMMERCIAL_MANUAL_MODE") == "SERVER":
                # No fallback to Stage 4 QA or an arbitrary HTTP-supplied DSN.
                if os.environ.get("COMMERCIAL_QA_WRITE_ENABLE") == "YES":
                    raise CommercialQaUnavailable("Stage 4 QA write flags are invalid in server mode.")
                if (os.environ.get("COMMERCIAL_SERVER_WRITE_ENABLE") == "YES" and
                        os.environ.get("COMMERCIAL_SERVER_WRITE_CONFIRM") == os.environ.get("DB_NAME") and
                        os.environ.get("DB_SYSTEM_IDENTIFIER", "").isdigit()):
                    server_target = PostgresConfig.from_env()
                    server_system_identifier = int(os.environ["DB_SYSTEM_IDENTIFIER"])
                    factory = lambda: PostgresRepository(server_target)
            return cls(manifest_path=Path(required["COMMERCIAL_QA_UI_MANIFEST_PATH"]),
                       artifacts_dir=Path(required["COMMERCIAL_QA_UI_ARTIFACTS_DIR"]),
                       diagnostic_xml_path=Path(required["COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH"]),
                       full_manifest_path=(Path(os.environ["COMMERCIAL_QA_FULL_MANIFEST_PATH"])
                                           if os.environ.get("COMMERCIAL_QA_FULL_MANIFEST_PATH") else None),
                       proposals_path=(Path(os.environ["COMMERCIAL_QA_PROPOSALS_PATH"])
                                       if os.environ.get("COMMERCIAL_QA_PROPOSALS_PATH") else None),
                       write_repository_factory=factory,
                       server_target=server_target,
                       server_system_identifier=server_system_identifier,
                       allow_missing_xml=os.environ.get("COMMERCIAL_MANUAL_MODE") == "SERVER",
                       server_mode=os.environ.get("COMMERCIAL_MANUAL_MODE") == "SERVER")
        except CommercialQaUnavailable as error:
            return DisabledCommercialQaService(str(error))

    def _manifest_summary(self) -> dict[str, Any]:
        raw = self.manifest.as_jsonable()["sites"]
        return {code: {"candidates": len(value["candidates"]), "sha256": value["sha256"]}
                for code, value in raw.items()}

    def _xml_info(self) -> dict[str, Any]:
        if not self.diagnostic_xml_path.is_file():
            return {"path": self.diagnostic_xml_path.name, "bytes": 0, "sha256": "",
                    "notice": "Диагностический XML ещё не сформирован."}
        content = self.diagnostic_xml_path.read_bytes()
        return {"path": self.diagnostic_xml_path.name, "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "notice": "Диагностический XML. Не является файлом импорта Sterbrust."}

    def overview(self) -> dict[str, Any]:
        with self._lock:
            runs = [item.public() for item in sorted(self._runs.values(), key=lambda item: item.started_at, reverse=True)]
        write_enabled = False
        write_reason = ("Требуются явное server write-включение, точное имя базы, cluster ID и успешный preflight."
                        if self.server_mode else "Требуются два write-флага и успешный preflight QA PostgreSQL.")
        if self.write_repository_factory is not None:
            try:
                for source in FULL_SOURCE_CODES if self.full_manifest else sorted(ALLOWED_SOURCES):
                    self._preflight_write_target(source)
            except Exception:
                # Do not display connection diagnostics, credentials, or an
                # unusable write option.  The POST repeats the same preflight.
                write_reason = "PostgreSQL недоступна или не прошла проверку идентичности и защит."
            else:
                write_enabled = True
                write_reason = ""
        target_name = (self.server_target.dbname if self.server_target else
                       os.environ.get("DB_NAME", "серверная PostgreSQL") if self.server_mode else STAGE4_DATABASE)
        return {"enabled": True, "reason": "", "server_mode": self.server_mode,
                "runs": runs, "manifest": self._manifest_summary(), "xml": self._xml_info(),
                "full_manifest": {site: {"candidates": sum(item.source == site or site == "all"
                                                       for item in self.full_manifest.candidates),
                                          "sha256": self.full_manifest.sha256}
                                  for site in (*FULL_SOURCE_CODES, "all")} if self.full_manifest else {},
                "write": {"enabled": write_enabled, "target": target_name, "reason": write_reason}}

    def _check_write_target(self, repository: Any, source: str) -> dict[str, Any]:
        """Fail closed on DB identity and commercial-supplier state, without writes."""
        repository.gate()
        with repository.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(), current_user, pg_is_in_recovery(), "
                    "current_setting('server_version'), inet_server_addr()::text, inet_server_port(), "
                    "system_identifier FROM pg_control_system()"
                )
                database, role, in_recovery, version, host, port, system_id = cursor.fetchone()
                if self.server_target is None:
                    expected = (STAGE4_DATABASE, STAGE4_ROLE, False, "17.11", STAGE4_HOST,
                                STAGE4_PORT, 7689025282387590508)
                    actual = (database, role, in_recovery, version, host, port, system_id)
                else:
                    expected = (self.server_target.dbname, self.server_target.user, False,
                                "17.11", self.server_target.port, self.server_system_identifier)
                    actual = (database, role, in_recovery, version, port, system_id)
                if actual != expected:
                    raise ValueError("Preflight PostgreSQL не подтвердил разрешённую Stage 4 базу."
                                     if self.server_target is None else
                                     "Preflight PostgreSQL не подтвердил разрешённую server базу и cluster ID.")
                cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
                if cursor.fetchone() != (False, False, False):
                    raise ValueError("Preflight: runtime role не соответствует ограничениям Stage 4 QA.")
                commercial_sources = FULL_SOURCE_CODES if source == "all" else (source,)
                if source not in (*FULL_SOURCE_CODES, "all"):
                    raise ValueError("Preflight: неизвестный коммерческий источник.")
                cursor.execute("SELECT code, enabled FROM suppliers WHERE code = ANY(%s)",
                               (list(commercial_sources),))
                states = dict(cursor.fetchall())
                if set(states) != set(commercial_sources) or any(states.values()):
                    raise ValueError("Preflight: коммерческие suppliers должны оставаться disabled.")
                if self.server_target is None:
                    cursor.execute("SELECT count(*) FROM offers WHERE active")
                else:
                    cursor.execute("SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id "
                                   "WHERE s.code = ANY(%s) AND o.active", (list(FULL_SOURCE_CODES),))
                if cursor.fetchone()[0] != 0:
                    raise ValueError("Preflight: у коммерческого источника обнаружены активные offers.")
                if self.server_target is None:
                    cursor.execute("SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL")
                    if cursor.fetchone()[0] != 0:
                        raise ValueError("Preflight: обнаружены canonical links source_products.")
                    cursor.execute("SELECT count(*) FROM product_matches")
                    if cursor.fetchone()[0] != 0:
                        raise ValueError("Preflight: обнаружены product_matches.")
                # Persisted canonical decisions are expected after server-side
                # matching. Ingest must preserve them; the isolated QA-only
                # zero-match invariant must not disable later manual refresh.
        return {"database": database, "role": role, "version": version,
                "system_identifier": system_id, "source": source}

    def _preflight_write_target(self, source: str) -> None:
        """Check the configured target with SELECT-only statements."""
        if self.write_repository_factory is None:
            raise ValueError("Запись в PostgreSQL не включена для этой панели.")
        repository = self.write_repository_factory()
        try:
            self._check_write_target(repository, source)
        finally:
            repository.close()

    def start(self, source: str, limit: int, dry_run: bool, *, full_manifest: bool = False,
              write_confirmed: bool = False, full_scope: bool = False) -> CommercialQaRun:
        if source not in (FULL_ALLOWED_SOURCES if full_scope else ALLOWED_SOURCES):
            raise ValueError("Недоступный источник для выбранного режима.")
        if full_scope and self.full_manifest is None:
            raise ValueError("Полный проверенный manifest ещё не подключён.")
        if not dry_run and self.write_repository_factory is None:
            raise ValueError("Запись в QA PostgreSQL не включена для этой панели.")
        if not dry_run and not write_confirmed:
            raise ValueError("Перед записью в QA PostgreSQL требуется отдельное подтверждение оператора.")
        if not dry_run:
            for selected_source in (FULL_SOURCE_CODES if source == "all" else (source,)):
                self._preflight_write_target(selected_source)
        available = (sum(item.source == source or source == "all" for item in self.full_manifest.candidates)
                     if full_scope else len(self.manifest.sites[source]))
        if full_manifest:
            limit = available
        maximum = (available if full_scope else MAX_UI_LIMIT) if dry_run else (
            max(MAX_MANUAL_LIMIT, available) if full_scope else MAX_MANUAL_LIMIT
        )
        if not 1 <= limit <= maximum:
            raise ValueError(f"Лимит запуска — от 1 до {maximum} карточек.")
        if full_scope and limit > available:
            raise ValueError("Лимит не может превышать число подтверждённых кандидатов источника.")
        with self._lock:
            if any(item.status in {"queued", "running"} for item in self._runs.values()):
                raise ValueError("Уже выполняется один тестовый сбор. Дождитесь результата.")
            run = CommercialQaRun(uuid4().hex, source, limit, "queued", _utc_now(),
                                  mode="dry_run" if dry_run else "qa_write",
                                  target="без БД" if dry_run else (getattr(self, "server_target", None).dbname
                                      if getattr(self, "server_target", None) else "Stage 4 QA PostgreSQL"),
                                  total=limit,
                                  manifest_sha256=(self.full_manifest.sha256 if full_scope
                                                   else self._manifest_summary()[source]["sha256"]),
                                  scope="full_sitemap" if full_scope else "stage5d_bounded")
            self._runs[run.id] = run
            try:
                self._record_event(run)
            except OSError:
                del self._runs[run.id]
                raise CommercialQaUnavailable("Не удалось создать журнал запуска; сбор не начат.")
        Thread(target=self._execute, args=(run.id,), name=f"commercial-qa-{source}", daemon=True).start()
        return run

    def _execute(self, run_id: str) -> None:
        run_dir = self.artifacts_dir / run_id
        repository = None
        try:
            with self._lock:
                run = self._runs[run_id]
                run.status = "running"
                self._record_event(run)
            repository = None if run.mode == "dry_run" else self.write_repository_factory()
            if repository is not None:
                with self._lock:
                    run.counts_before = self._database_counts(repository)
                    self._record_event(run)

            def on_progress(row, processed: int, total: int) -> None:
                with self._lock:
                    run.processed = processed
                    run.total = total
                    if run.rows is None:
                        run.rows = []
                    run.rows.append(asdict(row))
                    self._record_event(run)

            report_path = run_dir / "report.json"
            review_path = run_dir / "reviews.json"
            if run.scope == "full_sitemap":
                pacer = PublicPacer()
                discovery_evidence: list[dict] = []
                current_manifest = discover_full_manifest(pacer=pacer, evidence_log=discovery_evidence)
                if current_manifest.sha256 != self.full_manifest.sha256:
                    raise ValueError("Full discovery изменился; запись не начата. Проверьте новый manifest.")
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "discovery_evidence.json").write_text(
                    json.dumps({"manifest_sha256": current_manifest.sha256,
                                "sitemaps": discovery_evidence}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                def on_full_progress(row: dict, processed: int, total: int) -> None:
                    with self._lock:
                        run.processed = processed
                        run.total = total
                        if run.rows is None:
                            run.rows = []
                        run.rows.append(row)
                        self._record_event(run)
                full_result = run_full_collection(
                    manifest=current_manifest, repository=repository, evidence_dir=run_dir / "evidence",
                    report_path=report_path, dry_run=run.mode == "dry_run",
                    sources=FULL_SOURCE_CODES if run.source == "all" else (run.source,),
                    limit=run.limit, pacer=pacer, on_progress=on_full_progress,
                    write_identity_validator=(lambda target: self._check_write_target(target, run.source))
                    if self.server_target is not None else None,
                )
                review_path.write_text(json.dumps({"reviews": [row for row in full_result["rows"]
                                                  if row["status"] in {"REVIEW", "AMBIGUOUS"}]}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                site = {"source": run.source, "attempted": len(full_result["rows"]),
                        "fetched_cards": sum(row["status"] in {"DRY_RUN", "PERSISTED"} for row in full_result["rows"]),
                        "observations_created": sum(bool(row.get("observation_created")) for row in full_result["rows"]),
                        "reviews": sum(row["status"] in {"REVIEW", "AMBIGUOUS"} for row in full_result["rows"]),
                        "errors": sum(row["status"] in {"BLOCKED", "UNSAFE_URL", "NETWORK_ERROR", "HTTP_STATUS", "DB_ERROR"}
                                      for row in full_result["rows"]),
                        "blocked": bool(full_result["blocked_sources"])}
                result_rows = full_result["rows"]
            else:
                result = self.collector(sites=(run.source,), limit=run.limit,
                                        pause_seconds=MIN_PAUSE_SECONDS, evidence_dir=run_dir / "evidence",
                                        dry_run=run.mode == "dry_run", repository=repository, expected_manifest=self.manifest,
                                        candidate_offset=0, catalog_page_limit=8, on_progress=on_progress)
                write_collection_report(result, report_path)
                write_review_report(result.rows, review_path)
                site = asdict(result.site_results[0])
                result_rows = [asdict(row) for row in result.rows]
            database_error = bool(run.scope == "full_sitemap" and full_result.get("aborted_reason"))
            try:
                counts_after = self._database_counts(repository) if repository is not None and not database_error else None
            except Exception:
                counts_after = None
                database_error = True
            xml_sha256 = ""
            xml_products = None
            xml_error = False
            proposals_sha256 = ""
            proposal_error = False
            if repository is not None and not database_error:
                try:
                    xml_sha256, xml_products = self._refresh_diagnostic_xml(repository, run_id)
                except Exception:
                    xml_error = True
                if not xml_error:
                    try:
                        proposals_sha256 = self._refresh_proposals(run_id)
                    except Exception:
                        proposal_error = True
            with self._lock:
                run.status = "partial" if (database_error or xml_error or proposal_error or
                                           site["blocked"] or site["errors"]) else "succeeded"
                run.finished_at = _utc_now()
                run.report_path = str(report_path)
                run.review_path = str(review_path)
                run.rows = result_rows
                run.site_summary = site
                run.counts_after = counts_after
                run.xml_sha256 = xml_sha256
                run.xml_products = xml_products
                run.proposals_sha256 = proposals_sha256
                if database_error:
                    run.error = "PostgreSQL недоступна или запись прервалась; дальнейшие GET остановлены. Проверьте SQL и отчёт перед повтором."
                elif xml_error:
                    run.error = "Коммерческие записи могли сохраниться, но диагностический XML не обновлён. Проверьте БД и отчёт."
                elif proposal_error:
                    run.error = "Коммерческие записи и XML сохранены, но файлы сопоставления не обновлены. Проверьте QA snapshot и журнал."
                self._record_event(run)
        except Exception as error:
            with self._lock:
                run.status = "partial" if run.mode == "qa_write" and repository is not None else "failed"
                run.finished_at = _utc_now()
                run.error = (f"{type(error).__name__}: сбор прерван; проверьте журнал и SQL counts. "
                             "Автоматического повтора нет.")
                if repository is not None:
                    try:
                        run.counts_after = self._database_counts(repository)
                    except Exception:
                        pass
                try:
                    self._record_event(run)
                except OSError:
                    pass
        finally:
            if repository is not None:
                repository.close()

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._runs.get(run_id)
            return value.public() if value else None

    def report_bytes(self, run_id: str) -> bytes:
        item = self.run(run_id)
        if not item or not item.get("report_path"):
            raise KeyError(run_id)
        path = Path(str(item["report_path"])).resolve()
        if self.artifacts_dir not in path.parents:
            raise CommercialQaUnavailable("Недопустимый путь отчёта.")
        return path.read_bytes()

    def diagnostic_xml(self) -> tuple[bytes, dict[str, Any]]:
        if not self.diagnostic_xml_path.is_file():
            raise CommercialQaUnavailable("Диагностический XML ещё не сформирован.")
        return self.diagnostic_xml_path.read_bytes(), self._xml_info()

    def proposals(self) -> dict[str, Any]:
        if self.proposals_path is None or not self.proposals_path.is_file():
            raise CommercialQaUnavailable("Сопоставление с актуальным Sterbrust ещё не сформировано.")
        body = self.proposals_path.read_bytes()
        payload = json.loads(body)
        if payload.get("diagnostic_only") is not True or payload.get("esol_payload") is not False:
            raise CommercialQaUnavailable("Диагностический статус файлов не подтверждён.")
        tables = payload.get("tables") or {}
        if any(not isinstance(tables.get(key), list) for key in ("existing", "new", "review")):
            raise CommercialQaUnavailable("Таблицы предложений повреждены.")
        artifact_dir = payload.get("artifact_dir")
        if artifact_dir is not None and not re.fullmatch(r"files-[0-9a-f]{32}", str(artifact_dir)):
            raise CommercialQaUnavailable("Путь версии диагностических файлов недействителен.")
        counts = {key: len(tables[key]) for key in ("existing", "new", "review")}
        counts["conflict"] = sum(row.get("export_status") == "CONFLICT" for row in tables["review"])
        counts["review_only"] = counts["review"] - counts["conflict"]
        return {"sha256": hashlib.sha256(body).hexdigest(), "manifest_sha256": payload.get("manifest_sha256"),
                "registry_sha256": payload.get("registry_sha256"), "tables": tables,
                "artifact_dir": artifact_dir,
                "readiness_counts": payload.get("readiness_counts") or {},
                "counts": counts}

    def proposal_artifact(self, filename: str) -> bytes:
        allowed = {"STERBRUST_UPDATE_EXISTING_PROPOSED.xml", "STERBRUST_UPDATE_EXISTING.xlsx",
                   "STERBRUST_CREATE_NEW_CANDIDATES.xml", "STERBRUST_CREATE_NEW_CANDIDATES.xlsx",
                   "STERBRUST_REVIEW.xlsx", "MATCHING_REPORT.csv",
                   "STERBRUST_EXISTING_PRICE_AVAILABILITY.xlsx", "UNIVERSAL_SUPPLIER_NEUTRAL.xml"}
        if filename not in allowed or self.proposals_path is None:
            raise CommercialQaUnavailable("Файл не входит в разрешённый набор диагностических артефактов.")
        index = self.proposals()
        path = self.proposals_path.parent / (index.get("artifact_dir") or "") / filename
        if not path.is_file():
            raise CommercialQaUnavailable("Диагностический файл ещё не сформирован.")
        return path.read_bytes()
