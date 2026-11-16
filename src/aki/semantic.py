"""Meaning-based retrieval over transcribed passages.

Two embedding providers, both local at query time:

* ``model2vec`` — a static retrieval embedding model (default
  ``minishlab/potion-retrieval-32M``). It is downloaded once from Hugging Face and then
  runs offline on CPU with no PyTorch dependency.
* ``lsa`` — latent semantic analysis trained on this corpus alone (TF-IDF followed by a
  truncated SVD). It needs no download at all, so a machine with no network still gets a
  meaning leg; it is weaker on paraphrase than a trained model.

``auto`` prefers model2vec and falls back to LSA. Whichever runs is recorded in
``model_runs`` and in the index's own ``meta.json``, and every result carries the leg
that found it, so a result is never presented as coming from a model that did not run.

The index is a derived artefact under ``.aki/derived/semantic``: page text is cut into
overlapping passages so that a long page is not averaged into one vector, and a query
returns the best passage per page. Deleting the directory loses nothing canonical.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .db import Database, json_dumps, new_id, utcnow
from .gate import permitted_editions

DEFAULT_MODEL = "minishlab/potion-retrieval-32M"
PASSAGE_CHARS = 1000
PASSAGE_OVERLAP = 150
MIN_PASSAGE_LETTERS = 40
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


class SemanticUnavailable(RuntimeError):
    """No embedding provider could be loaded, or no index has been built."""


class Embedder(Protocol):
    provider: str
    model_name: str
    model_digest: str

    def fit(self, texts: Sequence[str]) -> None: ...

    def encode(self, texts: Sequence[str]) -> Any: ...

    def save(self, directory: Path) -> None: ...


def _normalise(matrix: Any) -> Any:
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class Model2VecEmbedder:
    provider = "model2vec"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from model2vec import StaticModel
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise SemanticUnavailable("model2vec is not installed") from exc
        try:
            self._model = StaticModel.from_pretrained(model_name)
        except Exception as exc:  # noqa: BLE001 - network, cache and format errors alike
            raise SemanticUnavailable(f"could not load {model_name}: {exc}") from exc
        self.model_name = model_name
        self.model_digest = f"hf:{model_name}"

    def fit(self, texts: Sequence[str]) -> None:
        return None

    def encode(self, texts: Sequence[str]) -> Any:
        return _normalise(self._model.encode(list(texts), show_progress_bar=False))

    def save(self, directory: Path) -> None:
        (directory / "model.json").write_text(json.dumps({"model_name": self.model_name}))


class LsaEmbedder:
    """TF-IDF + truncated SVD fitted on the corpus itself."""

    provider = "lsa"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions
        self.model_name = f"corpus-lsa-{dimensions}"
        self.model_digest = "unfitted"
        self._vectorizer: Any = None
        self._svd: Any = None

    def fit(self, texts: Sequence[str]) -> None:
        try:
            from sklearn.decomposition import TruncatedSVD
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError as exc:
            raise SemanticUnavailable("scikit-learn is required for the LSA provider") from exc
        small = len(texts) < 200
        self._vectorizer = TfidfVectorizer(
            sublinear_tf=True, min_df=1 if small else 2, max_df=1.0 if small else 0.5,
            max_features=150_000,
            token_pattern=r"(?u)\b[^\W\d_]{2,}\b", lowercase=True,
        )
        matrix = self._vectorizer.fit_transform(texts)
        components = max(2, min(self.dimensions, matrix.shape[1] - 1, matrix.shape[0] - 1))
        self._svd = TruncatedSVD(n_components=components, random_state=7)
        self._svd.fit(matrix)
        vocabulary = "|".join(sorted(self._vectorizer.vocabulary_))[:2_000_000]
        import hashlib

        self.model_digest = "sha256:" + hashlib.sha256(
            f"{components}:{len(texts)}:{vocabulary}".encode()
        ).hexdigest()

    def encode(self, texts: Sequence[str]) -> Any:
        if self._vectorizer is None:
            raise SemanticUnavailable("LSA provider has not been fitted")
        return _normalise(self._svd.transform(self._vectorizer.transform(list(texts))))

    def save(self, directory: Path) -> None:
        import joblib

        joblib.dump({"vectorizer": self._vectorizer, "svd": self._svd,
                     "digest": self.model_digest}, directory / "lsa.joblib")

    @classmethod
    def load(cls, directory: Path) -> "LsaEmbedder":
        import joblib

        state = joblib.load(directory / "lsa.joblib")
        embedder = cls(dimensions=int(state["svd"].n_components))
        embedder._vectorizer, embedder._svd = state["vectorizer"], state["svd"]
        embedder.model_digest = state["digest"]
        return embedder


def resolve_embedder(provider: str = "auto", model_name: str = DEFAULT_MODEL) -> Embedder:
    if provider in ("auto", "model2vec"):
        try:
            return Model2VecEmbedder(model_name)
        except SemanticUnavailable:
            if provider == "model2vec":
                raise
    if provider in ("auto", "lsa"):
        return LsaEmbedder()
    raise SemanticUnavailable(f"unknown embedding provider {provider!r}")


def split_passages(text: str, size: int = PASSAGE_CHARS, overlap: int = PASSAGE_OVERLAP
                   ) -> list[tuple[int, int]]:
    """Overlapping character windows, cut at whitespace where possible."""
    spans: list[tuple[int, int]] = []
    length = len(text)
    start = 0
    while start < length:
        end = min(length, start + size)
        if end < length:
            cut = text.rfind(" ", start + size // 2, end)
            end = cut if cut > start else end
        if len(_LETTERS.findall(text[start:end])) >= MIN_PASSAGE_LETTERS:
            spans.append((start, end))
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return spans


def index_dir(root: str | Path) -> Path:
    return Path(root) / "derived" / "semantic"


def build_index(
    database: Database,
    state_root: str | Path,
    *,
    provider: str = "auto",
    model_name: str = DEFAULT_MODEL,
    embedder: Embedder | None = None,
    batch: int = 4096,
) -> dict[str, Any]:
    """Embed every passage of every edition whose rights permit ``create_embeddings``."""
    import numpy as np

    database.require_current()
    allowed = permitted_editions(database, "create_embeddings")
    started = utcnow()
    clock = time.time()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, edition_id, text FROM chunk_records ORDER BY edition_id, page_id"
        ).fetchall()
    used_editions: set[str] = set()
    chunk_ids: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    texts: list[str] = []
    for row in rows:
        if row["edition_id"] not in allowed:
            continue
        text = str(row["text"])
        used_editions.add(str(row["edition_id"]))
        for start, end in split_passages(text):
            chunk_ids.append(str(row["id"]))
            starts.append(start)
            ends.append(end)
            texts.append(text[start:end])
    if not texts:
        raise SemanticUnavailable("no permitted passages to embed; ingest editions first")

    model = embedder if embedder is not None else resolve_embedder(provider, model_name)
    model.fit(texts)
    parts = [model.encode(texts[i:i + batch]).astype(np.float16) for i in range(0, len(texts), batch)]
    vectors = np.vstack(parts)

    target = index_dir(state_root)
    staging = target.with_name("semantic.building")
    staging.mkdir(parents=True, exist_ok=True)
    np.save(staging / "vectors.npy", vectors)
    np.save(staging / "starts.npy", np.asarray(starts, dtype=np.int32))
    np.save(staging / "ends.npy", np.asarray(ends, dtype=np.int32))
    (staging / "chunk_ids.json").write_text(json.dumps(chunk_ids))
    model.save(staging)
    meta = {
        "provider": model.provider, "model_name": model.model_name,
        "model_digest": model.model_digest, "dimensions": int(vectors.shape[1]),
        "passages": len(texts), "pages": len(set(chunk_ids)), "editions": len(used_editions),
        "passage_chars": PASSAGE_CHARS, "passage_overlap": PASSAGE_OVERLAP,
        "built_at": utcnow(), "seconds": round(time.time() - clock, 1),
    }
    (staging / "meta.json").write_text(json.dumps(meta, indent=2))
    _swap(staging, target)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO model_runs (id, task_type, provider, model_name, model_digest,"
            " runtime_version, parameters, started_at, completed_at, outcome, metrics)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), "create_embeddings", model.provider, model.model_name,
             model.model_digest, _runtime_version(), json_dumps({
                 "passage_chars": PASSAGE_CHARS, "passage_overlap": PASSAGE_OVERLAP}),
             started, utcnow(), "succeeded",
             json_dumps({"passages": len(texts), "pages": meta["pages"]})),
        )
    _CACHE.clear()
    return meta


def _swap(staging: Path, target: Path) -> None:
    import shutil

    old = target.with_name("semantic.previous")
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    staging.rename(target)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


def _runtime_version() -> str:
    import platform

    return f"python-{platform.python_version()}"


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    chunk_id: str
    score: float
    start: int
    end: int


class SemanticIndex:
    def __init__(self, directory: Path) -> None:
        import numpy as np

        if not (directory / "meta.json").exists():
            raise SemanticUnavailable("no semantic index has been built; run 'aki semantic build'")
        self.directory = directory
        self.meta = json.loads((directory / "meta.json").read_text())
        # float16 on disk halves the footprint; float32 in memory keeps queries on BLAS.
        self.vectors = np.load(directory / "vectors.npy").astype(np.float32)
        self.starts = np.load(directory / "starts.npy")
        self.ends = np.load(directory / "ends.npy")
        self.chunk_ids: list[str] = json.loads((directory / "chunk_ids.json").read_text())
        self._embedder: Embedder | None = None
        self._positions: dict[str, list[int]] | None = None

    def embedder(self) -> Embedder:
        if self._embedder is None:
            if self.meta["provider"] == "lsa":
                self._embedder = LsaEmbedder.load(self.directory)
            else:
                self._embedder = Model2VecEmbedder(self.meta["model_name"])
        return self._embedder

    def query(self, text: str, *, limit: int = 20, allowed_chunks: set[str] | None = None
              ) -> list[SemanticMatch]:
        import numpy as np

        if not text.strip():
            return []
        vector = self.embedder().encode([text])[0].astype(np.float32)
        scores = self.vectors @ vector
        order = np.argsort(-scores)
        best: dict[str, SemanticMatch] = {}
        for position in order[: limit * 40]:
            chunk_id = self.chunk_ids[int(position)]
            if chunk_id in best or (allowed_chunks is not None and chunk_id not in allowed_chunks):
                continue
            best[chunk_id] = SemanticMatch(chunk_id, float(scores[position]),
                                           int(self.starts[position]), int(self.ends[position]))
            if len(best) >= limit:
                break
        return list(best.values())

    def neighbours(self, chunk_id: str, *, limit: int = 40) -> list[SemanticMatch]:
        """Passages nearest to a page that is already in the index, best first.

        The page's own passages are averaged into one direction, so no text is re-embedded.
        Returns at most one passage per other page; the caller applies rights and filters.
        """
        import numpy as np

        if self._positions is None:
            positions: dict[str, list[int]] = {}
            for position, identifier in enumerate(self.chunk_ids):
                positions.setdefault(identifier, []).append(position)
            self._positions = positions
        own = self._positions.get(chunk_id)
        if not own:
            return []
        vector = self.vectors[own].mean(axis=0)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return []
        scores = self.vectors @ (vector / norm)
        best: dict[str, SemanticMatch] = {}
        for position in np.argsort(-scores)[: limit * 40]:
            other = self.chunk_ids[int(position)]
            if other == chunk_id or other in best:
                continue
            best[other] = SemanticMatch(other, float(scores[position]),
                                        int(self.starts[position]), int(self.ends[position]))
            if len(best) >= limit:
                break
        return list(best.values())


_CACHE: dict[str, SemanticIndex] = {}


def load_index(state_root: str | Path) -> SemanticIndex:
    directory = index_dir(state_root)
    key = str(directory.resolve())
    meta = directory / "meta.json"
    cached = _CACHE.get(key)
    if cached is not None and meta.exists() and json.loads(meta.read_text()) == cached.meta:
        return cached
    index = SemanticIndex(directory)
    _CACHE[key] = index
    return index


def index_status(state_root: str | Path) -> dict[str, Any] | None:
    meta = index_dir(state_root) / "meta.json"
    return json.loads(meta.read_text()) if meta.exists() else None
