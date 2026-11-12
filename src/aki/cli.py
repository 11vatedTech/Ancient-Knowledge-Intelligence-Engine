"""Operator CLI for catalog and document ingestion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .blob_store import ContentAddressedStore
from .catalog import SourceCatalog
from .domain import ProcessingOperation
from .manifest import read_manifest
from .pdf import PDFPageProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aki", description="Ancient Knowledge Intelligence Engine")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a local catalog")
    init.add_argument("--database", default=".aki/catalog.sqlite3")

    ingest = sub.add_parser("catalog-import", help="import a reviewed JSONL source manifest")
    ingest.add_argument("manifest")
    ingest.add_argument("--database", default=".aki/catalog.sqlite3")

    listing = sub.add_parser("catalog-list", help="list cataloged sources")
    listing.add_argument("--database", default=".aki/catalog.sqlite3")

    inspect = sub.add_parser("pdf-inspect", help="inspect a local PDF without rendering or storing it")
    inspect.add_argument("pdf")
    inspect.add_argument("--max-pages", type=int, default=10_000)

    render = sub.add_parser("pdf-render", help="render PDF pages into the immutable blob store")
    render.add_argument("source_id")
    render.add_argument("pdf")
    render.add_argument("--database", default=".aki/catalog.sqlite3")
    render.add_argument("--blob-root", default=".aki/blobs")
    render.add_argument("--dpi", type=int, default=200)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "pdf-inspect":
        result = PDFPageProcessor(max_pages=args.max_pages).inspect(args.pdf)
        print(json.dumps({
            "source_pdf_sha256": result.source_pdf_sha256,
            "file_size_bytes": result.file_size_bytes,
            "page_count": result.page_count,
            "metadata": dict(result.metadata),
        }, ensure_ascii=False, indent=2))
        return 0

    catalog = SourceCatalog(Path(args.database))
    if args.command == "init":
        print(json.dumps({"status": "initialized", "database": str(Path(args.database).resolve())}))
    elif args.command == "catalog-import":
        added, duplicates = catalog.import_records(read_manifest(args.manifest))
        print(json.dumps({"added": added, "duplicates": duplicates, "total": len(catalog.list_sources())}))
    elif args.command == "catalog-list":
        print(json.dumps([
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
        ], ensure_ascii=False, indent=2))
    elif args.command == "pdf-render":
        source = catalog.authorize(args.source_id, ProcessingOperation.STORE)
        pages = PDFPageProcessor().render_to_store(
            args.pdf, source.source_id, source.rights,
            ContentAddressedStore(args.blob_root), dpi=args.dpi,
        )
        print(json.dumps([page.to_dict() for page in pages], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
