"""HTTP routes for the reading room."""
from __future__ import annotations

import html
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..db import Database, utcnow
from ..gate import permitted_editions
from ..search import SearchError, reciprocal_rank_fusion, search
from .server import STATIC, Library, context_note, memorial

_MARK = re.compile(r"\[([^\[\]]{1,200})\]")


def create_app(database_path: str | Path, blob_root: str | Path, state_root: str | Path,
               *, writer: Any = None) -> Any:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    database = Database(database_path)
    database.require_current()
    library = Library(database, Path(blob_root), Path(state_root))
    answer_lock = threading.Lock()  # one answer at a time: the GPU holds one model well

    def answer_writer() -> Any:
        from ..answer import OllamaWriter

        return writer if writer is not None else OllamaWriter()
    app = FastAPI(title="AKI reading room", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
            " script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def research_ok() -> set[str]:
        return permitted_editions(database, "internal_research")

    def index_editions() -> set[str]:
        """Editions whose index-derived facts may be shown: searchable and researchable."""
        return research_ok() & set(library.editions())

    chart_cache: dict[str, Any] = {}
    integrity_cache: dict[str, Any] = {}

    def checked_integrity() -> dict[str, Any]:
        """The database check, at most once every ten minutes.

        ``quick_check`` reads the whole file: seven seconds on the full corpus, which made
        the colophon slow to open every time. The answer says when it was taken.
        """
        if not integrity_cache or time.time() - integrity_cache["at"] > 600:
            result = database.integrity_check()
            integrity_cache.update(at=time.time(), result={**result, "checked_at": utcnow()})
        return dict(integrity_cache["result"])

    def concordance_stamp(allowed: set[str]) -> tuple[Any, ...]:
        with database.connect() as connection:
            built = connection.execute(
                "SELECT MAX(completed_at) FROM model_runs WHERE task_type = 'concordance'"
            ).fetchone()[0]
            total = int(connection.execute("SELECT COUNT(*) FROM mentions").fetchone()[0])
        return (total, built, frozenset(allowed))

    counts_cache: dict[str, Any] = {}

    def entity_counts(allowed: set[str]) -> dict[str, tuple[int, int]]:
        """Index counts for the permitted editions, recomputed only when they could change."""
        stamp = concordance_stamp(allowed)
        if counts_cache.get("stamp") != stamp:
            counts_cache.clear()
            counts_cache.update(stamp=stamp, counts=_entity_counts(database, allowed))
        return dict(counts_cache["counts"])

    # ------------------------------------------------------------ catalog
    @app.get("/api/overview")
    def overview() -> Any:
        from ..concordance import status as concordance_status
        from ..semantic import index_status

        data = library.overview()
        data["semantic"] = index_status(state_root)
        data["concordance"] = concordance_status(database)
        data["joes_books"] = sorted(
            (e for e in library.editions().values() if e["joes"]),
            key=lambda e: -e["downloads"],
        )
        return data

    @app.get("/api/memorial")
    def memorial_route() -> Any:
        return memorial()

    @app.get("/api/books")
    def books() -> Any:
        return sorted(library.editions().values(), key=lambda e: e["title"].lower())

    @app.get("/api/books/{edition_id}")
    def book(edition_id: str) -> Any:
        edition = library.editions().get(edition_id)
        if edition is None:
            raise HTTPException(404, "This book is not in the library.")
        return {**edition, "page_list": library.page_list(edition_id),
                "names": _book_names(database, edition_id) if edition_id in research_ok() else [],
                "note": context_note(edition["listed_title"])}

    @app.get("/api/pages/{page_id}")
    def page(page_id: str) -> Any:
        data = library.page(page_id)
        if data is None:
            raise HTTPException(404, "This page is not in the library.")
        return data

    @app.get("/api/pages/{page_id}/gloss")
    def page_gloss(page_id: str) -> Any:
        """The margin of a page: the index names on it, and near passages in other books."""
        data = library.page(page_id)
        if data is None:
            raise HTTPException(404, "This page is not in the library.")
        editions = library.editions()
        own_edition = data["edition"]["id"]
        with database.connect() as connection:
            names = connection.execute(
                "SELECT en.id, en.canonical_name, en.entity_type FROM mentions m"
                " JOIN evidence ev ON ev.id = m.evidence_id"
                " JOIN entities en ON en.id = m.entity_id"
                " WHERE ev.page_id = ? GROUP BY en.id ORDER BY en.canonical_name", (page_id,),
            ).fetchall() if own_edition in research_ok() else []
            chunk = connection.execute(
                "SELECT id FROM chunk_records WHERE page_id = ? LIMIT 1", (page_id,)).fetchone()
        elsewhere: list[dict[str, Any]] = []
        meaning = False
        if chunk is not None:
            from ..semantic import SemanticUnavailable, load_index

            try:
                matches = load_index(state_root).neighbours(chunk["id"], limit=400)
                meaning = True
            except SemanticUnavailable:
                matches = []
            rows = _chunk_rows(database, [m.chunk_id for m in matches])
            embeddable = permitted_editions(database, "create_embeddings")
            unsure = _unsure_pages(database, [row["page_id"] for row in rows.values()])
            seen = {own_edition}
            for match in matches:
                row = rows.get(match.chunk_id)
                if row is None or row["edition_id"] in seen or row["edition_id"] not in editions:
                    continue
                if row["edition_id"] not in embeddable or row["page_id"] in unsure:
                    continue
                seen.add(row["edition_id"])
                passage = " ".join(str(row["text"])[match.start:match.end].split())
                elsewhere.append({
                    "page_id": row["page_id"], "page_index": int(row["page_index"]),
                    "page_label": row["printed_page_label"] or str(row["page_index"] + 1),
                    "book": {"id": row["edition_id"], "title": editions[row["edition_id"]]["title"],
                             "noted": editions[row["edition_id"]]["noted"]},
                    "excerpt": _trim(passage, 260), "score": round(match.score, 3),
                })
                if len(elsewhere) >= 5:
                    break
        return {"names": [dict(n) for n in names], "elsewhere": elsewhere, "meaning": meaning}

    @app.get("/api/pages/{page_id}/image")
    def page_image(page_id: str) -> Any:
        path = library.image_path(page_id)
        if path is None:
            raise HTTPException(404, "No image for this page.")
        with path.open("rb") as handle:
            head = handle.read(4)
        media = "image/png" if head.startswith(b"\x89PNG") else "image/jpeg"
        return FileResponse(path, media_type=media,
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/pages/{page_id}/thumb")
    def page_thumb(page_id: str, w: int = 320) -> Any:
        path = library.thumbnail(page_id, w)
        if path is None:
            raise HTTPException(404, "No image for this page.")
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    # ------------------------------------------------------------ search
    @app.get("/api/search")
    def search_route(
        q: str = Query(..., min_length=1, max_length=300),
        mode: str = "both",
        category: str | None = None,
        book: str | None = None,
        limit: int = Query(30, ge=1, le=100),
    ) -> Any:
        editions = library.editions()

        def keep(edition_id: str) -> bool:
            edition = editions.get(edition_id)
            if edition is None:
                return False
            if book and edition_id != book:
                return False
            return not (category and edition["category"] != category)

        legs: dict[str, list[str]] = {}
        details: dict[str, dict[str, Any]] = {}
        notes: list[str] = []
        if mode in ("words", "both"):
            hits = _word_hits(database, q, limit * 4)
            kept = [hit for hit in hits if keep(hit.edition_id)][: limit * 2]
            legs["words"] = [hit.chunk_id for hit in kept]
            for hit in kept:
                details[hit.chunk_id] = {
                    "chunk_id": hit.chunk_id, "evidence_id": hit.evidence_id,
                    "page_id": hit.page_id, "page_index": hit.page_index,
                    "page_label": hit.page_label or str(hit.page_index + 1),
                    "edition_id": hit.edition_id, "snippet": _marked(hit.snippet),
                }
        if mode in ("meaning", "both"):
            from ..semantic import SemanticUnavailable, load_index

            try:
                index = load_index(state_root)
                matches = index.query(q, limit=limit * 4)
            except SemanticUnavailable as exc:
                matches = []
                notes.append(str(exc))
            matched_ids = [m.chunk_id for m in matches]
            rows = _chunk_rows(database, matched_ids)
            kept_ids: list[str] = []
            for match in matches:
                row = rows.get(match.chunk_id)
                if row is None or not keep(row["edition_id"]):
                    continue
                kept_ids.append(match.chunk_id)
                if match.chunk_id not in details:
                    passage = " ".join(str(row["text"])[match.start:match.end].split())
                    details[match.chunk_id] = {
                        "chunk_id": match.chunk_id, "evidence_id": row["evidence_id"],
                        "page_id": row["page_id"], "page_index": int(row["page_index"]),
                        "page_label": row["printed_page_label"] or str(row["page_index"] + 1),
                        "edition_id": row["edition_id"],
                        "snippet": html.escape(_trim(passage, 320)),
                    }
                if len(kept_ids) >= limit * 2:
                    break
            legs["meaning"] = kept_ids
        fused = reciprocal_rank_fusion({k: v for k, v in legs.items() if v})
        results = []
        for chunk_id, score in fused[:limit]:
            item = details[chunk_id]
            edition = editions[item["edition_id"]]
            results.append({
                **item, "score": round(score, 5),
                "found_by": [leg for leg, ids in legs.items() if chunk_id in ids],
                "book": {key: edition[key] for key in ("id", "title", "author", "category", "noted")},
            })
        return {"query": q, "mode": mode, "results": results, "notes": notes,
                "legs": {leg: len(ids) for leg, ids in legs.items()}}

    # ------------------------------------------------------------ research
    @app.post("/api/research")
    def research(payload: dict[str, Any]) -> Any:
        from ..research import ResearchError, run_research

        question = str(payload.get("question", "")).strip()
        if len(question) > 400:
            raise HTTPException(400, "Keep the question under 400 characters.")
        try:
            result = run_research(database, question, limit=int(payload.get("limit", 12)))
        except ResearchError as exc:
            raise HTTPException(400, str(exc)) from exc
        research_allowed = permitted_editions(database, "internal_research")
        editions = {k: v for k, v in library.editions().items() if k in research_allowed}
        status_by_page: dict[tuple[str, int], str] = {}
        for assessment in result.verification.claims:
            for citation in assessment.citations:
                status_by_page[(citation.edition_title, citation.page_index)] = assessment.status
        findings = []
        for hit in result.hits:
            if hit.edition_id not in editions:
                continue
            findings.append({
                "page_id": hit.page_id, "evidence_id": hit.evidence_id,
                "page_label": hit.page_label or str(hit.page_index + 1),
                "snippet": _marked(hit.snippet),
                "status": status_by_page.get((hit.edition_title, hit.page_index), "not_claimed"),
                "book": {k: editions[hit.edition_id][k]
                         for k in ("id", "title", "author", "category", "noted")},
            })
        report = result.verification.to_dict()
        return {
            "case_id": result.case_id, "question": question, "terms": list(result.plan.terms),
            "subquestions": list(result.plan.subquestions), "findings": findings,
            "verified": sum(1 for f in findings if f["status"] == "supported"),
            "unverified": report["unverified_claims"],
            "contested": report["contested_claims"], "ok": report["ok"],
        }

    # ------------------------------------------------------------ cited answers
    @app.get("/api/answer/models")
    def answer_models() -> Any:
        from ..answer import WriterUnavailable, model_choices

        try:
            status = answer_writer().status()
        except WriterUnavailable as exc:
            status = {"running": False, "models": {}, "reason": str(exc)}
        installed = status.get("models", {})
        return {
            "running": bool(status.get("running")), "reason": status.get("reason"),
            "models": [{"depth": c.depth, "model": c.model, "label": c.label, "note": c.note,
                        "installed": c.model in installed}
                       for c in model_choices().values()],
        }

    @app.post("/api/answer")
    def answer_route(payload: dict[str, Any]) -> Any:
        """Gather, write, check: streamed as one JSON object per line.

        Only progress is streamed. The model's draft is never shown before it is checked.
        """
        from ..answer import answer_question, iter_events

        question = str(payload.get("question", "")).strip()
        depth = str(payload.get("depth", "quick"))
        if len(question) > 400:
            raise HTTPException(400, "Keep the question under 400 characters.")
        if answer_lock.locked():
            raise HTTPException(409, "Another answer is being written. Wait for it to finish.")
        editions = library.editions()
        research_editions = research_ok() & set(editions)
        meaning = None
        try:
            from ..semantic import SemanticUnavailable, load_index

            index = load_index(state_root)
            meaning = lambda text, n: index.query(text, limit=n)  # noqa: E731
        except (SemanticUnavailable, ImportError):
            meaning = None

        def run(emit: Any) -> Any:
            from ..answer import AnswerError

            # Held by the worker, so it is released even if the reader closes the page.
            if not answer_lock.acquire(blocking=False):
                raise AnswerError("Another answer is being written. Wait for it to finish.")
            try:
                return answer_question(
                    database, question, depth=depth, writer=answer_writer(),
                    research_editions=research_editions,
                    embedding_editions=permitted_editions(database, "create_embeddings"),
                    titles={k: v["title"] for k, v in editions.items()},
                    notes={k: context_note(v["listed_title"]) for k, v in editions.items()},
                    meaning=meaning, on_event=emit)
            finally:
                answer_lock.release()

        def stream() -> Any:
            for event in iter_events(run):
                if event.get("type") == "answer":
                    event = _answer_view(event, editions)
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/research")
    def research_list() -> Any:
        from ..research import list_cases

        return list_cases(database, limit=30)

    @app.get("/api/cite/{evidence_id}")
    def cite(evidence_id: str) -> Any:
        from ..evidence import EvidenceError, resolve_citation

        try:
            citation = resolve_citation(database, evidence_id)
        except EvidenceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return citation.to_dict()

    # ------------------------------------------------------------ concordance and plates
    @app.get("/api/index")
    def index_overview() -> Any:
        from ..concordance import overview as concordance_overview

        counts = entity_counts(index_editions())
        entries = concordance_overview(database)
        for entry in entries:
            entry["pages"], entry["books"] = counts.get(entry["id"], (0, 0))
        return entries

    @app.get("/api/index/{entity_id}")
    def index_entry(entity_id: str, offset: int = Query(0, ge=0)) -> Any:
        from ..concordance import entity_detail

        allowed = index_editions()
        detail = entity_detail(database, entity_id, offset=offset, editions=allowed)
        if detail is None:
            raise HTTPException(404, "This index entry does not exist.")
        editions = {key: value for key, value in library.editions().items() if key in allowed}
        detail["passages"] = [p for p in detail["passages"] if p["edition_id"] in editions]
        detail["books"] = [b for b in detail["books"] if b["edition_id"] in editions]
        for item in detail["books"]:
            item["title"] = editions[item["edition_id"]]["title"]
        for item in detail["passages"]:
            item["title"] = editions[item["edition_id"]]["title"]
            item["noted"] = editions[item["edition_id"]]["noted"]
        return detail

    @app.get("/api/chart")
    def chart() -> Any:
        """Which index names share pages. Co-occurrence only: no relationship is claimed."""
        from ..concordance import overview as concordance_overview

        allowed = index_editions()
        if not allowed:
            return {"nodes": [], "links": []}
        stamp = concordance_stamp(allowed)
        total = stamp[0]
        if chart_cache.get("stamp") == stamp:
            return chart_cache["data"]
        counts = entity_counts(allowed)
        marks = ",".join("?" * len(allowed))
        with database.connect() as connection:
            pairs = connection.execute(
                "SELECT a.entity_id AS x, b.entity_id AS y, COUNT(*) AS shared,"
                " COUNT(DISTINCT ev.edition_id) AS books"
                " FROM mentions a JOIN mentions b ON a.evidence_id = b.evidence_id"
                "   AND a.entity_id < b.entity_id"
                " JOIN evidence ev ON ev.id = a.evidence_id"
                f" WHERE ev.edition_id IN ({marks})"
                " GROUP BY a.entity_id, b.entity_id HAVING shared >= ?",
                # In a full library one or two shared pages are chance; in a small one
                # they are all there is.
                (*sorted(allowed), 3 if total > 2000 else 1)).fetchall()
        entries = []
        for entry in concordance_overview(database):
            pages, books = counts.get(entry["id"], (0, 0))
            if pages:
                entries.append({**entry, "pages": pages, "books": books})
        entries.sort(key=lambda e: (-e["pages"], e["canonical_name"]))
        entries = entries[:120]
        size = {e["id"]: e["pages"] for e in entries}
        scored: dict[str, list[tuple[float, str, int, int]]] = {}
        for row in pairs:
            if row["x"] not in size or row["y"] not in size:
                continue
            weight = row["shared"] / (size[row["x"]] * size[row["y"]]) ** 0.5
            scored.setdefault(row["x"], []).append((weight, row["y"], row["shared"], row["books"]))
            scored.setdefault(row["y"], []).append((weight, row["x"], row["shared"], row["books"]))
        links: dict[tuple[str, str], dict[str, Any]] = {}
        for node, candidates in scored.items():
            for weight, other, shared, books in sorted(candidates, reverse=True)[:3]:
                key = (node, other) if node < other else (other, node)
                links[key] = {"a": key[0], "b": key[1], "shared": int(shared),
                              "books": int(books), "weight": round(weight, 4)}
        data = {
            "nodes": [{"id": e["id"], "name": e["canonical_name"], "type": e["entity_type"],
                       "type_label": e["type_label"], "pages": e["pages"], "books": e["books"]}
                      for e in entries],
            "links": sorted(links.values(), key=lambda link: -link["weight"]),
        }
        chart_cache.clear()
        chart_cache.update(stamp=stamp, data=data)
        return data

    @app.get("/api/plates")
    def plates(offset: int = Query(0, ge=0), limit: int = Query(48, ge=1, le=120),
               book: str | None = None) -> Any:
        editions = library.editions()
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.page_index, p.printed_page_label, a.edition_id, v.alternatives"
                " FROM visual_observations v JOIN pages p ON p.id = v.page_id"
                " JOIN assets a ON a.id = p.asset_id WHERE v.observation_type = 'plate'"
                + (" AND a.edition_id = ?" if book else "")
                + " ORDER BY a.edition_id, p.page_index",
                ((book,) if book else ()),
            ).fetchall()
        items = [
            {"page_id": r["id"], "page_label": r["printed_page_label"] or str(r["page_index"] + 1),
             "edition_id": r["edition_id"], "title": editions[r["edition_id"]]["title"]}
            for r in rows if r["edition_id"] in editions
        ]
        return {"total": len(items), "items": items[offset:offset + limit]}

    # ------------------------------------------------------------ archive status
    @app.get("/api/archive")
    def archive() -> Any:
        with database.connect() as connection:
            rights = connection.execute(
                "SELECT state, license_name, reviewer, notes, scope, created_at"
                " FROM rights_decisions ORDER BY created_at DESC LIMIT 1").fetchone()
            runs = connection.execute(
                "SELECT task_type, provider, model_name, completed_at, outcome, metrics"
                " FROM model_runs ORDER BY completed_at DESC LIMIT 12").fetchall()
            engines = connection.execute(
                "SELECT engine_name, COUNT(*) AS pages FROM transcriptions"
                " GROUP BY engine_name ORDER BY pages DESC").fetchall()
        return {
            "rights": dict(rights) if rights else None,
            "runs": [dict(r) for r in runs],
            "engines": [dict(r) for r in engines],
            "integrity": checked_integrity(),
        }

    @app.exception_handler(404)
    async def not_found(request: Any, exc: Any) -> Any:
        if request.url.path.startswith("/api/"):
            detail = getattr(exc, "detail", "Not found.")
            return JSONResponse({"detail": detail}, status_code=404)
        return FileResponse(STATIC / "index.html")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


_STOP = frozenset(
    "a an and are as at be but by for from had has have he her his i in into is it its of on"
    " or our she so that the their them then there these they this to was we were what when"
    " which who will with ye you your unto thee thou thy shall hath doth".split()
)


def _word_hits(database: Database, query: str, limit: int) -> list[Any]:
    """Pages containing every meaningful word first, then pages with some of them."""
    words = re.findall(r"[^\W_]+", query, re.UNICODE)
    meaningful = [w for w in words if w.lower() not in _STOP and len(w) > 1] or words
    text = " ".join(meaningful)
    hits: list[Any] = []
    seen: set[str] = set()
    modes = ("phrase", "all", "any") if len(meaningful) > 1 else ("any",)
    for mode in modes:
        try:
            batch = search(database, text, limit=limit, mode=mode)
        except SearchError:
            continue
        for hit in batch:
            if hit.chunk_id not in seen:
                seen.add(hit.chunk_id)
                hits.append(hit)
        if len(hits) >= limit:
            break
    return hits[:limit]


OCR_SURE = 0.8  # below this a machine reading is mostly noise (ADR-023)


def _unsure_pages(database: Database, page_ids: list[str]) -> set[str]:
    """Pages whose latest transcription is an OCR reading the engine itself doubted."""
    unsure: set[str] = set()
    with database.connect() as connection:
        for start in range(0, len(page_ids), 500):
            batch = page_ids[start:start + 500]
            marks = ",".join("?" * len(batch))
            rows = connection.execute(
                "SELECT t.page_id, t.engine_name, t.confidence FROM transcriptions t"
                f" WHERE t.page_id IN ({marks}) AND t.created_at = (SELECT MAX(u.created_at)"
                "   FROM transcriptions u WHERE u.page_id = t.page_id)", batch).fetchall()
            for row in rows:
                machine = not str(row["engine_name"] or "").startswith("PyMuPDF")
                if machine and row["confidence"] is not None and row["confidence"] < OCR_SURE:
                    unsure.add(row["page_id"])
    return unsure


def _entity_counts(database: Database, editions: set[str]) -> dict[str, tuple[int, int]]:
    """Pages and books naming each index entry, counted only within the given editions."""
    if not editions:
        return {}
    marks = ",".join("?" * len(editions))
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT m.entity_id, COUNT(*) AS pages, COUNT(DISTINCT ev.edition_id) AS books"
            " FROM mentions m JOIN evidence ev ON ev.id = m.evidence_id"
            f" WHERE ev.edition_id IN ({marks}) GROUP BY m.entity_id", sorted(editions)).fetchall()
    return {row["entity_id"]: (int(row["pages"]), int(row["books"])) for row in rows}


def _book_names(database: Database, edition_id: str, limit: int = 16) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT en.id, en.canonical_name, en.entity_type, COUNT(*) AS pages FROM mentions m"
            " JOIN evidence ev ON ev.id = m.evidence_id JOIN entities en ON en.id = m.entity_id"
            " WHERE ev.edition_id = ? GROUP BY en.id ORDER BY pages DESC, en.canonical_name"
            " LIMIT ?", (edition_id, limit)).fetchall()
    return [dict(row) for row in rows]


def _answer_view(answer: dict[str, Any], editions: dict[str, Any]) -> dict[str, Any]:
    """The checked answer as the reader sees it: kept sentences in paragraphs, then sources.

    A sentence that leans on a book with a curator's note is marked here, whatever the model
    chose to say about it, so the warning never depends on the model.
    """
    from ..answer import strip_citations

    passages = {p["number"]: p for p in answer["passages"]}
    paragraphs: list[list[dict[str, Any]]] = []
    last = None
    for sentence in answer["sentences"]:
        if not sentence["kept"]:
            continue
        if sentence["paragraph"] != last or not paragraphs:
            paragraphs.append([])
            last = sentence["paragraph"]
        cites = [n for n in sentence["cites"] if n in passages]
        paragraphs[-1].append({
            "text": strip_citations(sentence["text"]),
            "cites": cites,
            "noted": any(passages[n]["note"] for n in cites),
        })
    sources = []
    for p in answer["passages"]:
        edition = editions.get(p["edition_id"], {})
        sources.append({
            "number": p["number"], "page_id": p["page_id"], "page_index": p["page_index"],
            "page_label": p["page_label"], "evidence_id": p["evidence_id"], "text": p["text"],
            "uncertain_reading": p["uncertain_reading"], "note": p["note"],
            "found_by": list(p["found_by"]),
            "cited": any(p["number"] in s["cites"] for para in paragraphs for s in para),
            "book": {"id": p["edition_id"], "title": p["book_title"],
                     "author": edition.get("author"), "noted": bool(p["note"])},
        })
    return {
        "type": "answer", "case_id": answer["case_id"], "question": answer["question"],
        "depth": answer["depth"], "model": answer["model"], "model_label": answer["model_label"],
        "answered": answer["answered"], "paragraphs": paragraphs, "sources": sources,
        "withheld": [{"text": s["text"], "reason": s["reason"]}
                     for s in answer["sentences"] if not s["kept"]],
        "kept": answer["kept"], "seconds": answer["seconds"],
        "tokens_per_second": answer["tokens_per_second"], "warnings": answer["warnings"],
    }


def _marked(snippet: str) -> str:
    escaped = html.escape(" ".join(snippet.split()))
    return _MARK.sub(lambda m: f"<mark>{m.group(1)}</mark>", escaped)


def _trim(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width].rsplit(" ", 1)[0] + " …"


def _chunk_rows(database: Database, chunk_ids: list[str]) -> dict[str, Any]:
    if not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT c.id, c.evidence_id, c.page_id, c.edition_id, c.text, p.page_index,"
            f" p.printed_page_label FROM chunk_records c JOIN pages p ON p.id = c.page_id"
            f" WHERE c.id IN ({marks})", chunk_ids,
        ).fetchall()
    return {row["id"]: row for row in rows}
