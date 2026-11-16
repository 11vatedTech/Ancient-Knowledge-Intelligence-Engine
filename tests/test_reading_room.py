"""Image/EPUB ingestion, meaning search, concordance, plates and the reading room API."""
from __future__ import annotations

import json
import zipfile

import pytest

from aki.blob_store import ContentAddressedStore
from aki.concordance import build as build_concordance
from aki.concordance import entity_detail, find_mentions, overview
from aki.db import Database, initialize, utcnow
from aki.ingest import ingest_pdf
from aki.media import ingest_media_asset, is_epub, pending_media
from aki.plates import is_plate
from aki.semantic import LsaEmbedder, build_index, load_index, split_passages

pymupdf = pytest.importorskip("pymupdf")

PAGES = [
    "Enoch walked with God, and the Watchers descended upon Mount Hermon in the days of Jared."
    " Azazel taught men to make swords and mirrors, and the earth was filled with violence.",
    "Hermes Trismegistus spoke of the divine Mind, and Pimander answered him in a vision of light."
    " The Word proceeded from the Mind, and the Word was the Son of God.",
    "The Lodge is opened in the name of the Great Architect, and the brethren of Freemasonry"
    " stand to order before the altar of King Solomon's Temple at Jerusalem.",
]

ALL_OPERATIONS = ["store_original", "render_pages", "ocr_transcribe", "internal_search",
                  "internal_research", "create_embeddings", "vision_analyze"]


def _pdf(path):
    document = pymupdf.open()
    for text in PAGES:
        page = document.new_page(width=420, height=600)
        page.insert_textbox(pymupdf.Rect(40, 60, 380, 560), text, fontsize=11)
    page = document.new_page(width=420, height=600)
    page.draw_rect(pymupdf.Rect(60, 80, 360, 520), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    page.draw_circle(pymupdf.Point(210, 300), 90, color=(0, 0, 0), fill=(0.55, 0.55, 0.55))
    document.save(str(path))
    return path


def _seed(database, edition_id, title, *, url, category="Ancient Texts", grant=True):
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO works (id, canonical_title, normalized_title, work_class, metadata,"
            " created_at) VALUES (?,?,?,'unclassified','{}',?)",
            ("w-" + edition_id, title, title.lower(), utcnow()))
        connection.execute(
            "INSERT INTO source_records (id, source_name, record_url, display_title,"
            " category_labels, discovered_at, discovery_method, record_status, metadata, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("s-" + edition_id, "theholyword.net", url, title, json.dumps([category]), utcnow(),
             "test", "metadata_captured",
             json.dumps({"author_as_listed": "Tester", "description_as_listed": "Joe's book"}),
             utcnow()))
        connection.execute(
            "INSERT INTO editions (id, work_id, source_record_id, edition_title, edition_type,"
            " metadata, created_at) VALUES (?,?,?,?,'unknown','{}',?)",
            (edition_id, "w-" + edition_id, "s-" + edition_id, title, utcnow()))
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("rd-" + edition_id, edition_id, "cleared_limited" if grant else "review_required",
             json.dumps({"permitted_operations": ALL_OPERATIONS if grant else []}), "test",
             utcnow()))


@pytest.fixture()
def corpus(tmp_path):
    database = Database(tmp_path / "aki.sqlite3")
    initialize(database.path)
    blobs = tmp_path / "blobs"
    _seed(database, "e1", "Fixture of the Watchers", url="https://theholyword.net/bible/b1")
    ingest_pdf(database, blobs, _pdf(tmp_path / "f.pdf"), edition_id="e1", dpi=72)
    return database, blobs, tmp_path


# ------------------------------------------------------------------ media
def _register_asset(database, blobs, edition_id, data, mime):
    stored = ContentAddressedStore(blobs).put_bytes(data)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256,"
            " byte_size, detected_mime, asset_role, preflight_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,'original_scan','passed',?)",
            ("a-" + edition_id, edition_id, "rd-" + edition_id,
             stored.path.relative_to(ContentAddressedStore(blobs).root).as_posix(),
             stored.sha256, stored.size_bytes, mime, utcnow()))


class _FakeOcr:
    name = "fake"

    def ocr(self, image_path):
        from aki.ocr import OcrPage

        return OcrPage(text="Bel and the Dragon, recognised", engine_name="FakeOCR",
                       engine_version="1", confidence=0.9, lines=())


def test_image_asset_becomes_one_ocr_page(corpus):
    database, blobs, _ = corpus
    _seed(database, "e2", "Bel and the Dragon", url="https://theholyword.net/bible/b2")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pixmap.clear_with(200)
    _register_asset(database, blobs, "e2", pixmap.tobytes("png"), "image/png")
    pending = pending_media(database)
    assert [a["edition_id"] for a in pending] == ["e2"]
    result = ingest_media_asset(database, blobs, pending[0], ocr_provider=_FakeOcr())
    assert (result.pages, result.pages_ocr) == (1, 1)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT p.page_image_key, a.storage_key, t.engine_name FROM pages p"
            " JOIN assets a ON a.id=p.asset_id JOIN transcriptions t ON t.page_id=p.id"
            " WHERE a.edition_id='e2'").fetchone()
    assert row["page_image_key"] == row["storage_key"]  # the original image, not a re-encode
    assert row["engine_name"] == "FakeOCR"
    assert pending_media(database) == []


def test_epub_asset_is_laid_out_and_transcribed(corpus, tmp_path):
    database, blobs, _ = corpus
    _seed(database, "e3", "Tablets", url="https://theholyword.net/bible/b3")
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
                         '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:'
                         'opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/c.opf"'
                         ' media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr("OPS/c.opf",
                         '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"'
                         ' unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                         '<dc:title>T</dc:title><dc:identifier id="id">x</dc:identifier></metadata>'
                         '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                         '</manifest><spine><itemref idref="c1"/></spine></package>')
        archive.writestr("OPS/c1.xhtml",
                         '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Thoth spoke the'
                         ' tablets of light.</p></body></html>')
    assert is_epub(epub)
    _register_asset(database, blobs, "e3", epub.read_bytes(), "application/zip")
    asset = pending_media(database, blobs)[0]
    result = ingest_media_asset(database, blobs, asset)
    assert result.kind == "epub" and result.pages >= 1 and result.pages_with_text >= 1
    assert pending_media(database, blobs) == []  # complete, so not offered again
    again = ingest_media_asset(database, blobs, asset)  # a re-run resumes: nothing to add
    assert again.pages == 0


def test_media_refused_without_rights(corpus):
    database, blobs, _ = corpus
    _seed(database, "e4", "Denied", url="https://theholyword.net/bible/b4", grant=False)
    _register_asset(database, blobs, "e4", b"\x89PNG fake", "image/png")
    with pytest.raises(PermissionError):
        ingest_media_asset(database, blobs, pending_media(database)[0])


# ------------------------------------------------------------------ semantic
def test_split_passages_overlap_and_skip_noise():
    text = ("word " * 400).strip()
    spans = split_passages(text, size=200, overlap=50)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(b[0] < a[1] for a, b in zip(spans, spans[1:]))
    assert split_passages("12 34 56 .. --") == []


def test_lsa_meaning_index_finds_paraphrase(corpus):
    database, _, root = corpus
    meta = build_index(database, root, embedder=LsaEmbedder(dimensions=8))
    assert meta["provider"] == "lsa" and meta["pages"] == 3
    hits = load_index(root).query("Freemasonry lodge brethren altar", limit=3)
    with database.connect() as connection:
        top = connection.execute("SELECT text FROM chunk_records WHERE id=?",
                                 (hits[0].chunk_id,)).fetchone()["text"]
    assert "Lodge" in top
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM model_runs WHERE task_type='create_embeddings'").fetchone()[0] == 1


# ------------------------------------------------------------------ concordance
def test_find_mentions_is_case_sensitive_and_prefers_longest():
    found = find_mentions("Hermes Trismegistus wrote; mercury the metal; Mercurius the god.")
    from aki.concordance_terms import TERMS

    names = {TERMS[i][0] for i in found}
    assert "Hermes Trismegistus" in names
    assert "Hermes" in names  # via "Mercurius"
    assert not any(TERMS[i][0] == "Hermes" and alias == "Hermes" for i, (alias, _) in found.items())


def test_concordance_build_links_mentions_to_citable_pages(corpus):
    database, _, _ = corpus
    report = build_concordance(database)
    assert report["mentions"] > 0
    entries = {e["canonical_name"]: e for e in overview(database)}
    assert entries["Enoch"]["pages"] == 1 and entries["Azazel"]["pages"] == 1
    detail = entity_detail(database, entries["Freemasonry"]["id"])
    assert detail["passages"][0]["evidence_id"]
    assert "Freemasonry" in detail["passages"][0]["excerpt"]
    assert build_concordance(database)["mentions"] == report["mentions"]  # rebuild is idempotent


# ------------------------------------------------------------------ plates
def test_plate_rule():
    assert is_plate(10, {"ink": 0.3, "midtone": 0.2, "paper": 240})
    assert not is_plate(10, {"ink": 0.01, "midtone": 0.0, "paper": 240})  # blank leaf
    assert not is_plate(900, {"ink": 0.3, "midtone": 0.2, "paper": 240})  # a page of prose


def test_plate_detection_finds_drawn_page(corpus):
    from aki.plates import detect

    database, blobs, _ = corpus
    report = detect(database, blobs)
    assert report["plates"] == 1
    assert detect(database, blobs)["examined"] == 0  # already examined pages are skipped


# ------------------------------------------------------------------ server
def test_reading_room_refuses_non_loopback():
    from aki.app.server import BindRefused, require_loopback

    require_loopback("127.0.0.1")
    require_loopback("localhost")
    with pytest.raises(BindRefused):
        require_loopback("0.0.0.0")


def test_reading_room_api(corpus):
    from fastapi.testclient import TestClient

    from aki.app.api import create_app

    database, blobs, root = corpus
    _seed(database, "e9", "Hidden", url="https://theholyword.net/bible/b9", grant=False)
    build_index(database, root, embedder=LsaEmbedder(dimensions=8))
    build_concordance(database)
    client = TestClient(create_app(database.path, blobs, root))

    books = client.get("/api/books").json()
    assert [b["id"] for b in books] == ["e1"]  # the unpermitted edition is not shown
    assert books[0]["joes"] and books[0]["category"] == "Ancient Texts"
    book = client.get("/api/books/e1").json()
    assert len(book["page_list"]) == 4
    page = client.get(f"/api/pages/{book['page_list'][0]['id']}").json()
    assert "Watchers" in page["text"] and page["citation"].startswith("Tester, ")
    image = client.get(f"/api/pages/{page['id']}/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert client.get(f"/api/pages/{page['id']}/thumb?w=160").headers["content-type"] == "image/jpeg"

    words = client.get("/api/search", params={"q": "the Watchers", "mode": "words"}).json()
    assert words["results"][0]["page_id"] == page["id"]
    assert "<mark>Watchers</mark>" in words["results"][0]["snippet"]
    both = client.get("/api/search", params={"q": "masonic lodge brethren", "mode": "both"}).json()
    assert both["results"] and "meaning" in both["results"][0]["found_by"]

    research = client.post("/api/research", json={"question": "What did Azazel teach?"}).json()
    assert research["ok"] and research["findings"][0]["status"] == "supported"
    assert "what" not in research["terms"]

    assert client.get("/api/index").json()
    assert client.get("/api/archive").json()["integrity"]["quick_check"] == "ok"
    assert client.get("/api/pages/nope").status_code == 404
    assert client.get("/").status_code == 200
    assert "Content-Security-Policy" in client.get("/").headers


def test_margin_chart_and_notes(corpus, tmp_path):
    """Side-notes, the chart of shared pages and curator's notes, with their rights limits."""
    from fastapi.testclient import TestClient

    from aki.app.api import create_app
    from aki.app.server import context_note

    database, blobs, root = corpus
    # A second permitted book whose first page reads like the fixture's first page.
    _seed(database, "e3", "The Protocols of the Learned Elders of Zion",
          url="https://theholyword.net/bible/b3")
    document = pymupdf.open()
    page = document.new_page(width=420, height=600)
    page.insert_textbox(pymupdf.Rect(40, 60, 380, 560),
                        "The Watchers descended upon Mount Hermon, and Azazel taught men to make"
                        " swords; Enoch walked with God in the days of Jared.", fontsize=11)
    document.save(str(tmp_path / "g.pdf"))
    ingest_pdf(database, blobs, tmp_path / "g.pdf", edition_id="e3", dpi=72)
    build_index(database, root, embedder=LsaEmbedder(dimensions=8))
    build_concordance(database)
    client = TestClient(create_app(database.path, blobs, root))

    first = client.get("/api/books/e1").json()
    gloss = client.get(f"/api/pages/{first['page_list'][0]['id']}/gloss").json()
    assert {"Enoch", "Azazel"} <= {n["canonical_name"] for n in gloss["names"]}
    assert gloss["meaning"] and gloss["elsewhere"]
    assert all(e["book"]["id"] != "e1" for e in gloss["elsewhere"])  # other books only
    assert gloss["elsewhere"][0]["book"]["id"] == "e3"
    assert client.get("/api/pages/nope/gloss").status_code == 404
    assert first["names"] and first["note"] is None

    chart = client.get("/api/chart").json()
    names = {n["id"]: n["name"] for n in chart["nodes"]}
    assert "Enoch" in names.values() and all(n["pages"] > 0 for n in chart["nodes"])
    assert chart["links"] and all(link["a"] in names and link["b"] in names for link in chart["links"])
    pairs = {frozenset((names[link["a"]], names[link["b"]])) for link in chart["links"]}
    assert frozenset(("Enoch", "Azazel")) in pairs  # they share both books' first pages

    forged = client.get("/api/books/e3").json()
    assert "forgery" in forged["note"]
    assert context_note("The Book of Enoch") is None

    for name in ("app.js", "light.js", "art.js", "chart.js", "app.css", "fonts/EBGaramond.woff2",
                 "fonts/OFL-IMFell.txt"):
        assert client.get(f"/{name}").status_code == 200, name


def test_index_derived_views_follow_a_narrowed_grant(corpus, tmp_path):
    """Withdrawing research rights from one book removes it from every index-derived view,
    at once, with the server still running and its chart already cached."""
    from fastapi.testclient import TestClient

    from aki.app.api import create_app

    database, blobs, root = corpus
    _seed(database, "e3", "Second Book of the Watchers", url="https://theholyword.net/bible/b3")
    document = pymupdf.open()
    page = document.new_page(width=420, height=600)
    page.insert_textbox(pymupdf.Rect(40, 60, 380, 560),
                        "The Watchers descended upon Mount Hermon, and Azazel taught men to make"
                        " swords; Enoch walked with God in the days of Jared.", fontsize=11)
    document.save(str(tmp_path / "g.pdf"))
    ingest_pdf(database, blobs, tmp_path / "g.pdf", edition_id="e3", dpi=72)
    build_index(database, root, embedder=LsaEmbedder(dimensions=8))
    build_concordance(database)
    client = TestClient(create_app(database.path, blobs, root))

    def enoch(listing):
        return next(e for e in listing if (e.get("name") or e.get("canonical_name")) == "Enoch")

    before = enoch(client.get("/api/chart").json()["nodes"])
    assert (before["pages"], before["books"]) == (2, 2)
    entry_id = before["id"]
    assert client.get(f"/api/index/{entry_id}").json()["book_count"] == 2
    second = client.get("/api/books/e3").json()
    assert second["names"]

    narrowed = [op for op in ALL_OPERATIONS if op != "internal_research"]
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd-narrow', 'e3', 'cleared_limited', ?, 'narrowed', '2999-01-01T00:00:00Z')",
            (json.dumps({"permitted_operations": narrowed}),))

    after = enoch(client.get("/api/chart").json()["nodes"])  # the warm cache must not survive
    assert (after["pages"], after["books"]) == (1, 1)
    assert enoch(client.get("/api/index").json())["pages"] == 1
    entry = client.get(f"/api/index/{entry_id}").json()
    assert entry["pages"] == 1 and entry["book_count"] == 1
    assert {p["edition_id"] for p in entry["passages"]} == {"e1"}
    assert client.get("/api/books/e3").json()["names"] == []
    gloss = client.get(f"/api/pages/{second['page_list'][0]['id']}/gloss").json()
    assert gloss["names"] == []  # still readable and searchable, but no longer indexed
    research = client.post("/api/research", json={"question": "What did Azazel teach men?"}).json()
    assert {f["book"]["id"] for f in research["findings"]} == {"e1"}
    assert "dossier" not in research and research["verified"] == len(
        [f for f in research["findings"] if f["status"] == "supported"])

    # Without the embedding grant a book no longer appears in another book's margin.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd-narrow2', 'e3', 'cleared_limited', ?, 'narrowed', '2999-02-01T00:00:00Z')",
            (json.dumps({"permitted_operations":
                         [op for op in narrowed if op != "create_embeddings"]}),))
    first = client.get("/api/books/e1").json()
    margin = client.get(f"/api/pages/{first['page_list'][0]['id']}/gloss").json()
    assert all(e["book"]["id"] != "e3" for e in margin["elsewhere"])
