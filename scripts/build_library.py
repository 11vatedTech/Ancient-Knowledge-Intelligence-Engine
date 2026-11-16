"""Compatibility wrapper: use ``aki library-export`` instead.

Kept so older notes that call this script still work. It now goes through the same
rights check and audit log as the CLI command.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aki.db import Database  # noqa: E402
from aki.home import resolve_home  # noqa: E402
from aki.library_export import export_library  # noqa: E402

home = resolve_home(ROOT)
budget = float(sys.argv[1]) if len(sys.argv) > 1 else None
report = export_library(
    Database(home / "aki.sqlite3"), home / "blobs", home / "Library",
    snapshot=ROOT / "data/holyword/books_raw.json", budget_seconds=budget,
)
print(json.dumps({k: v for k, v in report.items() if k != "skipped_without_store_original"}, indent=2))
