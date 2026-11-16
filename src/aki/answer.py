"""Cited answers: a local language model writes from passages the archive has gathered.

The order of work is fixed, and each step is independent of the one that follows it:

1. **Gather.** Whole passages (about 1,400 characters, ADR-030) are taken from the pages
   that best match the question by words and by meaning, within the editions whose rights
   permit research. Each passage is stored as an ``evidence`` row before the model sees it.
2. **Write.** A local model (through Ollama, on this computer only) is given the numbered
   passages and told to write only what they say, ending every sentence with the numbers
   of the passages it comes from.
3. **Check.** Every sentence is checked here, deterministically, against the passages it
   cites: it must cite at least one, its names and numbers must appear in them, any words
   it puts in quotation marks must appear in them exactly, and most of its other words
   must be found there. A sentence that fails is withheld from the answer and shown apart
   as withheld. The model is never asked to confirm its own work.
4. **Record.** The case, the model run, and each sentence as a claim linked to its
   evidence are written to the archive, so ``aki cite`` resolves every citation later.

The checker proves that a sentence is drawn from the words of its page. It cannot prove
that a paraphrase keeps the page's sense, and the reading room says so beside every answer.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterator, Protocol, Sequence

from .db import Database, new_id, utcnow
from .evidence import add_evidence, create_claim, link_claim, verify_claims
from .search import SearchError, reciprocal_rank_fusion, search

PROMPT_VERSION = "cited-answer-v1"
PASSAGE_CHARS = 1400
MAX_PASSAGES = 8
PER_BOOK = 2
SUPPORT_THRESHOLD = 0.6
OCR_SURE = 0.8
NO_ANSWER = "The passages found do not answer this question."


class AnswerError(RuntimeError):
    """A cited answer could not be produced."""


class WriterUnavailable(AnswerError):
    """The local model service or the chosen model is not available."""


# ------------------------------------------------------------------ models
@dataclass(frozen=True, slots=True)
class ModelChoice:
    depth: str
    model: str
    label: str
    note: str


def model_choices() -> dict[str, ModelChoice]:
    """The two models of ADR-030. Either may be replaced by an environment variable."""
    return {
        "quick": ModelChoice("quick", os.environ.get("AKI_ANSWER_MODEL", "qwen3.5:9b"),
                             "Qwen 3.5 9B", "Answers in a few seconds."),
        "deep": ModelChoice("deep", os.environ.get("AKI_DEEP_MODEL", "gemma4:26b"),
                            "Gemma 4 26B", "Slower and more careful: about two minutes."),
    }


# ------------------------------------------------------------------ the model service
class Writer(Protocol):
    def status(self) -> dict[str, Any]: ...

    def write(self, model: str, messages: list[dict[str, str]], options: dict[str, Any],
              on_progress: Callable[[int], None] | None = None) -> dict[str, Any]: ...


def _loopback_url(url: str) -> str:
    import ipaddress

    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        raise WriterUnavailable(
            f"refusing to send passages to {host!r}: the archive's texts may not leave this "
            "computer, so the model service must run on 127.0.0.1")
    return url.rstrip("/")


class OllamaWriter:
    """Talks to the local Ollama service. Refuses any address that is not this computer."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 600.0) -> None:
        self.base_url = _loopback_url(
            base_url or os.environ.get("AKI_OLLAMA_URL", "http://127.0.0.1:11434"))
        self.timeout = timeout

    def _get(self, path: str, timeout: float = 4.0) -> Any:
        with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def status(self) -> dict[str, Any]:
        """Which models are installed, with their digests, and the service version."""
        try:
            tags = self._get("/api/tags")
            version = self._get("/api/version").get("version", "unknown")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"running": False, "version": None, "models": {},
                    "reason": f"the local model service is not running ({exc})"}
        models = {m["name"]: m.get("digest", "") for m in tags.get("models", [])}
        return {"running": True, "version": version, "models": models}

    def write(self, model: str, messages: list[dict[str, str]], options: dict[str, Any],
              on_progress: Callable[[int], None] | None = None) -> dict[str, Any]:
        body = json.dumps({"model": model, "messages": messages, "stream": True,
                           "think": False, "options": options}).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/api/chat", data=body,
                                         headers={"Content-Type": "application/json"})
        parts: list[str] = []
        final: dict[str, Any] = {}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for line in response:
                    if not line.strip():
                        continue
                    event = json.loads(line.decode("utf-8"))
                    if event.get("error"):
                        raise WriterUnavailable(str(event["error"]))
                    parts.append(event.get("message", {}).get("content", ""))
                    if on_progress is not None:
                        on_progress(sum(len(p) for p in parts))
                    if event.get("done"):
                        final = event
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise WriterUnavailable(f"the model service refused the request: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise WriterUnavailable(f"the local model service could not be reached ({exc})") from exc
        return {"text": "".join(parts), "eval_count": final.get("eval_count"),
                "eval_duration": final.get("eval_duration"),
                "prompt_eval_count": final.get("prompt_eval_count"),
                "total_duration": final.get("total_duration")}


# ------------------------------------------------------------------ gathering
@dataclass(frozen=True, slots=True)
class Passage:
    number: int
    page_id: str
    page_index: int
    page_label: str
    edition_id: str
    book_title: str
    text: str
    found_by: tuple[str, ...]
    uncertain_reading: bool
    note: str | None
    evidence_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STOP = frozenset(
    "a an and are as at be been but by for from had has have he her him his i if in into is it"
    " its me my no not of on or our she so such than that the their them then there these they"
    " this those to too was we were what when where which while who whom why will with would"
    " ye you your unto thee thou thy thine shall hath doth did does do also all any can could"
    " may might must one only other own same should some very about after again against"
    " because before being below between both during each few further here how just more"
    " most nor now off once out over through under until up upon what whose say says said"
    " according describe describes described tell tells told text texts source sources passage"
    " passages book books page pages state states stated mention mentions mentioned".split()
)
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _stem(word: str) -> str:
    word = _fold(word).replace("’", "'")
    word = re.sub(r"'s$", "", word)
    for suffix in ("ingly", "edly", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:6]


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if len(w) > 2 and _fold(w) not in _STOP]


def _window(text: str, terms: Sequence[str], centre: int | None = None,
            width: int = PASSAGE_CHARS) -> str:
    """About ``width`` characters of ``text`` around the densest run of ``terms``."""
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if len(clean) <= width:
        return clean
    if centre is None:
        folded = _fold(clean)
        stems = {_stem(t) for t in terms if len(t) > 2}
        positions = [m.start() for m in _WORD.finditer(folded) if _stem(m.group()) in stems]
        if positions:
            best, best_count = positions[0], 0
            for start in positions:
                count = sum(1 for p in positions if start <= p < start + width * 0.7)
                if count > best_count:
                    best, best_count = start, count
            centre = best + int(width * 0.35)
        else:
            centre = 0
    start = max(0, min(len(clean) - width, centre - width // 2))
    end = min(len(clean), start + width)
    # Open and close on whole words, at a sentence edge when one is near.
    if start > 0:
        edge = max(clean.rfind(". ", start - 120, start + 120), clean.rfind("\n", start - 120, start + 120))
        start = edge + 1 if edge >= 0 else clean.find(" ", start) + 1
    if end < len(clean):
        edge = clean.rfind(". ", end - 160, end)
        end = edge + 1 if edge > start else max(clean.rfind(" ", start, end), start + 1)
    return clean[start:end].strip()


def gather_passages(
    database: Database,
    question: str,
    *,
    research_editions: set[str],
    embedding_editions: set[str],
    titles: dict[str, str],
    notes: dict[str, str | None],
    meaning: Callable[[str, int], list[Any]] | None = None,
    limit: int = MAX_PASSAGES,
) -> list[Passage]:
    """The best passages for a question, at most ``PER_BOOK`` from any one book."""
    from .research import plan_research

    plan = plan_research(question)
    terms = list(plan.terms)
    legs: dict[str, list[str]] = {}
    centres: dict[str, int] = {}
    if terms:
        seen: list[str] = []
        for mode in (("all", "any") if len(terms) > 1 else ("any",)):
            try:
                hits = search(database, " ".join(terms), limit=limit * 8, mode=mode)
            except SearchError:
                continue
            for hit in hits:
                if hit.edition_id in research_editions and hit.chunk_id not in seen:
                    seen.append(hit.chunk_id)
        legs["words"] = seen[: limit * 6]
    if meaning is not None:
        kept: list[str] = []
        for match in meaning(question, limit * 8):
            # A question with no known words has no direction; its "nearest" pages are noise.
            if match.score <= 0.0:
                continue
            if match.chunk_id not in kept:
                kept.append(match.chunk_id)
                centres.setdefault(match.chunk_id, (match.start + match.end) // 2)
        legs["meaning"] = kept
    fused = reciprocal_rank_fusion({k: v for k, v in legs.items() if v})
    if not fused:
        return []
    ids = [chunk_id for chunk_id, _ in fused[: limit * 10]]
    marks = ",".join("?" * len(ids))
    with database.connect() as connection:
        rows = {row["id"]: row for row in connection.execute(
            "SELECT c.id, c.page_id, c.edition_id, c.text, p.page_index, p.printed_page_label,"
            " (SELECT t.engine_name || '|' || COALESCE(t.confidence, '') FROM transcriptions t"
            "   WHERE t.page_id = c.page_id ORDER BY t.created_at DESC LIMIT 1) AS reading"
            f" FROM chunk_records c JOIN pages p ON p.id = c.page_id WHERE c.id IN ({marks})",
            ids).fetchall()}
    passages: list[Passage] = []
    per_book: dict[str, int] = {}
    pages: set[str] = set()
    for chunk_id in ids:
        row = rows.get(chunk_id)
        if row is None or row["edition_id"] not in research_editions:
            continue
        if row["edition_id"] not in titles or row["page_id"] in pages:
            continue
        found_by = tuple(leg for leg, members in legs.items() if chunk_id in members)
        if found_by == ("meaning",) and row["edition_id"] not in embedding_editions:
            continue
        if per_book.get(row["edition_id"], 0) >= PER_BOOK:
            continue
        text = _window(str(row["text"]), terms,
                       None if "words" in found_by else centres.get(chunk_id))
        if len(_content_words(text)) < 12:
            continue
        engine, _, confidence = str(row["reading"] or "|").partition("|")
        machine = bool(engine) and not engine.startswith("PyMuPDF")
        uncertain = machine and bool(confidence) and float(confidence) < OCR_SURE
        pages.add(row["page_id"])
        per_book[row["edition_id"]] = per_book.get(row["edition_id"], 0) + 1
        passages.append(Passage(
            number=len(passages) + 1, page_id=row["page_id"], page_index=int(row["page_index"]),
            page_label=row["printed_page_label"] or str(int(row["page_index"]) + 1),
            edition_id=row["edition_id"], book_title=titles[row["edition_id"]], text=text,
            found_by=found_by, uncertain_reading=uncertain, note=notes.get(row["edition_id"]),
        ))
        if len(passages) >= limit:
            break
    return passages


# ------------------------------------------------------------------ the prompt
SYSTEM = (
    "You answer questions about a private library of old and unusual books. You may use only"
    " the numbered passages you are given. You know nothing else: do not add facts, dates,"
    " names or explanations from your own knowledge, even when you are sure of them.\n\n"
    "Rules:\n"
    "1. Write plain prose in short paragraphs. No headings, lists, bold or preamble.\n"
    "2. End every sentence with the number of each passage it comes from, in square"
    " brackets, before the full stop: like this [2]. or like this [1][4].\n"
    "3. Say what the passages say, attributing it to them (\"One passage says...\"). Keep"
    " the passages' own words, names and spellings. Put exact quotations in double quotes.\n"
    "4. Report what a book claims as its claim, not as fact. Where passages disagree,"
    " say so and cite each side.\n"
    "5. Some passages carry a curator's warning. Any sentence that uses such a passage must"
    " itself say what the warning says the book is, for example \"A saved conversation with"
    " an AI chatbot claims...\" or \"The Protocols, a forgery, claims...\".\n"
    "6. If the passages do not answer the question, write exactly: " + NO_ANSWER + "\n"
    "7. Write at most about 250 words."
)


def build_messages(question: str, passages: Sequence[Passage]) -> list[dict[str, str]]:
    blocks = []
    for p in passages:
        marks = []
        if p.uncertain_reading:
            marks.append("This page was read by machine from old or damaged print; its"
                         " words may contain reading errors.")
        if p.note:
            marks.append(f"CURATOR'S WARNING about this book: {p.note}")
        head = f"[{p.number}] {p.book_title}, page {p.page_label}"
        blocks.append(head + ("\n" + "\n".join(marks) if marks else "") + "\n\"\"\"\n"
                      + p.text + "\n\"\"\"")
    user = ("Passages:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}\n\n"
            "Answer from the passages only, citing a passage number at the end of every sentence.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ------------------------------------------------------------------ checking
_CITE = re.compile(r"\[(\d{1,2}(?:\s*[,;–-]\s*\d{1,2})*)\]")
_TRAILING_CITES = re.compile(r"([.!?][\"”’')]*)((?:\s*\[\d{1,2}(?:\s*[,;–-]\s*\d{1,2})*\])+)")
_BREAK = re.compile(r"(?<=[.!?])[\"”’')]*\s+")
_ABBREVIATION = re.compile(
    r"\b(?:St|Mr|Mrs|Dr|Rev|Vol|vol|ch|Ch|cf|viz|p|pp|v|vv|e\.g|i\.e|etc|No|no)\.$|\b[A-Z]\.$")
_QUOTED = re.compile(r"[\"“]([^\"”]{3,400})[\"”]")


@dataclass(frozen=True, slots=True)
class SentenceCheck:
    text: str
    cites: tuple[int, ...]
    kept: bool
    reason: str
    support: float
    paragraph: int
    claim_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numbers(group: str) -> list[int]:
    found: list[int] = []
    for part in re.split(r"\s*[,;]\s*", group):
        if re.search(r"[–-]", part):
            low, _, high = re.split(r"\s*([–-])\s*", part)
            if low.isdigit() and high.isdigit() and int(high) - int(low) < 10:
                found.extend(range(int(low), int(high) + 1))
        elif part.strip().isdigit():
            found.append(int(part))
    return found


def split_sentences(text: str) -> list[tuple[int, str]]:
    """(paragraph number, sentence) pairs. Citation marks stay with their sentence."""
    text = text.replace("**", "").replace("__", "")
    out: list[tuple[int, str]] = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    for number, paragraph in enumerate(paragraphs):
        lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line) for line in paragraph.splitlines()]
        flat = " ".join(line.strip() for line in lines if line.strip())
        # "...end. [2]" becomes "...end [2]." so the marks stay with their own sentence.
        flat = _TRAILING_CITES.sub(lambda m: f"{m.group(2)}{m.group(1)}", flat)
        pending = ""
        for piece in _BREAK.split(flat):
            piece = f"{pending} {piece}".strip() if pending else piece.strip()
            if not piece:
                continue
            if _ABBREVIATION.search(piece):
                pending = piece
                continue
            pending = ""
            out.append((number, piece))
        if pending:
            out.append((number, pending))
    return out


def _squash(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)
    return " ".join(_WORD.findall(_fold(text)))


QUOTE_SIMILARITY = 0.88


def quote_on_page(quoted: str, source: str) -> bool:
    """Is ``quoted`` on the page, allowing only for the slips of a machine reading?

    Exact matches pass. Otherwise the quotation must match a run of the same number of
    words on the page at a character similarity of at least ``QUOTE_SIMILARITY``: enough
    to forgive "a neans" for "a means" in an OCR page, not enough to accept a rewording.
    """
    import difflib

    wanted = _squash(quoted)
    page = _squash(source)
    if not wanted or wanted in page:
        return bool(wanted)
    words = wanted.split()
    tokens = page.split()
    size = len(words)
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(wanted)
    for slack in (0, -1, 1):
        span = size + slack
        if span < 1:
            continue
        for start in range(0, max(0, len(tokens) - span) + 1):
            matcher.set_seq1(" ".join(tokens[start:start + span]))
            if matcher.real_quick_ratio() >= QUOTE_SIMILARITY and matcher.ratio() >= QUOTE_SIMILARITY:
                return True
    return False


def check_sentence(sentence: str, passages: dict[int, Passage]) -> tuple[tuple[int, ...], bool, str, float]:
    """Is this sentence drawn from the passages it cites? Deterministic, never asks a model."""
    cites = tuple(dict.fromkeys(n for g in _CITE.findall(sentence) for n in _numbers(g)))
    body = _CITE.sub("", sentence).strip()
    if _squash(body) == _squash(NO_ANSWER):
        return cites, True, "says the passages do not answer", 1.0
    if not cites:
        return cites, False, "cites no passage", 0.0
    unknown = [n for n in cites if n not in passages]
    if unknown:
        return cites, False, f"cites passage {unknown[0]}, which was not given", 0.0
    cited = [passages[n] for n in cites]
    source = "\n".join(p.text for p in cited)
    allowed_stems = {_stem(w) for w in _WORD.findall(source)}
    # A book's title, and the curator's note on it, may be named in a sentence about it.
    allowed_stems |= {_stem(w) for p in cited for w in _WORD.findall(p.book_title)}
    allowed_stems |= {_stem(w) for p in cited if p.note for w in _WORD.findall(p.note)}
    body = re.sub(r"(?<!\w)[*_]{1,2}|[*_]{1,2}(?!\w)", "", body)
    for quoted in _QUOTED.findall(body):
        if len(_WORD.findall(quoted)) >= 2 and not quote_on_page(quoted, source):
            return cites, False, f"quotes “{quoted[:60]}”, which is not in its page", 0.0
    for number in re.findall(r"\b\d+\b", body):
        if number not in source:
            return cites, False, f"gives the number {number}, which is not in its page", 0.0
    words = _WORD.findall(body)
    for position, word in enumerate(words):
        capital = word[:1].isupper() and position > 0 and not word.isupper()
        if capital and _fold(word) not in _STOP and _stem(word) not in allowed_stems:
            return cites, False, f"names “{word}”, which is not in its page", 0.0
    content = _content_words(body)
    if not content:
        return cites, False, "says nothing that can be checked", 0.0
    found = sum(1 for w in content if _stem(w) in allowed_stems)
    support = found / len(content)
    if support < SUPPORT_THRESHOLD:
        return cites, False, f"only {round(support * 100)}% of its words are in its page", support
    return cites, True, "drawn from its page", support


def strip_citations(sentence: str) -> str:
    """The sentence without its [n] marks, for display beside separate citation links."""
    text = " ".join(_CITE.sub("", sentence).split())
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def check_answer(text: str, passages: Sequence[Passage]) -> list[SentenceCheck]:
    by_number = {p.number: p for p in passages}
    checks = []
    for paragraph, sentence in split_sentences(text):
        cites, kept, reason, support = check_sentence(sentence, by_number)
        checks.append(SentenceCheck(sentence, cites, kept, reason, round(support, 3), paragraph))
    return checks


# ------------------------------------------------------------------ the whole answer
@dataclass(slots=True)
class CitedAnswer:
    case_id: str
    question: str
    depth: str
    model: str
    model_label: str
    passages: list[Passage]
    sentences: list[SentenceCheck]
    run_id: str | None
    seconds: float
    tokens_per_second: float | None
    warnings: list[str] = field(default_factory=list)

    @property
    def kept(self) -> list[SentenceCheck]:
        return [s for s in self.sentences if s.kept]

    @property
    def withheld(self) -> list[SentenceCheck]:
        return [s for s in self.sentences if not s.kept]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "question": self.question, "depth": self.depth,
            "model": self.model, "model_label": self.model_label,
            "passages": [p.to_dict() for p in self.passages],
            "sentences": [s.to_dict() for s in self.sentences],
            "kept": len(self.kept), "withheld": len(self.withheld),
            "answered": any(s.kept and s.cites for s in self.sentences),
            "run_id": self.run_id, "seconds": round(self.seconds, 1),
            "tokens_per_second": self.tokens_per_second, "warnings": self.warnings,
        }


def answer_question(
    database: Database,
    question: str,
    *,
    depth: str,
    writer: Writer,
    research_editions: set[str],
    embedding_editions: set[str],
    titles: dict[str, str],
    notes: dict[str, str | None],
    meaning: Callable[[str, int], list[Any]] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> CitedAnswer:
    """Gather, write, check and record one cited answer."""
    emit = on_event or (lambda event: None)
    database.require_current()
    question = " ".join(question.split())
    if len(question) < 8:
        raise AnswerError("Write a question of at least a few words.")
    choices = model_choices()
    if depth not in choices:
        raise AnswerError(f"unknown depth {depth!r}")
    choice = choices[depth]
    status = writer.status()
    if not status.get("running"):
        raise WriterUnavailable(
            "The local model service (Ollama) is not running, so no answer can be written. "
            "Start Ollama and ask again; gathering the pages still works without it.")
    if choice.model not in status.get("models", {}):
        raise WriterUnavailable(
            f"The model {choice.model} is not installed. Run: ollama pull {choice.model}")

    started = time.time()
    emit({"type": "stage", "stage": "gathering"})
    passages = gather_passages(
        database, question, research_editions=research_editions,
        embedding_editions=embedding_editions, titles=titles, notes=notes, meaning=meaning)
    case_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO research_cases (id, question, scope, corpus_snapshot, state, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (case_id, question, json.dumps({"kind": "cited_answer", "depth": depth,
                                            "model": choice.model}, sort_keys=True),
             json.dumps(database.coverage(), sort_keys=True), "retrieving", utcnow()))
    stored: list[Passage] = []
    for p in passages:
        evidence_id = add_evidence(
            database, edition_id=p.edition_id, page_id=p.page_id, evidence_kind="passage",
            evidence_class="source_explicit", quoted_text=p.text,
            locator={"page_index": p.page_index, "page_label": p.page_label,
                     "answer_case": case_id, "passage_number": p.number})
        stored.append(replace(p, evidence_id=evidence_id))
    passages = stored
    emit({"type": "passages", "count": len(passages),
          "books": len({p.edition_id for p in passages})})

    if not passages:
        _finish_case(database, case_id, "complete", "No passage bears on this question.")
        return CitedAnswer(case_id, question, depth, choice.model, choice.label, [],
                           [SentenceCheck(NO_ANSWER, (), True, "nothing was found", 1.0, 0)],
                           None, time.time() - started, None)

    _set_state(database, case_id, "analyzing")
    emit({"type": "stage", "stage": "writing", "model": choice.label})
    options = {"temperature": 0.2, "num_ctx": 8192, "num_predict": 700}
    run_id = new_id()
    run_started = utcnow()
    last = [0.0]

    def progress(chars: int) -> None:
        now = time.time()
        if now - last[0] > 0.7:
            last[0] = now
            emit({"type": "progress", "words": max(1, chars // 6)})

    try:
        written = writer.write(choice.model, build_messages(question, passages), options, progress)
    except WriterUnavailable:
        _record_run(database, run_id, choice, status, options, run_started, "failed", {})
        _finish_case(database, case_id, "failed", None)
        raise
    emit({"type": "stage", "stage": "checking"})
    _set_state(database, case_id, "verifying")
    checks = check_answer(written["text"], passages)
    if not checks:
        checks = [SentenceCheck("(The model wrote nothing.)", (), False, "empty answer", 0.0, 0)]

    by_number = {p.number: p for p in passages}
    final: list[SentenceCheck] = []
    for check in checks:
        claim_id = create_claim(database, statement=check.text, claim_type="model_inferred",
                                research_case_id=case_id, created_by_run=run_id)
        if check.kept and check.cites:
            for number in check.cites:
                link_claim(database, claim_id, by_number[number].evidence_id, "source_of_claim")
            report = verify_claims(database, [claim_id])
            if not report.ok:
                check = SentenceCheck(check.text, check.cites, False,
                                      "its citation did not resolve to a page", check.support,
                                      check.paragraph)
        if not check.kept:
            with database.transaction() as connection:
                connection.execute("UPDATE claims SET status='rejected' WHERE id=?", (claim_id,))
        final.append(SentenceCheck(check.text, check.cites, check.kept, check.reason,
                                   check.support, check.paragraph, claim_id))

    eval_count = written.get("eval_count") or 0
    eval_ns = written.get("eval_duration") or 0
    rate = round(eval_count / (eval_ns / 1e9), 1) if eval_ns else None
    metrics = {"passages": len(passages), "sentences": len(final),
               "kept": sum(1 for s in final if s.kept),
               "withheld": sum(1 for s in final if not s.kept),
               "eval_tokens": eval_count, "prompt_tokens": written.get("prompt_eval_count"),
               "tokens_per_second": rate, "seconds": round(time.time() - started, 1)}
    _record_run(database, run_id, choice, status, options, run_started, "succeeded", metrics)
    answer = CitedAnswer(case_id, question, depth, choice.model, choice.label, passages, final,
                         run_id, time.time() - started, rate)
    if any(p.note for p in passages):
        answer.warnings.append("Some passages come from books that carry a curator's note.")
    if any(p.uncertain_reading for p in passages):
        answer.warnings.append("Some passages were read by machine from old print and may"
                               " contain reading errors.")
    _finish_case(database, case_id, "complete", render_answer(answer))
    return answer


def _set_state(database: Database, case_id: str, state: str) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE research_cases SET state=? WHERE id=?", (state, case_id))


def _finish_case(database: Database, case_id: str, state: str, dossier: str | None) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE research_cases SET state=?, dossier=?, completed_at=? WHERE id=?",
            (state, dossier, utcnow(), case_id))


def _record_run(database: Database, run_id: str, choice: ModelChoice, status: dict[str, Any],
                options: dict[str, Any], started: str, outcome: str,
                metrics: dict[str, Any]) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO model_runs (id, task_type, provider, model_name, model_digest,"
            " runtime_version, prompt_version, parameters, hardware_profile, started_at,"
            " completed_at, outcome, metrics) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, "cited_answer", "ollama", choice.model,
             str(status.get("models", {}).get(choice.model, "")),
             f"ollama-{status.get('version')}", PROMPT_VERSION,
             json.dumps({**options, "think": False, "depth": choice.depth}, sort_keys=True),
             "{}", started, utcnow(), outcome, json.dumps(metrics, sort_keys=True)))


def render_answer(answer: CitedAnswer) -> str:
    """The answer as a Markdown dossier, stored with its research case."""
    lines = [f"# Cited answer — {answer.question}", "",
             f"Written by {answer.model_label} (`{answer.model}`) on this computer from "
             f"{len(answer.passages)} passages, then checked sentence by sentence. "
             f"{len(answer.kept)} kept, {len(answer.withheld)} withheld.", ""]
    paragraph = None
    current: list[str] = []
    for s in answer.kept:
        if paragraph is not None and s.paragraph != paragraph:
            lines.extend([" ".join(current), ""])
            current = []
        paragraph = s.paragraph
        current.append(s.text)
    if current:
        lines.extend([" ".join(current), ""])
    lines.extend(["## Passages", ""])
    for p in answer.passages:
        lines.append(f"[{p.number}] {p.book_title}, page {p.page_label} — evidence `{p.evidence_id}`")
    if answer.withheld:
        lines.extend(["", "## Withheld sentences", ""])
        for s in answer.withheld:
            lines.append(f"- {s.text} — {s.reason}")
    return "\n".join(lines) + "\n"


def iter_events(run: Callable[[Callable[[dict[str, Any]], None]], CitedAnswer]
                ) -> Iterator[dict[str, Any]]:
    """Run ``run`` on a thread and yield its events as they come, then the answer."""
    import queue
    import threading

    events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

    def target() -> None:
        try:
            result = run(events.put)
            events.put({"type": "answer", **result.to_dict()})
        except AnswerError as exc:
            events.put({"type": "error", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported to the reader, not swallowed
            events.put({"type": "error", "detail": f"The answer failed: {exc}"})
        finally:
            events.put(None)

    threading.Thread(target=target, daemon=True).start()
    while True:
        event = events.get()
        if event is None:
            return
        yield event
