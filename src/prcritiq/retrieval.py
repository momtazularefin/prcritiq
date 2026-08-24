"""Medium-depth context retrieval over a repository snapshot.

ADR-005 sets the depth: more than grep, far less than a symbol graph. Retrieval
combines four structural signals with one ranking signal, and every returned
chunk carries the reason it was chosen so the evidence trail in a finding can
name it rather than gesture at "context".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final, Protocol

from .chunking import SourceChunk, chunk_source, python_imports, tokenize_identifiers
from .config import AccelerationMode, ConfigError, Settings, SimilarityMode
from .diff import FileDiff
from .guardrails import is_indexable_path

# Structural signals outrank pure text similarity: a file the changed code
# imports is related whether or not it shares vocabulary with it. The weights are
# spaced by more than _MAX_SIMILARITY_BONUS so similarity orders chunks within a
# tier without ever promoting one across tiers.
_REASON_WEIGHTS: Final[dict[str, float]] = {
    "imported_by_changed_file": 4.0,
    "test_for_changed_symbol": 3.0,
    "same_directory": 2.0,
    "lexical_similarity": 1.0,
}

_MAX_SIMILARITY_BONUS: Final = 0.9

#: Share of the context budget reserved for the changed code itself. Without a
#: split, a large pull request fills the whole budget with its own diff and
#: retrieval returns nothing the reviewer did not already have.
_FOCUS_BUDGET_SHARE: Final = 0.7

_BM25_K1: Final = 1.5
_BM25_B: Final = 0.75


class SimilarityProvider(Protocol):
    """Ranks indexed chunks against a query."""

    def rank(self, query_tokens: Sequence[str], chunks: Sequence[SourceChunk]) -> dict[str, float]:
        """Return a score per chunk id. Absent ids score zero."""


@dataclass
class LexicalSimilarity:
    """BM25 over identifier tokens.

    Identifier-aware lexical matching is a deliberate default for code rather
    than a placeholder for embeddings: it is deterministic, needs no model
    download, and matches on the names that actually connect one region of a
    codebase to another.
    """

    _document_frequency: Counter[str] = field(default_factory=Counter)
    _lengths: dict[str, int] = field(default_factory=dict)
    _tokens: dict[str, Counter[str]] = field(default_factory=dict)
    _average_length: float = 0.0

    def fit(self, chunks: Sequence[SourceChunk]) -> None:
        for chunk in chunks:
            tokens = Counter(tokenize_identifiers(chunk.text))
            self._tokens[chunk.chunk_id] = tokens
            self._lengths[chunk.chunk_id] = sum(tokens.values())
            self._document_frequency.update(tokens.keys())
        total = sum(self._lengths.values())
        self._average_length = total / len(self._lengths) if self._lengths else 0.0

    def rank(self, query_tokens: Sequence[str], chunks: Sequence[SourceChunk]) -> dict[str, float]:
        if not self._lengths or not query_tokens:
            return {}
        corpus_size = len(self._lengths)
        query = Counter(query_tokens)
        scores: dict[str, float] = {}
        for chunk in chunks:
            tokens = self._tokens.get(chunk.chunk_id)
            if tokens is None:
                continue
            length = self._lengths[chunk.chunk_id] or 1
            score = 0.0
            for term in query:
                frequency = tokens.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                idf = math.log(
                    1 + (corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * length / (self._average_length or 1)
                )
                score += idf * (frequency * (_BM25_K1 + 1)) / denominator
            if score > 0:
                scores[chunk.chunk_id] = score
        return scores


def build_similarity_provider(settings: Settings) -> SimilarityProvider:
    """Select the similarity provider, refusing anything not actually available.

    ADR-013 forbids silent fallback. Requesting embeddings, or requesting GPU or
    NPU execution for retrieval that has no accelerated path, fails here instead
    of quietly running something else and reporting it as what was asked for.
    """

    if settings.similarity is SimilarityMode.EMBEDDING:
        raise ConfigError(
            "PRCRITIQ_SIMILARITY=embedding is not implemented yet. Retrieval ranking "
            "is lexical in this milestone; set PRCRITIQ_SIMILARITY=lexical to proceed."
        )
    if settings.acceleration is not AccelerationMode.NONE:
        raise ConfigError(
            f"ACCELERATION={settings.acceleration.value} was requested, but lexical "
            "retrieval has no GPU or NPU path. Set ACCELERATION=none, or wait for an "
            "embedding provider that can use the requested device."
        )
    return LexicalSimilarity()


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk of context with the reason it was retrieved."""

    chunk: SourceChunk
    reason: str
    score: float


@dataclass
class _Candidate:
    """A related chunk being scored, before it is frozen into a result."""

    chunk: SourceChunk
    reason: str
    structural: float
    similarity: float = 0.0

    @property
    def score(self) -> float:
        return self.structural + self.similarity


@dataclass(frozen=True)
class RetrievalResult:
    """Context assembled for one review run."""

    focus: tuple[RetrievedChunk, ...]
    related: tuple[RetrievedChunk, ...]
    total_bytes: int
    truncated: bool

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(item.chunk.chunk_id for item in (*self.focus, *self.related))


class SourceIndex:
    """A chunked, queryable snapshot of the repository under review."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        # Filtered here rather than by the caller: vendored, generated, and unsafe
        # files must never reach retrieval, whatever assembled the source map.
        self._sources = {path: text for path, text in sources.items() if is_indexable_path(path)}
        self._chunks: list[SourceChunk] = []
        for path, text in sorted(self._sources.items()):
            self._chunks.extend(chunk_source(path, text))
        self._by_path: dict[str, list[SourceChunk]] = {}
        for chunk in self._chunks:
            self._by_path.setdefault(chunk.path, []).append(chunk)

    @property
    def chunks(self) -> tuple[SourceChunk, ...]:
        return tuple(self._chunks)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_path))

    def source(self, path: str) -> str | None:
        return self._sources.get(path)

    def chunks_for(self, path: str) -> tuple[SourceChunk, ...]:
        return tuple(self._by_path.get(path, ()))

    def resolve_import(self, module: str) -> str | None:
        """Map a dotted Python module name onto an indexed repository path."""

        relative = module.replace(".", "/")
        candidates = (f"{relative}.py", f"{relative}/__init__.py")
        for candidate in candidates:
            for path in self._by_path:
                if path == candidate or path.endswith(f"/{candidate}"):
                    return path
        return None


def _is_test_path(path: str) -> bool:
    name = PurePosixPath(path).name
    parts = PurePosixPath(path).parts[:-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or any(part in {"test", "tests"} for part in parts)
    )


def _changed_symbols(index: SourceIndex, file_diffs: Sequence[FileDiff]) -> set[str]:
    symbols: set[str] = set()
    for file_diff in file_diffs:
        changed = file_diff.commentable_lines
        for chunk in index.chunks_for(file_diff.path):
            if chunk.overlaps(changed) and chunk.symbol_type != "module_level":
                symbols.add(chunk.symbol.split(".")[-1])
    return symbols


def retrieve_context(
    index: SourceIndex,
    file_diffs: Sequence[FileDiff],
    settings: Settings,
    similarity: SimilarityProvider | None = None,
) -> RetrievalResult:
    """Assemble review context for the reviewable files of a pull request.

    Focus chunks are the regions the pull request actually changed. Related
    chunks come from imports, nearby tests, directory siblings, and lexical
    ranking, in that order of structural confidence, trimmed to the configured
    budget.
    """

    provider = similarity if similarity is not None else build_similarity_provider(settings)
    if isinstance(provider, LexicalSimilarity):
        provider.fit(index.chunks)

    focus_budget_bytes = int(settings.max_context_bytes * _FOCUS_BUDGET_SHARE)
    focus_budget_chunks = max(1, int(settings.max_context_chunks * _FOCUS_BUDGET_SHARE))

    focus: list[RetrievedChunk] = []
    focus_ids: set[str] = set()
    focus_bytes = 0
    truncated = False
    changed_paths = {file_diff.path for file_diff in file_diffs}
    for file_diff in file_diffs:
        for chunk in index.chunks_for(file_diff.path):
            if not chunk.overlaps(file_diff.commentable_lines):
                continue
            size = len(chunk.text.encode("utf-8"))
            if len(focus) >= focus_budget_chunks or focus_bytes + size > focus_budget_bytes:
                truncated = True
                continue
            focus.append(RetrievedChunk(chunk, "changed_in_pull_request", math.inf))
            focus_ids.add(chunk.chunk_id)
            focus_bytes += size

    candidates: dict[str, _Candidate] = {}

    def offer(chunk: SourceChunk, reason: str) -> None:
        """Record the strongest structural reason a chunk is related."""

        if chunk.chunk_id in focus_ids:
            return
        weight = _REASON_WEIGHTS[reason]
        existing = candidates.get(chunk.chunk_id)
        if existing is None:
            candidates[chunk.chunk_id] = _Candidate(chunk, reason, weight)
        elif weight > existing.structural:
            existing.reason = reason
            existing.structural = weight

    for path in sorted(changed_paths):
        source = index.source(path)
        if source is not None:
            for module in sorted(python_imports(source)):
                resolved = index.resolve_import(module)
                if resolved and resolved not in changed_paths:
                    for chunk in index.chunks_for(resolved):
                        offer(chunk, "imported_by_changed_file")

        directory = str(PurePosixPath(path).parent)
        for sibling in index.paths:
            if sibling in changed_paths:
                continue
            if str(PurePosixPath(sibling).parent) == directory:
                for chunk in index.chunks_for(sibling):
                    offer(chunk, "same_directory")

    symbols = {symbol.lower() for symbol in _changed_symbols(index, file_diffs)}
    if symbols:
        for path in index.paths:
            if path in changed_paths or not _is_test_path(path):
                continue
            for chunk in index.chunks_for(path):
                if symbols.intersection(tokenize_identifiers(chunk.text)):
                    offer(chunk, "test_for_changed_symbol")

    query_tokens: list[str] = []
    for item in focus:
        query_tokens.extend(tokenize_identifiers(item.chunk.text))
    if query_tokens:
        rankable = [
            chunk
            for chunk in index.chunks
            if chunk.chunk_id not in focus_ids and chunk.path not in changed_paths
        ]
        ranked = provider.rank(query_tokens, rankable)
        highest = max(ranked.values(), default=0.0)
        by_id = {chunk.chunk_id: chunk for chunk in rankable}
        for chunk_id, score in ranked.items():
            chunk = by_id[chunk_id]
            # Normalized so similarity orders chunks inside a structural tier
            # without ever lifting one above a stronger structural signal.
            bonus = _MAX_SIMILARITY_BONUS * (score / highest) if highest else 0.0
            offer(chunk, "lexical_similarity")
            candidate = candidates.get(chunk.chunk_id)
            if candidate is not None:
                candidate.similarity = bonus

    ordered = sorted(
        candidates.values(),
        key=lambda item: (-item.score, item.chunk.path, item.chunk.start_line),
    )

    related: list[RetrievedChunk] = []
    total_bytes = focus_bytes
    for item in ordered:
        if len(focus) + len(related) >= settings.max_context_chunks:
            truncated = True
            break
        size = len(item.chunk.text.encode("utf-8"))
        if total_bytes + size > settings.max_context_bytes:
            truncated = True
            continue
        related.append(RetrievedChunk(item.chunk, item.reason, item.score))
        total_bytes += size

    return RetrievalResult(
        focus=tuple(focus),
        related=tuple(related),
        total_bytes=total_bytes,
        truncated=truncated,
    )


def index_from_workspace(
    root_files: Iterable[tuple[str, str]],
) -> SourceIndex:
    """Build an index from `(path, text)` pairs already read off disk."""

    return SourceIndex(dict(root_files))
