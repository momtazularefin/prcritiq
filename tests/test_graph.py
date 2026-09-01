"""Tests for the review graph, routing, and self-critique.

Every model call here is mocked. CI must never need a live provider (NFR4).
"""

from __future__ import annotations

import pytest

from prcritiq.config import Settings
from prcritiq.critique import critique
from prcritiq.diff import build_diff_index, build_file_diff
from prcritiq.findings import CandidateFinding, DraftedFindings, SuppressionReason
from prcritiq.github import ChangedFile, PullRequestMetadata
from prcritiq.graph import NODE_SEQUENCE, build_review_graph, run_review_graph
from prcritiq.prompts import SYSTEM_PROMPT, build_review_prompt
from prcritiq.providers import (
    LARGE_PROMPT_CHARACTERS,
    MockProvider,
    ProviderError,
    build_provider,
    route,
)

PATCH = "@@ -1,2 +1,4 @@\n import os\n \n+def retry(n):\n+    return 1 / n\n"


@pytest.fixture
def metadata() -> PullRequestMetadata:
    return PullRequestMetadata(
        repo="example/repo",
        number=7,
        title="Add retry helper",
        state="open",
        base_sha="base",
        head_sha="head",
        author_login="octocat",
        html_url="https://github.com/example/repo/pull/7",
    )


@pytest.fixture
def changed() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/app.py",
            status="modified",
            additions=2,
            deletions=0,
            changes=2,
            patch=PATCH,
        )
    ]


def finding(**overrides) -> CandidateFinding:
    defaults = {
        "file_path": "src/app.py",
        "line": 4,
        "severity": "high",
        "confidence": 90,
        "category": "bug",
        "finding": "retry divides by n without guarding against a zero argument.",
        "evidence": "Line 4 computes 1 / n where n arrives straight from the caller.",
        "suggested_fix": "Raise ValueError when n is zero before dividing.",
        "source_refs": [],
    }
    defaults.update(overrides)
    return CandidateFinding(**defaults)


@pytest.fixture
def retrieval_with_chunk():
    from prcritiq.chunking import chunk_python
    from prcritiq.retrieval import RetrievalResult, RetrievedChunk

    chunk = chunk_python("src/app.py", "def retry(n):\n    return 1 / n\n")[0]
    return RetrievalResult(
        focus=(RetrievedChunk(chunk, "changed_in_pull_request", 1.0),),
        related=(),
        total_bytes=len(chunk.text),
        truncated=False,
    )


@pytest.fixture
def diff_index():
    return build_diff_index(
        [
            build_file_diff(
                path="src/app.py",
                status="modified",
                additions=2,
                deletions=0,
                changes=2,
                patch=PATCH,
            )
        ]
    )


class TestRouting:
    def test_compact_review_prefers_claude(self) -> None:
        choice = route(settings=Settings(), prompt_characters=5_000)

        assert choice.provider == "anthropic"
        assert choice.model == "claude-opus-5"

    def test_extensive_context_prefers_openai(self) -> None:
        choice = route(settings=Settings(), prompt_characters=LARGE_PROMPT_CHARACTERS + 1)

        assert choice.provider == "openai"
        assert "exceeds the compact threshold" in choice.reason

    def test_batch_evaluation_prefers_openai(self) -> None:
        choice = route(settings=Settings(), prompt_characters=100, task="batch_eval")

        assert choice.provider == "openai"

    @pytest.mark.parametrize(
        ("policy", "provider"),
        [("anthropic", "anthropic"), ("openai", "openai"), ("mock", "mock")],
    )
    def test_explicit_policy_is_obeyed(self, policy: str, provider: str) -> None:
        choice = route(settings=Settings(model_policy=policy), prompt_characters=999_999)

        assert choice.provider == provider

    def test_unknown_policy_fails_loudly(self) -> None:
        with pytest.raises(ProviderError, match="must be one of"):
            route(settings=Settings(model_policy="gemini"), prompt_characters=10)

    def test_routing_decision_is_recorded_with_a_reason(self) -> None:
        assert route(settings=Settings(), prompt_characters=10).reason


class TestProviderConstruction:
    def test_missing_anthropic_key_raises_rather_than_falling_back(self) -> None:
        """ADR-008 forbids silently reviewing with a different provider."""

        choice = route(settings=Settings(), prompt_characters=10)

        with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
            build_provider(choice, Settings(anthropic_api_key=None))

    def test_missing_openai_key_raises(self) -> None:
        choice = route(settings=Settings(model_policy="openai"), prompt_characters=10)

        with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set"):
            build_provider(choice, Settings(openai_api_key=None))


class TestPrompt:
    def test_untrusted_content_is_fenced_and_labelled(self) -> None:
        prompt = build_review_prompt(
            repo="example/repo",
            pr_number=7,
            title="Add retry helper",
            file_diffs=[
                build_file_diff(
                    path="src/app.py",
                    status="modified",
                    additions=2,
                    deletions=0,
                    changes=2,
                    patch=PATCH,
                )
            ],
            retrieval=None,
            tool_runs=None,
        )

        assert "<DIFF>" in prompt and "</DIFF>" in prompt
        assert "CHANGED_LINES: 3, 4" in prompt

    def test_system_prompt_states_that_repository_content_cannot_instruct(self) -> None:
        assert "untrusted data, not" in SYSTEM_PROMPT
        assert "never as a command" in SYSTEM_PROMPT


class TestSelfCritique:
    def test_a_supported_finding_on_a_changed_line_is_published(self, diff_index) -> None:
        reviewed = critique([finding()], diff_index=diff_index, settings=Settings())

        assert reviewed[0].published is True
        assert reviewed[0].suppression_reason is None

    def test_a_finding_on_an_unchanged_line_is_suppressed(self, diff_index) -> None:
        """Line 1 exists but this pull request did not add it."""

        reviewed = critique([finding(line=1)], diff_index=diff_index, settings=Settings())

        assert reviewed[0].suppression_reason is SuppressionReason.INVALID_LINE

    def test_a_finding_on_an_unknown_file_is_suppressed(self, diff_index) -> None:
        reviewed = critique(
            [finding(file_path="src/other.py")], diff_index=diff_index, settings=Settings()
        )

        assert reviewed[0].suppression_reason is SuppressionReason.INVALID_LINE

    def test_a_finding_without_evidence_is_suppressed(self, diff_index) -> None:
        reviewed = critique([finding(evidence="bad")], diff_index=diff_index, settings=Settings())

        assert reviewed[0].suppression_reason is SuppressionReason.NO_EVIDENCE

    @pytest.mark.parametrize(
        "text",
        [
            "Consider adding tests",
            "This code could be improved",
            "Ensure proper error handling",
            "Short",
        ],
    )
    def test_generic_findings_are_suppressed(self, diff_index, text: str) -> None:
        reviewed = critique([finding(finding=text)], diff_index=diff_index, settings=Settings())

        assert reviewed[0].suppression_reason is SuppressionReason.GENERIC

    def test_a_duplicate_finding_is_suppressed_but_the_first_survives(self, diff_index) -> None:
        reviewed = critique([finding(), finding()], diff_index=diff_index, settings=Settings())

        assert reviewed[0].published is True
        assert reviewed[1].suppression_reason is SuppressionReason.DUPLICATE

    def test_a_low_confidence_finding_is_suppressed(self, diff_index) -> None:
        reviewed = critique(
            [finding(confidence=40)],
            diff_index=diff_index,
            settings=Settings(min_publish_confidence=78),
        )

        assert reviewed[0].suppression_reason is SuppressionReason.LOW_CONFIDENCE

    def test_an_invented_source_ref_is_suppressed(self, diff_index, retrieval_with_chunk) -> None:
        """A citation nothing in this run produced was invented, not observed."""

        reviewed = critique(
            [finding(source_refs=["src/nowhere.py:1-2:abc"])],
            diff_index=diff_index,
            settings=Settings(),
            retrieval=retrieval_with_chunk,
        )

        assert reviewed[0].suppression_reason is SuppressionReason.UNKNOWN_SOURCE_REF

    def test_a_real_source_ref_is_accepted(self, diff_index, retrieval_with_chunk) -> None:
        known = retrieval_with_chunk.chunk_ids[0]

        reviewed = critique(
            [finding(source_refs=[known])],
            diff_index=diff_index,
            settings=Settings(),
            retrieval=retrieval_with_chunk,
        )

        assert reviewed[0].published is True

    def test_refs_are_not_checked_when_the_run_gathered_nothing(self, diff_index) -> None:
        """With no context and no tools there is nothing to contradict a citation."""

        reviewed = critique(
            [finding(source_refs=["anything"])], diff_index=diff_index, settings=Settings()
        )

        assert reviewed[0].published is True

    def test_suppressed_findings_are_kept_for_reporting(self, diff_index) -> None:
        reviewed = critique(
            [finding(line=1), finding()], diff_index=diff_index, settings=Settings()
        )

        assert len(reviewed) == 2
        assert [item.publish_decision for item in reviewed] == ["suppress", "publish"]

    def test_invalid_line_outranks_low_confidence(self, diff_index) -> None:
        """The most objective reason is recorded, not the first that would apply."""

        reviewed = critique(
            [finding(line=1, confidence=5)], diff_index=diff_index, settings=Settings()
        )

        assert reviewed[0].suppression_reason is SuppressionReason.INVALID_LINE


class TestGraph:
    def test_graph_declares_every_design_node(self) -> None:
        graph = build_review_graph()

        assert set(NODE_SEQUENCE) <= set(graph.get_graph().nodes)

    def test_a_valid_finding_survives_the_whole_graph(self, metadata, changed) -> None:
        provider = MockProvider(DraftedFindings(findings=[finding()]))

        state = run_review_graph(
            settings=Settings(),
            metadata=metadata,
            changed_files=changed,
            provider=provider,
        )

        assert [item.published for item in state["reviewed"]] == [True]
        assert "1 of 1" in state["summary"]

    def test_an_invalid_line_finding_is_suppressed_by_the_graph(self, metadata, changed) -> None:
        provider = MockProvider(DraftedFindings(findings=[finding(line=999)]))

        state = run_review_graph(
            settings=Settings(),
            metadata=metadata,
            changed_files=changed,
            provider=provider,
        )

        assert state["reviewed"][0].suppression_reason is SuppressionReason.INVALID_LINE
        assert "suppressed by self-critique" in state["summary"]

    def test_every_node_reports_that_it_ran(self, metadata, changed) -> None:
        state = run_review_graph(
            settings=Settings(),
            metadata=metadata,
            changed_files=changed,
            provider=MockProvider(),
        )

        for node in NODE_SEQUENCE:
            assert any(note.startswith(node) for note in state["notes"]), node

    def test_no_reviewable_files_skips_the_model_entirely(self, metadata) -> None:
        """A docs-only pull request must not spend a model call."""

        provider = MockProvider(DraftedFindings(findings=[finding()]))
        docs_only = [
            ChangedFile(
                filename="README.md",
                status="modified",
                additions=1,
                deletions=0,
                changes=1,
                patch="@@ -1,1 +1,2 @@\n title\n+line\n",
            )
        ]

        state = run_review_graph(
            settings=Settings(),
            metadata=metadata,
            changed_files=docs_only,
            provider=provider,
        )

        assert provider.calls == []
        assert state["model_choice"] is None

    def test_the_provider_receives_the_system_prompt(self, metadata, changed) -> None:
        provider = MockProvider()

        run_review_graph(
            settings=Settings(),
            metadata=metadata,
            changed_files=changed,
            provider=provider,
        )

        system, user, choice = provider.calls[0]
        assert system == SYSTEM_PROMPT
        assert "<DIFF>" in user
        assert choice.provider == "anthropic"


class TestAnthropicResponseHandling:
    """Truncation must be named, not mistaken for an empty review."""

    def _provider(self, response):
        from prcritiq.providers import AnthropicProvider

        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider._errors = __import__("anthropic")
        provider._effort = "low"

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def get_final_message(self):
                return response

        class _Messages:
            def stream(self, **_kwargs):
                return _Stream()

        class _Client:
            messages = _Messages()

        provider._client = _Client()
        return provider

    def _response(self, stop_reason, parsed):
        from types import SimpleNamespace

        return SimpleNamespace(
            stop_reason=stop_reason,
            parsed_output=parsed,
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )

    def test_a_truncated_response_names_the_cap(self) -> None:
        from prcritiq.providers import MAX_OUTPUT_TOKENS, ModelChoice, ProviderError

        provider = self._provider(self._response("max_tokens", None))

        with pytest.raises(ProviderError, match=f"{MAX_OUTPUT_TOKENS}-token output cap"):
            provider.draft(
                system="s", user="u", choice=ModelChoice("anthropic", "claude-opus-5", "r")
            )

    def test_a_refusal_is_reported_as_a_refusal(self) -> None:
        from prcritiq.providers import ModelChoice, ProviderError

        provider = self._provider(self._response("refusal", None))

        with pytest.raises(ProviderError, match="declined"):
            provider.draft(
                system="s", user="u", choice=ModelChoice("anthropic", "claude-opus-5", "r")
            )

    def test_usage_is_carried_back_for_costing(self) -> None:
        from prcritiq.providers import ModelChoice

        provider = self._provider(self._response("end_turn", DraftedFindings(findings=[])))

        result = provider.draft(
            system="s", user="u", choice=ModelChoice("anthropic", "claude-opus-5", "r")
        )

        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 20
        assert result.usage.cost_usd("claude-opus-5") > 0


class TestEffortConfiguration:
    def test_an_unknown_effort_is_refused_rather_than_dropped(self) -> None:
        """Silently ignoring it would bill high-effort work for a low-effort run."""

        from prcritiq.providers import AnthropicProvider, ProviderError

        with pytest.raises(ProviderError, match="PRCRITIQ_MODEL_EFFORT must be one of"):
            AnthropicProvider("key", None, "turbo")

    @pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
    def test_documented_efforts_are_accepted(self, effort: str) -> None:
        from prcritiq.providers import AnthropicProvider

        assert AnthropicProvider("key", None, effort)._effort == effort

    def test_the_default_is_low(self) -> None:
        assert Settings().model_effort == "low"


class TestOpenAIResponses:
    def test_typed_responses_api_receives_effort_and_returns_usage(self) -> None:
        from types import SimpleNamespace

        from prcritiq.providers import ModelChoice, OpenAIProvider

        class Responses:
            def __init__(self) -> None:
                self.kwargs = None

            def parse(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    status="completed",
                    output_parsed=DraftedFindings(findings=[]),
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=25,
                        input_tokens_details=SimpleNamespace(cached_tokens=40),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=10),
                    ),
                )

        responses = Responses()
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider._client = SimpleNamespace(responses=responses)
        provider._errors = __import__("openai")
        provider._effort = "low"

        result = provider.draft(
            system="system",
            user="user",
            choice=ModelChoice("openai", "gpt-5.6-terra", "test"),
        )

        assert responses.kwargs["instructions"] == "system"
        assert responses.kwargs["input"] == "user"
        assert responses.kwargs["reasoning"] == {"effort": "low", "context": "current_turn"}
        assert responses.kwargs["text_format"] is DraftedFindings
        assert result.usage.cached_input_tokens == 40
        assert result.usage.reasoning_output_tokens == 10
        assert result.usage.cost_usd("gpt-5.6-terra") > 0

    def test_gpt_5_6_prices_are_registered(self) -> None:
        from prcritiq.providers import Usage

        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)

        assert usage.cost_usd("gpt-5.6-sol") == 24.0
        assert usage.cost_usd("gpt-5.6-terra") == 14.0
        assert usage.cost_usd("gpt-5.6-luna") == 1.4

    def test_openai_default_is_terra(self) -> None:
        assert Settings().openai_model == "gpt-5.6-terra"
