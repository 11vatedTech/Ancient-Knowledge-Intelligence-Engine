"""Operator CLI for catalog initialization and reviewed manifest ingestion."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from . import __version__
from .catalog import SourceCatalog
from .manifest import read_manifest


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = SourceCatalog(Path(args.database))
    if args.command == "init":
        print(json.dumps({"status": "initialized", "database": str(Path(args.database).resolve())}))
    elif args.command == "catalog-import":
        added, duplicates = catalog.import_records(read_manifest(args.manifest))
        print(json.dumps({"added": added, "duplicates": duplicates, "total": len(catalog.list_sources())}))
    elif args.command == "catalog-list":
        print(json.dumps([
            {"source_id": s.source_id, "title": s.title, "creator": s.creator,
             "language": s.language, "source_uri": s.source_uri,
             "collection": s.collection, "edition_label": s.edition_label,
             "rights_status": s.rights.status.value,
             "permitted_operations": sorted(op.value for op in s.rights.permitted_operations)}
            for s in catalog.list_sources()
        ], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
