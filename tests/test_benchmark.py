"""Tests for the benchmark corpus, matcher, and metrics.

Every model call is mocked. These prove the measurement is correct; they cannot
prove the review is good, which is what a live run is for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prcritiq.benchmark import (
    LINE_WINDOW,
    aggregate,
    build_report,
    evaluate_gates,
    matches,
    render_markdown_report,
    run_case,
    score_case,
)
from prcritiq.config import Settings
from prcritiq.dataset import (
    BenchmarkCase,
    Label,
    adjudication_queue,
    apply_adjudications,
    classify_comment,
    is_meaningful_comment,
    labels_from_comments,
    read_dataset,
    write_dataset,
)
from prcritiq.findings import CandidateFinding, DraftedFindings, ReviewedFinding
from prcritiq.providers import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]


def label(**overrides) -> Label:
    defaults = {
        "file_path": "src/app.py",
        "line": 4,
        "category": "bug",
        "severity": "high",
        "human_comment": "This divides by n without checking for a zero argument first.",
        "expected_issue": "zero division",
        "adjudication": "confirmed_defect",
    }
    defaults.update(overrides)
    return Label(**defaults)


def finding(publish: bool = True, **overrides) -> ReviewedFinding:
    defaults = {
        "file_path": "src/app.py",
        "line": 4,
        "severity": "high",
        "confidence": 90,
        "category": "bug",
        "finding": "retry divides by n without guarding against a zero argument.",
        "evidence": "Line 4 computes 1 / n where n arrives from the caller unchecked.",
        "suggested_fix": "Raise ValueError when n is zero.",
        "source_refs": [],
    }
    defaults.update(overrides)
    return ReviewedFinding(
        candidate=CandidateFinding(**defaults),
        publish_decision="publish" if publish else "suppress",
        suppression_reason=None if publish else "generic",
    )


class TestCommentFiltering:
    @pytest.mark.parametrize(
        "body",
        [
            "LGTM, thanks for the fix here and there!",
            "nit",
            "@someone",
            "Please rebase onto main before we merge this one",
            "short",
        ],
    )
    def test_social_and_trivial_comments_are_excluded(self, body: str) -> None:
        assert not is_meaningful_comment(body)

    def test_style_comments_are_excluded(self) -> None:
        """PRCritiq does not report formatting, so it must not be scored on it."""

        assert not is_meaningful_comment(
            "Could you fix the indentation and whitespace here, it looks inconsistent"
        )

    def test_a_substantive_comment_is_kept(self) -> None:
        assert is_meaningful_comment(
            "This will raise a ZeroDivisionError when n is zero, which the caller can pass."
        )

    @pytest.mark.parametrize(
        ("body", "category"),
        [
            ("This allows SQL injection through the unescaped parameter value", "security"),
            ("This will crash with an exception when the value is None here", "bug"),
            ("There is no test covering this new branch at all, please assert it", "test_gap"),
            ("This regressed the behaviour, it no longer returns the old shape", "regression"),
        ],
    )
    def test_categories_are_guessed_from_the_reviewer_wording(
        self, body: str, category: str
    ) -> None:
        assert classify_comment(body)[0] == category

    def test_labels_need_a_path_and_a_line(self) -> None:
        comments = [
            {
                "body": "This will raise ZeroDivisionError when n is zero here",
                "path": "",
                "line": 4,
            },
            {
                "body": "This will raise ZeroDivisionError when n is zero here",
                "path": "a.py",
                "line": None,
            },
            {
                "body": "This will raise ZeroDivisionError when n is zero here",
                "path": "a.py",
                "line": 4,
            },
        ]

        assert len(labels_from_comments(comments)) == 1

    def test_authors_bots_replies_and_old_revisions_are_not_labels(self) -> None:
        body = "This will raise ZeroDivisionError when n is zero here"
        comments = [
            {"id": 1, "body": body, "path": "a.py", "line": 4, "user": "author"},
            {
                "id": 2,
                "body": body,
                "path": "a.py",
                "line": 4,
                "user": {"login": "review-bot", "type": "Bot"},
            },
            {
                "id": 3,
                "body": body,
                "path": "a.py",
                "line": 4,
                "user": "reviewer",
                "in_reply_to_id": 99,
            },
            {
                "id": 4,
                "body": body,
                "path": "a.py",
                "line": 4,
                "user": "reviewer",
                "commit_id": "old",
            },
            {
                "id": 5,
                "body": body,
                "path": "a.py",
                "line": 4,
                "user": "reviewer",
                "commit_id": "head",
            },
        ]

        labels = labels_from_comments(
            comments,
            pr_author_login="author",
            head_sha="head",
        )

        assert [item.source_comment_id for item in labels] == [5]
        assert labels[0].reviewer_login == "reviewer"
        assert labels[0].adjudication == "unreviewed"


class TestMatching:
    def test_a_finding_matching_the_human_comment(self) -> None:
        assert matches(finding(), label())

    def test_a_different_file_never_matches(self) -> None:
        assert not matches(finding(file_path="src/other.py"), label())

    def test_a_distant_line_does_not_match(self) -> None:
        assert not matches(finding(line=4 + LINE_WINDOW + 1), label())

    def test_a_nearby_line_still_matches(self) -> None:
        """Reviewers often comment a line or two from the one they mean."""

        assert matches(finding(line=4 + LINE_WINDOW), label())

    def test_unrelated_wording_does_not_match(self) -> None:
        unrelated = finding(
            finding="The logging configuration silently discards handler errors.",
            evidence="The logger swallows every exception raised by a handler.",
        )

        assert not matches(unrelated, label())


class TestScoring:
    def test_a_matched_label_counts_once(self) -> None:
        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label()],
        )

        outcome = score_case(case, [finding()])

        assert outcome.matched_labels == ["src/app.py:4"]
        assert outcome.matched_findings == 1
        assert outcome.missed_labels == []

    def test_an_unmatched_finding_is_recorded(self) -> None:
        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label()],
        )
        noise = finding(
            line=4,
            finding="The logging configuration silently discards handler errors.",
            evidence="The logger swallows every exception raised by a handler.",
        )

        outcome = score_case(case, [noise])

        assert outcome.matched_findings == 0
        assert outcome.unmatched_findings == ["src/app.py:4"]
        assert outcome.missed_labels == ["src/app.py:4"]

    def test_invalid_lines_are_checked_against_the_real_diff(self) -> None:
        """AC8 must be verified, not assumed from the fact that critique ran."""

        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[],
        )

        outcome = score_case(case, [finding()], valid_targets={("src/app.py", 99)})

        assert outcome.invalid_line == 1

    def test_suppressed_spam_does_not_count_as_published(self) -> None:
        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[],
        )

        outcome = score_case(case, [finding(publish=False)])

        assert outcome.published == 0
        assert outcome.spam == 1

    def test_matching_is_one_to_one(self) -> None:
        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label(), label(line=5)],
        )

        outcome = score_case(case, [finding()])

        assert len(outcome.matched_pairs) == 1
        assert outcome.matched_findings == 1
        assert len(outcome.missed_labels) == 1

    def test_invalid_label_targets_are_excluded_and_reported(self) -> None:
        case = BenchmarkCase(
            id="c1",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label(line=99)],
        )

        outcome = score_case(case, [], valid_targets={("src/app.py", 4)})

        assert outcome.labels == 0
        assert outcome.invalid_label_targets == ["src/app.py:99"]

    def test_report_keeps_complete_findings_for_adjudication(self) -> None:
        outcome = score_case(self_case(), [finding(), finding(publish=False)])

        assert outcome.published_findings[0]["candidate"]["finding"]
        assert outcome.suppressed_findings[0]["suppression_reason"] == "generic"


class TestAggregation:
    def _case(self, **overrides):
        base = BenchmarkCase(
            id="c",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label()],
        )
        return base

    def test_recall_and_precision_are_computed_from_findings(self) -> None:
        hit = score_case(self._case(), [finding()])
        miss = score_case(self._case(), [])

        metrics = aggregate([hit, miss])

        assert metrics.labels == 2
        assert metrics.matched == 1
        assert metrics.issue_recall == 0.5
        assert metrics.comment_precision == 1.0

    def test_precision_cannot_exceed_one(self) -> None:
        """One finding answering two comments must not push precision above 1."""

        case = BenchmarkCase(
            id="c",
            repo="example/repo",
            pr_number=1,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[label(), label(line=5)],
        )

        metrics = aggregate([score_case(case, [finding()])])

        assert metrics.comment_precision <= 1.0

    def test_empty_input_is_not_a_division_error(self) -> None:
        assert aggregate([]).cases == 0


class TestGates:
    def test_a_perfect_run_passes_every_gate(self) -> None:
        outcomes = [
            score_case(
                BenchmarkCase(
                    id=f"c{index}",
                    repo="example/repo",
                    pr_number=index,
                    base_sha="a",
                    head_sha="b",
                    languages=["python"],
                    diff_path="x",
                    human_comments_path="x",
                    labels=[label()],
                ),
                [finding()],
                valid_targets={("src/app.py", 4)},
            )
            for index in range(20)
        ]

        gates = evaluate_gates(aggregate(outcomes))

        assert all(gate.passed for gate in gates), [g for g in gates if not g.passed]

    def test_a_small_corpus_fails_the_size_gate(self) -> None:
        gates = evaluate_gates(aggregate([score_case(self_case(), [finding()])]))

        assert not next(gate for gate in gates if gate.name == "corpus size").passed


def self_case() -> BenchmarkCase:
    return BenchmarkCase(
        id="c",
        repo="example/repo",
        pr_number=1,
        base_sha="a",
        head_sha="b",
        languages=["python"],
        diff_path="x",
        human_comments_path="x",
        labels=[label()],
    )


class TestDatasetRoundTrip:
    def test_a_dataset_survives_a_write_and_read(self, tmp_path: Path) -> None:
        case = self_case()

        write_dataset([case], tmp_path / "dataset.jsonl")
        restored = read_dataset(tmp_path / "dataset.jsonl")

        assert restored[0].id == case.id
        assert restored[0].labels[0].human_comment == case.labels[0].human_comment
        assert restored[0].labels[0].label_id == "legacy:c:001"

    def test_adjudications_apply_by_stable_label_id(self) -> None:
        case = self_case()
        restored = read_after_write(case)
        label_id = restored.labels[0].label_id

        updated = apply_adjudications(
            [restored],
            [
                {
                    "label_id": label_id,
                    "adjudication": "excluded",
                    "adjudication_notes": "author reply",
                }
            ],
        )

        assert updated[0].labels[0].adjudication == "excluded"
        assert updated[0].labels[0].adjudication_notes == "author reply"

    def test_missing_adjudications_fail_closed(self) -> None:
        restored = read_after_write(self_case())

        with pytest.raises(ValueError, match="Missing adjudications"):
            apply_adjudications([restored], [])

    def test_queue_contains_provenance_and_decision_fields(self) -> None:
        restored = read_after_write(self_case())

        row = adjudication_queue([restored])[0]

        assert row["label_id"] == "legacy:c:001"
        assert row["case_id"] == "c"
        assert row["adjudication"] == "confirmed_defect"


def read_after_write(case: BenchmarkCase) -> BenchmarkCase:
    """Round-trip through a real temporary-shaped JSONL payload without I/O fixtures."""

    payload = case.to_json()
    for item in payload["labels"]:
        item["label_id"] = ""
    labels = []
    for index, item in enumerate(payload.pop("labels"), start=1):
        if not item.get("label_id"):
            item["label_id"] = f"legacy:{payload['id']}:{index:03d}"
        labels.append(Label(**item))
    return BenchmarkCase(**payload, labels=labels)


class TestShippedCorpus:
    """The corpus committed to this repository, checked against the eval plan."""

    @pytest.fixture
    def cases(self) -> list[BenchmarkCase]:
        path = REPO_ROOT / "eval" / "dataset.jsonl"
        if not path.exists():
            pytest.skip("eval/dataset.jsonl has not been built")
        return read_dataset(path)

    def test_the_corpus_meets_the_minimum_size(self, cases) -> None:
        assert len(cases) >= 20

    def test_every_case_has_at_least_one_label(self, cases) -> None:
        assert all(case.labels for case in cases)

    def test_every_case_is_python(self, cases) -> None:
        assert all("python" in case.languages for case in cases)

    def test_every_fixture_referenced_by_the_dataset_exists(self, cases) -> None:
        for case in cases:
            assert (REPO_ROOT / case.diff_path).exists(), case.diff_path

    def test_labels_land_on_files_the_fixture_contains(self, cases) -> None:
        """A label on a file outside the diff could never be matched."""

        for case in cases:
            payload = json.loads((REPO_ROOT / case.diff_path).read_text(encoding="utf-8"))
            changed = {item["filename"] for item in payload["files"]}
            for item in case.labels:
                assert item.file_path in changed, f"{case.id}: {item.file_path}"


class TestFixtureModeRun:
    def test_a_mocked_run_produces_a_scored_report(self, tmp_path: Path) -> None:
        """CI proves the measurement works without a live model (NFR4)."""

        path = REPO_ROOT / "eval" / "dataset.jsonl"
        if not path.exists():
            pytest.skip("eval/dataset.jsonl has not been built")
        cases = read_dataset(path)[:2]
        provider = MockProvider(DraftedFindings(findings=[]))

        outcomes = [
            run_case(case, root=REPO_ROOT, settings=Settings(), provider=provider) for case in cases
        ]
        metrics = aggregate(outcomes)
        report = build_report(
            outcomes,
            metrics,
            model_policy="mock",
            model="mock-reviewer",
            dataset_version="dataset.jsonl",
            mode="fixture",
        )

        assert all(item.error is None for item in outcomes)
        assert metrics.cases == 2
        assert report["passed"] is False  # a two-case corpus cannot pass
        assert "unreviewed" in report["limitations"][0]

    def test_the_markdown_report_renders(self, tmp_path: Path) -> None:
        report = build_report(
            [score_case(self_case(), [finding()], valid_targets={("src/app.py", 4)})],
            aggregate([score_case(self_case(), [finding()])]),
            model_policy="mock",
            model="mock-reviewer",
            dataset_version="dataset.jsonl",
            mode="fixture",
        )

        rendered = render_markdown_report(report)

        assert "# PRCritiq benchmark" in rendered
        assert "| Issue recall |" in rendered
        assert "## Limitations" in rendered


class TestSuppressionReporting:
    def test_a_run_that_suppresses_everything_says_why(self) -> None:
        """A recall of zero must distinguish "found nothing" from "gate rejected it"."""

        case = self_case()

        outcome = score_case(case, [finding(publish=False)])

        assert outcome.published == 0
        assert outcome.suppressed_by_reason == {"generic": 1}

    def test_reasons_merge_across_cases(self) -> None:
        low = ReviewedFinding(
            candidate=finding().candidate,
            publish_decision="suppress",
            suppression_reason="low_confidence",
        )

        metrics = aggregate(
            [score_case(self_case(), [finding(publish=False)]), score_case(self_case(), [low])]
        )

        assert metrics.suppressed_by_reason == {"generic": 1, "low_confidence": 1}


class TestThresholdSweep:
    def _raw(self, confidence: int):
        from prcritiq.benchmark import RawCaseRun
        from prcritiq.diff import build_diff_index, build_file_diff

        patch = "@@ -1,2 +1,4 @@\n import os\n \n+def retry(n):\n+    return 1 / n\n"
        index = build_diff_index(
            [
                build_file_diff(
                    path="src/app.py",
                    status="modified",
                    additions=2,
                    deletions=0,
                    changes=2,
                    patch=patch,
                )
            ]
        )
        return RawCaseRun(
            case=self_case(),
            candidates=(finding(confidence=confidence).candidate,),
            valid_targets={("src/app.py", 4)},
            diff_index=index,
        )

    def test_a_candidate_below_the_threshold_is_suppressed(self) -> None:
        from prcritiq.benchmark import score_raw

        outcome = score_raw(self._raw(60), settings=Settings(), threshold=78)

        assert outcome.published == 0
        assert outcome.suppressed_by_reason == {"low_confidence": 1}

    def test_the_same_candidate_publishes_at_a_lower_threshold(self) -> None:
        """The sweep is the point: one run, several publish policies."""

        from prcritiq.benchmark import score_raw

        outcome = score_raw(self._raw(60), settings=Settings(), threshold=50)

        assert outcome.published == 1

    def test_the_sweep_reports_a_row_per_threshold(self) -> None:
        from prcritiq.benchmark import threshold_sweep

        sweep = threshold_sweep([self._raw(60)], settings=Settings(), thresholds=[50, 70, 85])

        assert [row["min_publish_confidence"] for row in sweep] == [50, 70, 85]
        assert sweep[0]["findings"] == 1
        assert sweep[2]["findings"] == 0

    def test_a_failed_case_still_scores_without_crashing_the_sweep(self) -> None:
        from prcritiq.benchmark import RawCaseRun, score_raw

        broken = RawCaseRun(case=self_case(), error="provider exploded")

        outcome = score_raw(broken, settings=Settings(), threshold=78)

        assert outcome.error == "provider exploded"
        assert outcome.published == 0


class TestContextInBenchmark:
    """Context retrieval is opt-in and must not fire by default."""

    def _case(self):
        path = REPO_ROOT / "eval" / "dataset.jsonl"
        if not path.exists():
            pytest.skip("eval/dataset.jsonl has not been built")
        return read_dataset(path)[0]

    def test_context_is_not_retrieved_by_default(self) -> None:
        from prcritiq.benchmark import execute_case

        provider = MockProvider()
        raw = execute_case(self._case(), root=REPO_ROOT, settings=Settings(), provider=provider)

        assert raw.error is None
        _system, user, _ = provider.calls[0]
        assert "<CONTEXT>" not in user

    def test_requesting_context_reaches_the_prompt(self, stub_client) -> None:
        """The stub serves an archive, so no network is touched."""

        from prcritiq.benchmark import execute_case

        provider = MockProvider()
        raw = execute_case(
            self._case(),
            root=REPO_ROOT,
            settings=Settings(),
            provider=provider,
            include_context=True,
            client=stub_client,
        )

        assert raw.error is None
        assert any(call[0] == "download_source_archive" for call in stub_client.calls)


class TestEvaluationFailures:
    def test_billing_errors_escape_execute_case_and_abort_the_batch(self) -> None:
        from prcritiq.benchmark import execute_case
        from prcritiq.providers import ProviderBillingError

        class ExhaustedProvider:
            def draft(self, **_kwargs):
                raise ProviderBillingError("no quota")

        path = REPO_ROOT / "eval" / "dataset.jsonl"
        if not path.exists():
            pytest.skip("eval/dataset.jsonl has not been built")

        with pytest.raises(ProviderBillingError, match="no quota"):
            execute_case(
                read_dataset(path)[0],
                root=REPO_ROOT,
                settings=Settings(model_policy="anthropic"),
                provider=ExhaustedProvider(),
            )
