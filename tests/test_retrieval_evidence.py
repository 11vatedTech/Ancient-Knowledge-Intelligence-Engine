"""Retrieval, rank fusion, citation resolution and claim verification."""
from __future__ import annotations

import json

import pytest

from aki.db import Database, initialize, utcnow
from aki.evidence import (
    CitationError,
    add_evidence,
    assess_claim,
    create_claim,
    link_claim,
    resolve_citation,
    verify_claims,
)
from aki.research import plan_research, run_research
from aki.search import (
    SearchError,
    SearchFilters,
    build_match_query,
    index_chunk,
    ndcg_at_k,
    rebuild_index,
    recall_at_k,
    reciprocal_rank_fusion,
    search,
)


@pytest.fixture()
def corpus(tmp_path):
    database = Database(tmp_path / "aki.sqlite3")
    initialize(database.path)
    seeded = []
    with database.transaction() as connection:
        for index, (text, page_index) in enumerate([
            ("The archive preserves the original page image byte for byte.", 0),
            ("A citation resolves to an edition and a page rectangle.", 1),
            ("Similarity is a retrieval signal, not evidence of transmission.", 2),
        ]):
            work_id, edition_id = f"w{index}", f"e{index}"
            asset_id, page_id, transcription_id = f"a{index}", f"p{index}", f"t{index}"
            connection.execute(
                "INSERT INTO works (id, canonical_title, normalized_title, work_class, created_at)"
                " VALUES (?,?,?,?,?)",
                (work_id, f"Work {index}", f"work {index}", "ancient_primary", utcnow()),
            )
            connection.execute(
                "INSERT INTO editions (id, work_id, edition_title, edition_type, language,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (edition_id, work_id, f"Edition {index}", "facsimile", "en", utcnow()),
            )
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('r0','e0','cleared_limited',?,'test',?)",
            (json.dumps({"permitted_operations": ["internal_search"]}), utcnow()),
        )
        for index, (text, page_index) in enumerate([
            ("The archive preserves the original page image byte for byte.", 0),
            ("A citation resolves to an edition and a page rectangle.", 1),
            ("Similarity is a retrieval signal, not evidence of transmission.", 2),
        ]):
            work_id, edition_id = f"w{index}", f"e{index}"
            asset_id, page_id, transcription_id = f"a{index}", f"p{index}", f"t{index}"
            connection.execute(
                "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256,"
                " byte_size, detected_mime, asset_role, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (asset_id, edition_id, "r0", f"sha256/{asset_id}", "a" * 64, 10,
                 "application/pdf", "original_scan", utcnow()),
            )
            connection.execute(
                "INSERT INTO pages (id, asset_id, page_index, printed_page_label, width_px,"
                " height_px, page_image_key, page_sha256, processing_state, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (page_id, asset_id, page_index, str(page_index + 1), 100, 200,
                 f"sha256/{page_id}", "b" * 64, "completed", utcnow()),
            )
            connection.execute(
                "INSERT INTO transcriptions (id, page_id, layer, text_content, engine_name,"
                " engine_version, review_state, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (transcription_id, page_id, "normalized", text, "test", "1", "unreviewed",
                 utcnow()),
            )
            seeded.append((work_id, edition_id, page_id, transcription_id, text))

    for work_id, edition_id, page_id, transcription_id, text in seeded:
        evidence_id = add_evidence(
            database, edition_id=edition_id, page_id=page_id,
            transcription_id=transcription_id, evidence_kind="passage",
            evidence_class="source_explicit", quoted_text=text,
        )
        index_chunk(
            database, evidence_id=evidence_id, page_id=page_id, edition_id=edition_id,
            work_id=work_id, layer="normalized", text=text, pipeline_version="test",
        )
    return database


def test_match_query_quotes_untrusted_input():
    query = build_match_query('archive" OR *')
    assert '"archive"' in query
    assert "*" not in query


def test_match_query_rejects_empty():
    with pytest.raises(SearchError):
        build_match_query("  ??  ")


def test_search_returns_ranked_hits_with_locators(corpus):
    hits = search(corpus, "archive preserves page", limit=5)
    assert hits
    top = hits[0]
    assert top.work_title == "Work 0"
    assert top.page_index == 0
    assert top.rights_state == "cleared_limited"
    assert "archive" in top.snippet.lower()


def test_search_filters_by_edition(corpus):
    hits = search(corpus, "citation resolves", filters=SearchFilters(edition_id="e2"), limit=5)
    assert hits == []


def test_phrase_mode_requires_adjacency(corpus):
    assert search(corpus, "archive preserves", mode="phrase")
    assert search(corpus, "archive rectangle", mode="phrase") == []


def test_rebuild_index_reproduces_results(corpus):
    before = [hit.chunk_id for hit in search(corpus, "citation", limit=5)]
    rebuild_index(corpus)
    after = [hit.chunk_id for hit in search(corpus, "citation", limit=5)]
    assert before == after


def test_reciprocal_rank_fusion_prefers_agreement():
    fused = reciprocal_rank_fusion({"a": ["x", "y"], "b": ["y", "z"]})
    assert fused[0][0] == "y"
    assert dict(fused)["x"] == pytest.approx(1 / 61)


def test_fusion_rejects_bad_k():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"a": ["x"]}, k=0)


def test_recall_and_ndcg_metrics():
    assert recall_at_k(["a", "b"], {"a"}, 20) == 1.0
    assert recall_at_k(["c"], {"a"}, 20) == 0.0
    # One of two equally relevant items retrieved first: DCG 1.0 vs ideal 1 + 1/log2(3).
    assert ndcg_at_k(["a"], {"a": 1.0, "b": 1.0}, 10) == pytest.approx(0.613147, rel=1e-5)
    assert ndcg_at_k(["a", "b"], {"a": 1.0, "b": 1.0}, 10) == pytest.approx(1.0)
    assert ndcg_at_k([], {"a": 1.0}, 10) == 0.0


def test_citation_resolves_to_page(corpus):
    with corpus.connect() as connection:
        evidence_id = connection.execute(
            "SELECT id FROM evidence WHERE edition_id='e0' LIMIT 1"
        ).fetchone()["id"]
    citation = resolve_citation(corpus, evidence_id)
    assert citation.page_index == 0
    assert citation.page_image_key
    assert citation.quoted_text


def test_unknown_citation_raises(corpus):
    with pytest.raises(CitationError):
        resolve_citation(corpus, "missing")


def test_supported_claim_requires_resolvable_support(corpus):
    with corpus.connect() as connection:
        evidence_id = connection.execute("SELECT id FROM evidence LIMIT 1").fetchone()["id"]
    claim_id = create_claim(corpus, statement="The archive preserves bytes.", claim_type="source_explicit")
    assert assess_claim(corpus, claim_id).status == "unverified"
    link_claim(corpus, claim_id, evidence_id, "source_of_claim")
    assessment = assess_claim(corpus, claim_id)
    assert assessment.status == "supported"
    assert assessment.verified is True


def test_contradicting_evidence_marks_claim_contested(corpus):
    with corpus.connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM evidence LIMIT 2")]
    claim_id = create_claim(corpus, statement="Conflicting reading.", claim_type="model_inferred")
    link_claim(corpus, claim_id, ids[0], "supports")
    link_claim(corpus, claim_id, ids[1], "contradicts")
    assert assess_claim(corpus, claim_id).status == "contested"


def test_uncited_claim_fails_verification(corpus):
    claim_id = create_claim(corpus, statement="An unsupported assertion.", claim_type="historical_context")
    report = verify_claims(corpus, [claim_id])
    assert report.ok is False
    assert any("unverified" in error for error in report.errors)


def test_invalid_claim_type_rejected(corpus):
    with pytest.raises(ValueError):
        create_claim(corpus, statement="x", claim_type="certain_fact")


def test_plan_decomposes_question():
    plan = plan_research("What does the archive preserve and how is a citation resolved?")
    assert len(plan.subquestions) >= 2
    assert "archive" in plan.terms


def test_research_produces_verified_dossier(corpus, tmp_path):
    result = run_research(
        corpus, "What does the archive preserve and how is a citation resolved?",
        limit=5, output_dir=tmp_path,
    )
    assert result.claim_ids
    assert result.verification.ok
    assert "Evidence-assembly" in result.dossier or "evidence-assembly" in result.dossier
    assert result.dossier_path is not None
    assert "Cite:" in result.dossier
    assert all(a.status == "supported" for a in result.verification.claims)
