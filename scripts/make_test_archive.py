"""Build a small synthetic AKI archive for the launcher tests. Never touches a real archive.

    python make_test_archive.py <project folder> [--long-pages N] [--bad-book]

Creates <project>/.aki (database + vault) and <project>/data/holyword/books_raw.json with:
a short text PDF with a drawn plate, a long text PDF (for interruption tests), a PNG scan,
an EPUB, and optionally an encrypted PDF that ingestion must reject. Every edition carries
a rights decision permitting the operations the real attestation grants.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

GRANTED = ["acquire", "store_original", "render_pages", "ocr_transcribe", "vision_analyze",
           "create_embeddings", "internal_search", "internal_research"]


def _pdf(pages: list[str], *, plate: bool = False, encrypt: bool = False) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page(width=420, height=600)
        page.insert_textbox(pymupdf.Rect(40, 60, 380, 560), text, fontsize=11)
    if plate:
        page = document.new_page(width=420, height=600)
        page.draw_rect(pymupdf.Rect(60, 80, 360, 520), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    if encrypt:
        return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    return document.tobytes()


def _png() -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_textbox(pymupdf.Rect(20, 20, 280, 380), "Bel and the Dragon. Daniel and the priests of Bel.",
                        fontsize=16)
    return page.get_pixmap(dpi=100).tobytes("png")


def _epub() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
                         '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:'
                         'opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/c.opf"'
                         ' media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr("OPS/c.opf",
                         '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                         'version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/'
                         'dc/elements/1.1/"><dc:title>T</dc:title><dc:identifier id="id">x'
                         '</dc:identifier></metadata><manifest><item id="c1" href="c1.xhtml" '
                         'media-type="application/xhtml+xml"/></manifest><spine><itemref '
                         'idref="c1"/></spine></package>')
        archive.writestr("OPS/c1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                         + "<p>Thoth spoke the tablets of light.</p>" * 40 + "</body></html>")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--long-pages", type=int, default=12)
    parser.add_argument("--bad-book", action="store_true")
    args = parser.parse_args()

    from aki.blob_store import ContentAddressedStore
    from aki.db import Database, initialize, utcnow

    project = Path(args.project)
    home = project / ".aki"
    if (home / "aki.sqlite3").exists():
        print(f"refusing: {home} already holds an archive", file=sys.stderr)
        return 1
    database = Database(home / "aki.sqlite3")
    initialize(database.path)
    store = ContentAddressedStore(home / "blobs")
    words = ("Enoch walked with God and the Watchers descended upon Hermon. Hermes Trismegistus "
             "and Pimander spoke of the Mind. The Lodge of Freemasonry met at the Temple. ")
    books = [
        ("t1", "Test Book of Watchers", "Ancient Texts", "application/pdf",
         _pdf([words * 3, words * 2], plate=True)),
        ("t2", "Test Long Book", "Bibles", "application/pdf",
         _pdf([f"Page {i + 1}. " + words * 4 for i in range(args.long_pages)])),
        ("t3", "Test Bel and the Dragon scan", "Bibles", "image/png", _png()),
        ("t4", "Test Tablets EPUB", "Magic", "application/zip", _epub()),
    ]
    if args.bad_book:
        books.append(("t5", "Test Locked Book", "Apothecary", "application/pdf",
                      _pdf([words], encrypt=True)))
    snapshot = []
    with database.transaction() as connection:
        for key, title, shelf, mime, data in books:
            blob = store.put_bytes(data)
            storage_key = blob.path.relative_to(store.root).as_posix()
            edition = f"edt-test-{key}"
            connection.execute(
                "INSERT INTO works (id, canonical_title, normalized_title, work_class, metadata,"
                " created_at) VALUES (?,?,?,'unclassified','{}',?)",
                (f"wrk-{key}", title, title.lower(), utcnow()))
            connection.execute(
                "INSERT INTO source_records (id, source_name, record_url, display_title,"
                " category_labels, discovered_at, discovery_method, record_status, metadata,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"src-{key}", "test", f"https://example.invalid/bible/{key}", title,
                 json.dumps([shelf]), utcnow(), "launcher test fixture", "metadata_captured",
                 json.dumps({"author_as_listed": "Fixture"}), utcnow()))
            connection.execute(
                "INSERT INTO editions (id, work_id, source_record_id, edition_title, edition_type,"
                " metadata, created_at) VALUES (?,?,?,?,'unknown','{}',?)",
                (edition, f"wrk-{key}", f"src-{key}", title, utcnow()))
            connection.execute(
                "INSERT INTO rights_decisions (id, edition_id, state, scope, license_name, notes,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (f"rd-{key}", edition, "cleared_limited",
                 json.dumps({"permitted_operations": GRANTED}), "Launcher test fixture",
                 "synthetic", utcnow()))
            connection.execute(
                "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256,"
                " byte_size, detected_mime, original_filename, asset_role, preflight_status,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,'original_scan','passed',?)",
                (f"ast-{key}", edition, f"rd-{key}", storage_key, blob.sha256, blob.size_bytes,
                 mime, title, utcnow()))
            snapshot.append({"id": key, "title": title, "category": shelf, "author": "Fixture"})
    target = project / "data" / "holyword" / "books_raw.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    print(json.dumps({"home": str(home), "books": len(books)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
