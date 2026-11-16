"""Cited answers: the sentence checker, the model client's limits, and the whole route."""
from __future__ import annotations

import json

import pytest
from test_reading_room import corpus  # noqa: F401 - the shared fixture corpus

from aki.answer import (
    NO_ANSWER,
    OllamaWriter,
    Passage,
    WriterUnavailable,
    check_answer,
    check_sentence,
    quote_on_page,
    split_sentences,
    strip_citations,
)

PAGE = ("And Azazel taught men to make swords, and knives, and shields, and breastplates, and"
        " made known to them the metals of the earth and the art of working them, and bracelets,"
        " and ornaments. Semjaza taught enchantments, and root-cuttings. In the days of Jared two"
        " hundred of the Watchers descended upon Mount Hermon.")
OCR_PAGE = ("No workable disclosure of\na neans for accomplishing thts is included, and such"
            " generali-\nties as suggest the approach which Tesla had in mind do not seem capable.")


def _passage(number: int, text: str, title: str = "The Book of Enoch", note: str | None = None
             ) -> Passage:
    return Passage(number=number, page_id=f"p{number}", page_index=number, page_label=str(number),
                   edition_id=f"e{number}", book_title=title, text=text, found_by=("words",),
                   uncertain_reading=False, note=note)


PASSAGES = {1: _passage(1, PAGE), 2: _passage(2, OCR_PAGE, "FBI File on Nicola Tesla")}


# ------------------------------------------------------------------ splitting
def test_split_keeps_citations_with_their_sentence():
    text = ("Azazel taught men to make swords [1]. Semjaza taught enchantments. [1]\n\n"
            "St. Jerome is not here [2]. Two hundred descended [1][2].")
    sentences = split_sentences(text)
    assert [s for _, s in sentences] == [
        "Azazel taught men to make swords [1].",
        "Semjaza taught enchantments [1].",
        "St. Jerome is not here [2].",
        "Two hundred descended [1][2].",
    ]
    assert [p for p, _ in sentences] == [0, 0, 1, 1]
    assert strip_citations("Swords were made [1][2].") == "Swords were made."


# ------------------------------------------------------------------ checking
@pytest.mark.parametrize("sentence, kept, reason", [
    ("One passage says Azazel taught men to make swords and shields [1].", True, "drawn"),
    ("It says Azazel taught “the art of working them” [1].", True, "drawn"),
    ("Azazel taught men to make swords.", False, "cites no passage"),
    ("Azazel taught men to make swords [7].", False, "not given"),
    ("Azazel taught Noah to make swords [1].", False, "names “Noah”"),
    ("Three hundred Watchers descended in 1903 [1].", False, "number 1903"),
    ("It says Azazel taught “the art of forging steel blades” [1].", False, "quotes"),
    ("The passage asserts gunpowder, cannons, rifles and modern artillery came first [1].",
     False, "of its words"),
    ("Azazel taught men to make swords [2].", False, "of its words"),  # true, not on page 2
])
def test_sentence_check(sentence, kept, reason):
    _, ok, why, _ = check_sentence(sentence, PASSAGES)
    assert ok is kept and reason in why


def test_no_answer_sentence_needs_no_citation():
    assert check_sentence(NO_ANSWER, PASSAGES)[1]


def test_quotations_forgive_machine_reading_slips_but_not_rewording():
    assert quote_on_page("no workable disclosure of a means for accomplishing this", OCR_PAGE)
    assert quote_on_page("such generalities as suggest the approach", OCR_PAGE)
    assert not quote_on_page("no practical description of a method for achieving this", OCR_PAGE)
    assert not quote_on_page("Tesla had a complete working design", OCR_PAGE)


def test_a_curators_note_may_be_named_in_the_sentence_that_uses_it():
    note = "It is a saved conversation with an AI chatbot, not an old text."
    passages = {1: _passage(1, PAGE, "Watchers Angels", note=note)}
    sentence = "A saved conversation with an AI chatbot claims Azazel taught men swords [1]."
    assert check_sentence(sentence, passages)[1]


def test_check_answer_marks_each_sentence():
    checks = check_answer("Azazel taught men to make swords [1]. He also built the pyramids [1].",
                          list(PASSAGES.values()))
    assert [c.kept for c in checks] == [True, False]


# ------------------------------------------------------------------ the model client
def test_writer_refuses_any_address_off_this_computer():
    with pytest.raises(WriterUnavailable):
        OllamaWriter("http://192.168.1.20:11434")
    with pytest.raises(WriterUnavailable):
        OllamaWriter("https://example.com")
    assert OllamaWriter("http://127.0.0.1:1").status()["running"] is False


# ------------------------------------------------------------------ the route, end to end
class FakeWriter:
    def __init__(self, text: str, *, running: bool = True,
                 models: tuple[str, ...] = ("qwen3.5:9b", "gemma4:26b")) -> None:
        self.text = text
        self.running = running
        self.models = models
        self.calls: list[dict] = []

    def status(self):
        return {"running": self.running, "version": "test",
                "models": {m: "sha256:test" for m in self.models} if self.running else {}}

    def write(self, model, messages, options, on_progress=None):
        self.calls.append({"model": model, "messages": messages, "options": options})
        if on_progress:
            on_progress(len(self.text))
        return {"text": self.text, "eval_count": 40, "eval_duration": 1_000_000_000,
                "prompt_eval_count": 900}


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture()
def room(corpus):  # noqa: F811 - the imported fixture, requested by name
    from test_reading_room import _seed

    from aki.concordance import build as build_concordance
    from aki.semantic import LsaEmbedder, build_index

    database, blobs, root = corpus
    _seed(database, "e9", "Hidden", url="https://theholyword.net/bible/b9", grant=False)
    build_index(database, root, embedder=LsaEmbedder(dimensions=8))
    build_concordance(database)
    return database, blobs, root


def _client(room, writer):
    from fastapi.testclient import TestClient

    from aki.app.api import create_app

    database, blobs, root = room
    return TestClient(create_app(database.path, blobs, root, writer=writer))


def test_cited_answer_route(room):
    database = room[0]
    writer = FakeWriter("One passage says Azazel taught men to make swords and mirrors [1]. "
                        "Azazel also taught Noah to build the ark [1].\n\n"
                        "This sentence cites nothing at all.")
    client = _client(room, writer)
    models = client.get("/api/answer/models").json()
    assert models["running"] and [m["installed"] for m in models["models"]] == [True, True]

    events = _events(client.post("/api/answer", json={"question": "What did Azazel teach men?",
                                                      "depth": "deep"}))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "stage" and "passages" in kinds and kinds[-1] == "answer"
    answer = events[-1]
    assert writer.calls[0]["model"] == "gemma4:26b"
    assert answer["answered"] and answer["kept"] == 1
    assert [s["text"] for p in answer["paragraphs"] for s in p] == [
        "One passage says Azazel taught men to make swords and mirrors."]
    assert answer["paragraphs"][0][0]["cites"] == [1]
    reasons = [w["reason"] for w in answer["withheld"]]
    assert any("Noah" in r for r in reasons) and "cites no passage" in reasons
    source = answer["sources"][0]
    assert source["cited"] and "Azazel" in source["text"] and source["book"]["id"] == "e1"
    # The passage the model read is the evidence the citation resolves to.
    cite = client.get(f"/api/cite/{source['evidence_id']}").json()
    assert cite["quoted_text"] == source["text"] and cite["page_index"] == source["page_index"]
    prompt = writer.calls[0]["messages"][1]["content"]
    assert "[1] Fixture of the Watchers" in prompt and "Hidden" not in prompt

    with database.connect() as connection:
        claims = connection.execute(
            "SELECT claim_text, status, claim_type, created_by_run FROM claims"
            " WHERE research_case_id=? ORDER BY created_at", (answer["case_id"],)).fetchall()
        run = connection.execute("SELECT * FROM model_runs WHERE task_type='cited_answer'").fetchone()
        case = connection.execute("SELECT state, dossier FROM research_cases WHERE id=?",
                                  (answer["case_id"],)).fetchone()
    assert [c["status"] for c in claims] == ["supported", "rejected", "rejected"]
    assert {c["claim_type"] for c in claims} == {"model_inferred"}
    assert run["model_name"] == "gemma4:26b" and run["outcome"] == "succeeded"
    assert run["provider"] == "ollama" and claims[0]["created_by_run"] == run["id"]
    assert json.loads(run["metrics"])["withheld"] == 2
    assert case["state"] == "complete" and "Withheld sentences" in case["dossier"]


def test_answer_route_when_the_model_service_is_down(room):
    client = _client(room, FakeWriter("", running=False))
    assert client.get("/api/answer/models").json()["running"] is False
    events = _events(client.post("/api/answer", json={"question": "What did Azazel teach men?"}))
    assert events[-1]["type"] == "error" and "not running" in events[-1]["detail"]


def test_answer_route_when_the_model_is_missing(room):
    client = _client(room, FakeWriter("", models=("qwen3.5:9b",)))
    events = _events(client.post("/api/answer", json={"question": "What did Azazel teach men?",
                                                      "depth": "deep"}))
    assert events[-1]["type"] == "error" and "ollama pull gemma4:26b" in events[-1]["detail"]


def test_answer_route_with_nothing_to_find(room):
    writer = FakeWriter("unused")
    client = _client(room, writer)
    events = _events(client.post("/api/answer", json={"question": "Zyxwv qwertyuiop asdfgh?"}))
    answer = events[-1]
    assert answer["type"] == "answer" and not answer["answered"] and not answer["sources"]
    assert writer.calls == []  # the model is not asked when no page was found
