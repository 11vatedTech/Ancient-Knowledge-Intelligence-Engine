"""Operator CLI for the AKI research engine."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .acquire import acquire_catalog, acquisition_state
from .blob_store import ContentAddressedStore
from .catalog import SourceCatalog
from .db import Database, initialize
from .doctor import run_doctor
from .domain import ProcessingOperation
from .evidence import resolve_citation
from .ingest import IngestError, ingest_pdf, pending_editions
from .ocr import OcrUnavailable, provider_status, resolve_provider
from .inventory import (
    CatalogConfig,
    acquisition_report,
    export_manifest,
    fetch_catalog,
    import_snapshot,
    load_snapshot,
    write_snapshot,
)
from .jobs import JobQueue
from .manifest import read_manifest
from .page_store import PageArtifactCatalog
from .pdf import PDFPageProcessor
from .research import assess_case, get_case, list_cases, run_research
from .search import SearchFilters, rebuild_index, search
from .worker import enqueue_ingest, run_worker

DEFAULT_DATABASE = ".aki/aki.sqlite3"
DEFAULT_BLOB_ROOT = ".aki/blobs"


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _database(args: argparse.Namespace) -> Database:
    return Database(args.database)


def cmd_init(args: argparse.Namespace) -> int:
    report = initialize(args.database)
    _emit({"status": "initialized", "database": report["path"], **report})
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    report = initialize(args.database)
    _emit({
        "path": report["path"],
        "version_before": report["version_before"],
        "version_after": report["version_after"],
        "applied": report["applied"],
    })
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(data_root=args.data_root)
    _emit({"environment": report.to_dict(), "budget": report.budget()})
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    database = _database(args)
    database.require_current()
    integrity = database.integrity_check()
    with database.connect() as connection:
        assets = connection.execute("SELECT sha256, storage_key FROM assets").fetchall()
    store = ContentAddressedStore(args.blob_root)
    missing = [row["sha256"] for row in assets if not store.verify(row["sha256"])]
    payload = {
        "integrity": integrity,
        "assets_checked": len(assets),
        "assets_missing_or_corrupt": missing,
        "ok": integrity["quick_check"] == "ok" and not missing
        and integrity["foreign_key_violations"] == 0,
    }
    _emit(payload)
    return 0 if payload["ok"] else 1


def cmd_inventory_sync(args: argparse.Namespace) -> int:
    config = CatalogConfig.load(args.config)
    snapshot = fetch_catalog(config, site_url=args.site_url)
    path = write_snapshot(snapshot, args.out)
    database = _database(args)
    initialize(database.path)
    report = import_snapshot(database, snapshot)
    _emit({
        "snapshot": path,
        "advertised_count": snapshot.advertised_count,
        "observed_count": len(snapshot.items),
        "import": report.to_dict(),
    })
    return 0


def cmd_inventory_import(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(args.snapshot)
    database = _database(args)
    initialize(database.path)
    _emit(import_snapshot(database, snapshot).to_dict())
    return 0


def cmd_inventory_report(args: argparse.Namespace) -> int:
    _emit(acquisition_report(_database(args)))
    return 0


def cmd_inventory_manifest(args: argparse.Namespace) -> int:
    _emit(export_manifest(_database(args), args.destination))
    return 0


ATTESTATION_PATH = "docs/execution/PERMISSION_ATTESTATION.md"

DEFAULT_ATTESTED_OPERATIONS = (
    "acquire,store_original,render_pages,ocr_transcribe,vision_analyze,"
    "create_embeddings,internal_search,internal_research"
)


def cmd_rights_attest(args: argparse.Namespace) -> int:
    """Record a rights-holder permission as an explicit, labelled attestation.

    The record states exactly what it is: an assertion by the party claiming authority,
    captured with its timestamp and scope. It is not presented as an independently
    verified licence, and the evidence pointer is the committed attestation document.
    """
    from .db import new_id, utcnow
    from .production_domain import RightsDecisionState, RightsOperation

    database = _database(args)
    database.require_current()
    operations = [item.strip() for item in args.operations.split(",") if item.strip()]
    valid = {member.value for member in RightsOperation}
    unknown = [op for op in operations if op not in valid]
    if unknown:
        raise SystemExit(f"unknown operations: {unknown}")
    state = RightsDecisionState(args.state)
    stamp = utcnow()

    with database.connect() as connection:
        edition_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM editions WHERE source_record_id IS NOT NULL ORDER BY id"
            )
        ]
    if not edition_ids:
        raise SystemExit("no catalogued editions to attest")

    attestation_id = new_id()
    scopes = ", ".join(operations)
    notes = (
        f"Self-attested permission. Attester claims to be the rights holder / operator of "
        f"{args.source_name}. Asserted in an interactive session on {stamp}; no independent "
        f"documentary verification (signed licence, domain-verified email, or written "
        f"instrument) was supplied. Recorded at the account owner's instruction. "
        f"Scope: {scopes}. Basis: {args.basis or 'not supplied'}."
    )
    document = [
        "# Permission Attestation",
        "",
        f"**Attestation ID:** `{attestation_id}`",
        f"**Recorded at:** {stamp}",
        f"**Attester:** {args.reviewer}",
        f"**Source:** {args.source_name}",
        f"**State recorded:** `{state.value}`",
        f"**Operations granted:** {scopes}",
        f"**Basis offered:** {args.basis or '(none supplied)'}",
        f"**Editions covered:** {len(edition_ids)}",
        "",
        "## Evidentiary status",
        "",
        "This document records an **assertion of authority made by the attester**. It is not a",
        "signed licence, not a legal opinion, and not independently verified. It is retained so",
        "that every acquisition performed under it is attributable to a specific person, at a",
        "specific time, with a specific stated scope.",
        "",
        "If the attester's authority is later contested or narrowed, the appropriate response is",
        "to record a superseding decision and stop further processing; existing archived bytes",
        "remain auditable and can be quarantined without loss of provenance.",
        "",
        "## Scope limits that still apply",
        "",
        "- Permission for one operation does not imply another; check the list above.",
        "- Fine-tuning and redistribution remain denied unless listed.",
        "- Rights in the underlying historical work are separate from rights in this digital",
        "  edition, scan, or compilation.",
        "",
    ]
    attestation_file = Path(ATTESTATION_PATH).resolve()
    attestation_file.write_text("\n".join(document), encoding="utf-8")
    evidence_uri = attestation_file.as_uri()

    inserted = 0
    with database.transaction() as connection:
        for edition_id in edition_ids:
            connection.execute(
                "INSERT INTO rights_decisions (id, edition_id, state, jurisdiction, rights_holder,"
                " license_name, license_url, evidence_uri, reviewer, decided_at, scope, notes,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id(), edition_id, state.value, args.jurisdiction, args.reviewer,
                    args.basis or "Self-attested permission", None, evidence_uri,
                    args.reviewer, stamp,
                    json.dumps({"permitted_operations": operations}, sort_keys=True),
                    notes, stamp,
                ),
            )
            inserted += 1
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, details) VALUES (?,?,?,?,?,?,?,?)",
            (
                new_id(), stamp, "human", args.reviewer, "rights.attestation_recorded",
                "collection", args.source_name,
                json.dumps(
                    {
                        "attestation_id": attestation_id,
                        "state": state.value,
                        "operations": operations,
                        "editions": inserted,
                        "document": ATTESTATION_PATH,
                    },
                    sort_keys=True,
                ),
            ),
        )
    _emit({
        "attestation_id": attestation_id,
        "document": str(attestation_file),
        "editions_covered": inserted,
        "state": state.value,
        "permitted_operations": operations,
        "evidentiary_status": "self-attested by claimant; not independently verified",
    })
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    database = _database(args)
    if args.action == "status":
        _emit(acquisition_state(database))
        return 0
    config = CatalogConfig.load(args.config)
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def progress(record: dict) -> None:
        with log_path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")

    report = acquire_catalog(
        database,
        args.blob_root,
        base_url=config.base_url,
        limit=args.limit,
        min_interval_seconds=args.min_interval,
        progress=progress,
    )
    _emit(report.to_dict())
    return 0 if report.failed == 0 else 1


def cmd_rights_grant(args: argparse.Namespace) -> int:
    from .production_domain import RightsDecisionState, RightsOperation

    database = _database(args)
    database.require_current()
    operations = [item.strip() for item in args.operations.split(",") if item.strip()]
    unknown = [op for op in operations if op not in RightsOperation.__members__.values()]
    if unknown:
        raise SystemExit(f"unknown operations: {unknown}")
    with database.transaction() as connection:
        edition = connection.execute(
            "SELECT id FROM editions WHERE id=?", (args.edition,)
        ).fetchone()
        if edition is None:
            raise SystemExit(f"unknown edition {args.edition}")
        from .db import new_id, utcnow

        decision_id = new_id()
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, jurisdiction, rights_holder,"
            " license_name, license_url, evidence_uri, reviewer, decided_at, scope, notes,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id, args.edition, RightsDecisionState(args.state).value,
                args.jurisdiction, args.rights_holder, args.license_name, args.license_url,
                args.evidence_uri, args.reviewer, utcnow(),
                json.dumps({"permitted_operations": operations}, sort_keys=True),
                args.notes, utcnow(),
            ),
        )
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, details) VALUES (?,?,?,?,?,?,?,?)",
            (
                new_id(), utcnow(), "human", args.reviewer, "rights.decision_recorded",
                "edition", args.edition,
                json.dumps({"state": args.state, "operations": operations}, sort_keys=True),
            ),
        )
    _emit({"decision_id": decision_id, "edition_id": args.edition, "state": args.state,
           "permitted_operations": operations})
    return 0


def cmd_rights_list(args: argparse.Namespace) -> int:
    database = _database(args)
    database.require_current()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT rd.id, rd.edition_id, rd.state, rd.reviewer, rd.decided_at, rd.scope,"
            " e.edition_title FROM rights_decisions rd"
            " LEFT JOIN editions e ON e.id = rd.edition_id"
            " ORDER BY rd.created_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    _emit([dict(row) for row in rows])
    return 0


def cmd_catalog_import(args: argparse.Namespace) -> int:
    catalog = SourceCatalog(Path(args.database))
    added, duplicates = catalog.import_records(read_manifest(args.manifest))
    _emit({"added": added, "duplicates": duplicates, "total": len(catalog.list_sources())})
    return 0


def cmd_catalog_list(args: argparse.Namespace) -> int:
    catalog = SourceCatalog(Path(args.database))
    _emit([
        {
            "source_id": source.source_id,
            "title": source.title,
            "creator": source.creator,
            "language": source.language,
            "source_uri": source.source_uri,
            "collection": source.collection,
            "edition_label": source.edition_label,
            "rights_status": source.rights.status.value,
            "permitted_operations": sorted(
                operation.value for operation in source.rights.permitted_operations
            ),
        }
        for source in catalog.list_sources()
    ])
    return 0


def cmd_pdf_inspect(args: argparse.Namespace) -> int:
    result = PDFPageProcessor(max_pages=args.max_pages).inspect(args.pdf)
    _emit({
        "source_pdf_sha256": result.source_pdf_sha256,
        "file_size_bytes": result.file_size_bytes,
        "page_count": result.page_count,
        "metadata": dict(result.metadata),
    })
    return 0


def cmd_pdf_render(args: argparse.Namespace) -> int:
    catalog = SourceCatalog(Path(args.database))
    source = catalog.authorize(args.source_id, ProcessingOperation.STORE)
    pages = PDFPageProcessor().render_to_store(
        args.pdf, source.source_id, source.rights,
        ContentAddressedStore(args.blob_root), dpi=args.dpi,
    )
    manifest_store = PageArtifactCatalog(args.database, ContentAddressedStore(args.blob_root))
    added, duplicates = manifest_store.record_batch(pages)
    _emit({
        "source_id": source.source_id,
        "rendered_pages": len(pages),
        "manifest_added": added,
        "manifest_duplicates": duplicates,
        "pages": [page.to_dict() for page in pages],
    })
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    database = _database(args)
    try:
        result = ingest_pdf(
            database, args.blob_root, args.pdf,
            edition_id=args.edition_id, dpi=args.dpi,
            ocr=bool(getattr(args, "ocr", False)), provider_name=args.provider,
        )
    except (IngestError, PermissionError, OcrUnavailable) as exc:
        _emit({"error": str(exc), "ingested": False})
        return 2
    _emit(result.to_dict())
    return 0


def cmd_ingest_batch(args: argparse.Namespace) -> int:
    """Ingest every acquired edition that has no rendered pages yet.

    Resumability comes from the database, not a side manifest: an edition drops out of
    ``pending_editions`` the moment its first page row is committed, so re-running after
    an interruption continues rather than repeating.
    """
    import json
    import time
    from pathlib import Path

    from .db import utcnow

    database = _database(args)
    pending = pending_editions(database, args.blob_root)
    if args.edition_id:
        pending = [(edition, path) for edition, path in pending if edition == args.edition_id]
    if args.limit is not None:
        pending = pending[: args.limit]

    engine = None
    if args.ocr:
        try:
            engine = resolve_provider(args.provider)
        except OcrUnavailable as exc:
            _emit({"error": str(exc), "started": False})
            return 2

    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    totals = {"editions": len(pending), "succeeded": 0, "failed": 0, "skipped": 0,
              "pages": 0, "pages_ocr": 0, "evidence": 0, "chunks": 0}
    errors: list[str] = []
    with log.open("a", encoding="utf-8") as handle:
        for edition_id, pdf_path in pending:
            record: dict[str, object] = {"edition_id": edition_id, "started_at": utcnow()}
            try:
                result = ingest_pdf(
                    database, args.blob_root, pdf_path, edition_id=edition_id,
                    dpi=args.dpi, ocr=args.ocr, ocr_provider=engine,
                )
            except Exception as exc:  # noqa: BLE001 - one bad edition must not stop the pass
                totals["failed"] += 1
                message = f"{edition_id}: {type(exc).__name__}: {exc}"
                errors.append(message)
                record.update({"status": "failed", "error": message})
            else:
                if result.skipped:
                    totals["skipped"] += 1
                    record["status"] = "skipped"
                else:
                    totals["succeeded"] += 1
                    totals["pages"] += result.pages_rendered
                    totals["pages_ocr"] += result.pages_ocr
                    totals["evidence"] += result.evidence_created
                    totals["chunks"] += result.chunks_indexed
                    record.update({"status": "completed", **result.to_dict()})
            record["finished_at"] = utcnow()
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
    totals["errors"] = errors[:20]
    totals["elapsed_seconds"] = round(time.time() - started, 1)
    _emit(totals)
    return 0 if totals["failed"] == 0 else 1


def cmd_ocr(args: argparse.Namespace) -> int:
    database = _database(args)
    if args.action == "providers":
        _emit({"providers": provider_status()})
        return 0
    from pathlib import Path

    try:
        engine = resolve_provider(args.provider)
    except OcrUnavailable as exc:
        _emit({"error": str(exc)})
        return 2
    with database.connect() as connection:
        row = connection.execute(
            "SELECT p.id, p.page_image_key FROM pages p JOIN assets a ON a.id = p.asset_id"
            " WHERE a.edition_id = ? AND p.page_index = ?",
            (args.edition, args.page),
        ).fetchone()
    if row is None:
        _emit({"error": f"edition {args.edition} has no rendered page {args.page}"})
        return 2
    image = Path(args.blob_root) / row["page_image_key"]
    if not image.exists():
        _emit({"error": f"page image is missing from the vault: {row['page_image_key']}"})
        return 2
    recognised = engine.ocr(image)
    _emit({"page_id": row["id"], "image": str(image), **recognised.to_dict(),
           "text": recognised.text[:4000]})
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    database = _database(args)
    filters = SearchFilters(
        work_id=args.work, edition_id=args.edition, language=args.language,
        evidence_class=args.evidence_class,
    )
    hits = search(database, args.query, filters=filters, limit=args.limit, mode=args.mode)
    _emit([hit.to_dict() for hit in hits])
    return 0


def cmd_cite(args: argparse.Namespace) -> int:
    database = _database(args)
    try:
        citation = resolve_citation(database, args.evidence_id)
    except LookupError as exc:
        _emit({"error": str(exc), "resolved": False})
        return 2
    _emit({"resolved": True, **citation.to_dict()})
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    database = _database(args)
    result = run_research(
        database, args.question, limit=args.limit, output_dir=args.out_dir,
    )
    _emit(result.to_dict())
    if args.show_dossier:
        print(result.dossier)
    return 0 if result.verification.ok else 1


def cmd_case(args: argparse.Namespace) -> int:
    database = _database(args)
    payload = get_case(database, args.case_id)
    if payload is None:
        _emit({"error": f"unknown case {args.case_id}"})
        return 2
    _emit({"case": payload, "verification": assess_case(database, args.case_id)})
    return 0


def cmd_cases(args: argparse.Namespace) -> int:
    _emit(list_cases(_database(args), limit=args.limit))
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    database = _database(args)
    queue = JobQueue(database)
    if args.action == "status":
        _emit({"stats": queue.stats(), "jobs": [
            {
                "id": job.id, "type": job.job_type, "state": job.state.value,
                "attempts": job.attempt_count, "subject": job.subject_id,
                "last_error": job.last_error,
            }
            for job in queue.list_jobs(limit=args.limit)
        ]})
    elif args.action == "recover":
        _emit(queue.recover())
    elif args.action == "run":
        _emit(run_worker(
            database, max_jobs=args.max_jobs, capabilities=frozenset(
                args.capabilities.split(",") if args.capabilities else ()
            ),
        ).to_dict())
    elif args.action == "enqueue-ingest":
        job_id, created = enqueue_ingest(
            database, edition_id=args.edition_id, pdf_path=args.pdf,
            blob_root=args.blob_root, dpi=args.dpi,
        )
        _emit({"job_id": job_id, "created": created})
    elif args.action == "cancel":
        _emit({"cancelled": queue.cancel(args.job_id)})
    return 0


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    _emit(rebuild_index(_database(args)))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    _emit(_database(args).backup(args.destination))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    _emit(_database(args).restore(args.source, confirm=args.confirm))
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    _emit(_database(args).coverage())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aki", description="Ancient Knowledge Intelligence Engine"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--blob-root", default=DEFAULT_BLOB_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create or upgrade the local database").set_defaults(func=cmd_init)
    sub.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("verify", help="verify database and vault integrity").set_defaults(func=cmd_verify)
    sub.add_parser("coverage", help="show corpus coverage counters").set_defaults(func=cmd_coverage)

    doctor = sub.add_parser("doctor", help="detect hardware and runtime capability")
    doctor.add_argument("--data-root", default=None)
    doctor.set_defaults(func=cmd_doctor)

    inventory = sub.add_parser("inventory", help="catalog inventory reconciliation")
    inventory_sub = inventory.add_subparsers(dest="action", required=True)
    sync = inventory_sub.add_parser("sync", help="enumerate a public catalog and import it")
    sync.add_argument("--config", required=True, help="catalog connection JSON (gitignored)")
    sync.add_argument("--site-url", default="https://theholyword.net/")
    sync.add_argument("--out", default="data/holyword/inventory_snapshot.json")
    sync.set_defaults(func=cmd_inventory_sync)
    imp = inventory_sub.add_parser("import", help="import a captured snapshot")
    imp.add_argument("snapshot")
    imp.set_defaults(func=cmd_inventory_import)
    rep = inventory_sub.add_parser("report", help="factual acquisition report")
    rep.set_defaults(func=cmd_inventory_report)
    man = inventory_sub.add_parser("manifest", help="export the JSONL acquisition manifest")
    man.add_argument("destination")
    man.set_defaults(func=cmd_inventory_manifest)

    rights = sub.add_parser("rights", help="rights decisions")
    rights_sub = rights.add_subparsers(dest="action", required=True)
    grant = rights_sub.add_parser("grant", help="record an evidence-backed rights decision")
    grant.add_argument("--edition", required=True)
    grant.add_argument("--state", required=True,
                       choices=["cleared_limited", "cleared_full", "restricted", "denied"])
    grant.add_argument("--operations", required=True,
                       help="comma-separated operations, e.g. store_original,render_pages")
    grant.add_argument("--reviewer", required=True)
    grant.add_argument("--license-name", default=None)
    grant.add_argument("--license-url", default=None)
    grant.add_argument("--evidence-uri", default=None)
    grant.add_argument("--jurisdiction", default=None)
    grant.add_argument("--rights-holder", default=None)
    grant.add_argument("--notes", default="")
    grant.set_defaults(func=cmd_rights_grant)
    rights_list = rights_sub.add_parser("list", help="list recent rights decisions")
    rights_list.add_argument("--limit", type=int, default=50)
    rights_list.set_defaults(func=cmd_rights_list)
    attest = rights_sub.add_parser(
        "attest", help="record a rights-holder permission statement for the collection"
    )
    attest.add_argument("--reviewer", required=True)
    attest.add_argument("--source-name", default="The Holy Word (theholyword.net)")
    attest.add_argument("--basis", default=None, help="stated basis for the grant")
    attest.add_argument("--jurisdiction", default=None)
    attest.add_argument("--state", default="cleared_limited",
                        choices=["cleared_limited", "cleared_full"])
    attest.add_argument("--operations", default=DEFAULT_ATTESTED_OPERATIONS)
    attest.set_defaults(func=cmd_rights_attest)

    acquire = sub.add_parser("acquire", help="batch acquisition of catalogued objects")
    acquire_sub = acquire.add_subparsers(dest="action", required=True)
    acquire_run = acquire_sub.add_parser("run", help="download rights-cleared objects")
    acquire_run.add_argument("--config", required=True)
    acquire_run.add_argument("--limit", type=int, default=None)
    acquire_run.add_argument("--min-interval", type=float, default=1.0)
    acquire_run.add_argument("--log", default=".aki/acquisition-progress.jsonl")
    acquire_run.set_defaults(func=cmd_acquire)
    acquire_status = acquire_sub.add_parser("status", help="acquisition coverage")
    acquire_status.set_defaults(func=cmd_acquire)

    cat_import = sub.add_parser("catalog-import", help="import a reviewed JSONL manifest")
    cat_import.add_argument("manifest")
    cat_import.set_defaults(func=cmd_catalog_import)
    cat_list = sub.add_parser("catalog-list", help="list legacy catalog sources")
    cat_list.set_defaults(func=cmd_catalog_list)

    inspect = sub.add_parser("pdf-inspect", help="inspect a PDF without storing it")
    inspect.add_argument("pdf")
    inspect.add_argument("--max-pages", type=int, default=10_000)
    inspect.set_defaults(func=cmd_pdf_inspect)

    render = sub.add_parser("pdf-render", help="render PDF pages into the blob store")
    render.add_argument("source_id")
    render.add_argument("pdf")
    render.add_argument("--dpi", type=int, default=200)
    render.set_defaults(func=cmd_pdf_render)

    ingest = sub.add_parser("ingest", help="archive, render and index a PDF edition")
    ingest.add_argument("edition_id")
    ingest.add_argument("pdf")
    ingest.add_argument("--dpi", type=int, default=300)
    ingest.add_argument("--ocr", action="store_true",
                        help="OCR pages whose embedded text layer is missing or too short")
    ingest.add_argument("--provider", default=None, choices=["rapidocr", "tesseract"])
    ingest.set_defaults(func=cmd_ingest)

    batch = sub.add_parser(
        "ingest-batch", help="ingest every acquired edition in one resumable pass"
    )
    batch.add_argument("--dpi", type=int, default=200)
    batch.add_argument("--ocr", action="store_true")
    batch.add_argument("--provider", default=None, choices=["rapidocr", "tesseract"])
    batch.add_argument("--limit", type=int, default=None)
    batch.add_argument("--edition-id", default=None)
    batch.add_argument("--log", default=".aki/ingest-progress.jsonl")
    batch.set_defaults(func=cmd_ingest_batch)

    ocr_cmd = sub.add_parser("ocr", help="OCR engine status and page-level recognition")
    ocr_sub = ocr_cmd.add_subparsers(dest="action", required=True)
    ocr_providers = ocr_sub.add_parser("providers", help="report available OCR engines")
    ocr_providers.set_defaults(func=cmd_ocr, provider=None, edition=None, page=None)
    ocr_page = ocr_sub.add_parser("page", help="OCR one already-rendered page image")
    ocr_page.add_argument("--edition", required=True)
    ocr_page.add_argument("--page", type=int, required=True)
    ocr_page.add_argument("--provider", default=None)
    ocr_page.set_defaults(func=cmd_ocr)

    search_cmd = sub.add_parser("search", help="lexical full-text search")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--limit", type=int, default=20)
    search_cmd.add_argument("--mode", default="any", choices=["any", "all", "phrase"])
    search_cmd.add_argument("--work", default=None)
    search_cmd.add_argument("--edition", default=None)
    search_cmd.add_argument("--language", default=None)
    search_cmd.add_argument("--evidence-class", default=None)
    search_cmd.set_defaults(func=cmd_search)

    cite = sub.add_parser("cite", help="resolve an evidence id to a page locator")
    cite.add_argument("evidence_id")
    cite.set_defaults(func=cmd_cite)

    research = sub.add_parser("research", help="run a grounded research case")
    research.add_argument("question")
    research.add_argument("--limit", type=int, default=12)
    research.add_argument("--out-dir", default=".aki/dossiers")
    research.add_argument("--show-dossier", action="store_true")
    research.set_defaults(func=cmd_research)

    case = sub.add_parser("case", help="inspect and re-verify a research case")
    case.add_argument("case_id")
    case.set_defaults(func=cmd_case)

    cases = sub.add_parser("cases", help="list research cases")
    cases.add_argument("--limit", type=int, default=50)
    cases.set_defaults(func=cmd_cases)

    jobs = sub.add_parser("jobs", help="durable job control")
    jobs.add_argument("action", choices=["status", "recover", "run", "enqueue-ingest", "cancel"])
    jobs.add_argument("--max-jobs", type=int, default=10)
    jobs.add_argument("--capabilities", default=None)
    jobs.add_argument("--edition-id", default=None)
    jobs.add_argument("--pdf", default=None)
    jobs.add_argument("--dpi", type=int, default=300)
    jobs.add_argument("--job-id", default=None)
    jobs.add_argument("--limit", type=int, default=50)
    jobs.set_defaults(func=cmd_jobs)

    rebuild = sub.add_parser("index-rebuild", help="rebuild the derived search index")
    rebuild.set_defaults(func=cmd_index_rebuild)

    backup = sub.add_parser("backup", help="consistent database backup with verification")
    backup.add_argument("destination")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="restore from a verified backup")
    restore.add_argument("source")
    restore.add_argument("--confirm", action="store_true")
    restore.set_defaults(func=cmd_restore)

    # Accept --database/--blob-root after the subcommand as well as before it. The
    # SUPPRESS default keeps a value supplied at the root level from being overwritten.
    for subparser in _walk_subparsers(parser):
        subparser.add_argument("--database", default=argparse.SUPPRESS)
        subparser.add_argument("--blob-root", default=argparse.SUPPRESS)

    return parser


def _walk_subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public accessor
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for subparser in action.choices.values():
                yield subparser
                yield from _walk_subparsers(subparser)


def _force_utf8_streams() -> None:
    """Windows consoles default to a legacy code page; quoted source text needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - operator-facing failure surface
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
