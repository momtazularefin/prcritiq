"""Retrieval regression tests over a synthetic repository fixture."""

from __future__ import annotations

import pytest

from prcritiq.config import AccelerationMode, ConfigError, Settings, SimilarityMode
from prcritiq.diff import FileDiff, build_file_diff
from prcritiq.retrieval import (
    LexicalSimilarity,
    SourceIndex,
    build_similarity_provider,
    retrieve_context,
)

# Changes submit_order in src/app/service.py at its new lines 9 to 13.
SERVICE_PATCH = (
    "@@ -8,1 +8,6 @@\n"
    " \n"
    "+def submit_order(order: Order) -> bool:\n"
    "+    if not order.is_valid():\n"
    "+        return False\n"
    "+    send_receipt(order)\n"
    "+    return True\n"
)


@pytest.fixture
def index(repo_sources: dict[str, str]) -> SourceIndex:
    return SourceIndex(repo_sources)


@pytest.fixture
def service_diff() -> FileDiff:
    return build_file_diff(
        path="src/app/service.py",
        status="modified",
        additions=5,
        deletions=0,
        changes=5,
        patch=SERVICE_PATCH,
    )


def reasons_by_path(result) -> dict[str, str]:
    return {item.chunk.path: item.reason for item in result.related}


class TestSourceIndex:
    def test_only_indexable_languages_are_chunked(self, index: SourceIndex) -> None:
        assert "docs/guide.md" not in index.paths

    def test_import_resolution_maps_dotted_modules_to_paths(self, index: SourceIndex) -> None:
        assert index.resolve_import("app.models") == "src/app/models.py"
        assert index.resolve_import("app.nowhere") is None

    def test_chunks_are_grouped_by_path(self, index: SourceIndex) -> None:
        assert all(
            chunk.path == "src/app/models.py" for chunk in index.chunks_for("src/app/models.py")
        )


class TestRetrieval:
    def test_focus_is_the_changed_region(self, index: SourceIndex, service_diff: FileDiff) -> None:
        result = retrieve_context(index, [service_diff], Settings())

        focus_symbols = {item.chunk.symbol for item in result.focus}
        assert "submit_order" in focus_symbols
        assert all(item.reason == "changed_in_pull_request" for item in result.focus)

    def test_imported_modules_are_retrieved(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        """models and notify are imported by the changed file."""

        result = retrieve_context(index, [service_diff], Settings())
        reasons = reasons_by_path(result)

        assert reasons.get("src/app/models.py") == "imported_by_changed_file"
        assert reasons.get("src/app/notify.py") == "imported_by_changed_file"

    def test_tests_touching_changed_symbols_are_retrieved(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())
        reasons = reasons_by_path(result)

        assert reasons.get("tests/test_service.py") == "test_for_changed_symbol"

    def test_unrelated_tests_are_not_retrieved_as_tests(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        """test_pricing does not mention a changed symbol, so it earns no test signal."""

        result = retrieve_context(index, [service_diff], Settings())
        reasons = reasons_by_path(result)

        assert reasons.get("tests/test_pricing.py") != "test_for_changed_symbol"

    def test_directory_siblings_are_retrieved(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())

        assert "src/app/pricing.py" in reasons_by_path(result)

    def test_lexical_similarity_reaches_another_package(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        """summary.py is neither imported nor a sibling, but shares vocabulary."""

        result = retrieve_context(index, [service_diff], Settings())

        assert "src/reporting/summary.py" in reasons_by_path(result)

    def test_vendored_code_is_never_retrieved(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())

        assert all("vendor/" not in path for path in reasons_by_path(result))

    def test_changed_file_is_not_returned_as_related(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())

        assert "src/app/service.py" not in reasons_by_path(result)

    def test_structural_signals_outrank_similarity(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())
        ordered = [item.reason for item in result.related]
        first_import = ordered.index("imported_by_changed_file")

        assert all(
            first_import < position
            for position, reason in enumerate(ordered)
            if reason == "lexical_similarity"
        )

    def test_chunk_ids_cover_focus_and_related(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings())

        assert len(result.chunk_ids) == len(result.focus) + len(result.related)
        assert len(set(result.chunk_ids)) == len(result.chunk_ids)


class TestBudgets:
    def test_chunk_budget_truncates_and_says_so(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings(max_context_chunks=3))

        assert len(result.focus) + len(result.related) <= 3
        assert result.truncated is True

    def test_byte_budget_truncates_and_says_so(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(index, [service_diff], Settings(max_context_bytes=200))

        assert result.truncated is True
        assert result.total_bytes <= 200 + len(service_diff.patch or "")

    def test_generous_budget_is_not_truncated(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        result = retrieve_context(
            index, [service_diff], Settings(max_context_chunks=500, max_context_bytes=5_000_000)
        )

        assert result.truncated is False


class TestSimilarityProviderSelection:
    def test_lexical_on_cpu_is_the_default(self) -> None:
        assert isinstance(build_similarity_provider(Settings()), LexicalSimilarity)

    def test_embedding_mode_fails_instead_of_falling_back(self) -> None:
        """ADR-013 forbids quietly running something other than what was asked for."""

        with pytest.raises(ConfigError, match="not implemented"):
            build_similarity_provider(Settings(similarity=SimilarityMode.EMBEDDING))

    @pytest.mark.parametrize("mode", [AccelerationMode.GPU, AccelerationMode.NPU])
    def test_requested_acceleration_without_a_path_fails(self, mode: AccelerationMode) -> None:
        with pytest.raises(ConfigError, match="no GPU or NPU path"):
            build_similarity_provider(Settings(acceleration=mode))


class TestLexicalSimilarity:
    def test_ranking_prefers_the_chunk_sharing_vocabulary(self, index: SourceIndex) -> None:
        provider = LexicalSimilarity()
        provider.fit(index.chunks)

        scores = provider.rank(["discount", "percent"], index.chunks)
        best = max(scores, key=lambda key: scores[key])

        assert "pricing.py" in best

    def test_empty_query_scores_nothing(self, index: SourceIndex) -> None:
        provider = LexicalSimilarity()
        provider.fit(index.chunks)

        assert provider.rank([], index.chunks) == {}


class ExplodingSimilarity:
    """Spikes one structurally unrelated chunk, to probe the ranking ceiling.

    Scoring every chunk equally would prove nothing, since a uniform bonus keeps
    the existing order. Only a lopsided score can show whether similarity is
    capped below the structural tiers.
    """

    def __init__(self, target_path: str) -> None:
        self.target_path = target_path

    def rank(self, query_tokens, chunks):
        return {chunk.chunk_id: 10_000.0 for chunk in chunks if chunk.path == self.target_path}


class TestSimilarityCannotOverpowerStructure:
    def test_extreme_similarity_never_promotes_above_a_structural_signal(
        self, index: SourceIndex, service_diff: FileDiff
    ) -> None:
        """The bonus is normalized, so ranking orders within a tier, never across."""

        result = retrieve_context(
            index,
            [service_diff],
            Settings(max_context_chunks=500, max_context_bytes=5_000_000),
            similarity=ExplodingSimilarity("src/reporting/summary.py"),
        )
        positions = [
            position
            for position, item in enumerate(result.related)
            if item.reason == "lexical_similarity"
        ]
        structural = [
            position
            for position, item in enumerate(result.related)
            if item.reason != "lexical_similarity"
        ]

        assert positions and structural
        assert min(positions) > max(structural)
