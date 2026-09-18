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
    certify_dataset,
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
    case_is_certified,
    classify_comment,
    is_likely_defect_comment,
    is_meaningful_comment,
    is_resolution_reply,
    is_reviewable_source_path,
    labels_from_comments,
    labels_from_review_revision,
    read_dataset,
    select_confirmed_cases,
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
        "adjudication_notes": "Verified against the changed behavior.",
        "human_approved": True,
        "adjudicator": "test-human",
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

    @pytest.mark.parametrize(
        "body",
        [
            "Thanks, fixed in the next commit.",
            "added tests for both branches",
            "Good catch — this is updated now.",
            "Good point! Reverted the change.",
            "I've made all the requested changes.",
            "thanks removed now :) ",
        ],
    )
    def test_completed_author_replies_are_high_signal(self, body: str) -> None:
        assert is_resolution_reply(body)

    @pytest.mark.parametrize(
        "body",
        [
            "I will add tests later.",
            "This is not fixed because the current behavior is intentional.",
            "I don't think we should change this.",
            "You're right, but let's address this in another pull request.",
        ],
    )
    def test_future_or_rejected_changes_are_not_resolution_evidence(self, body: str) -> None:
        assert not is_resolution_reply(body)

    @pytest.mark.parametrize(
        "body",
        [
            "This will crash when the optional value is None at runtime.",
            "The old trees retain the prior encoding, silently corrupting predictions.",
            "This returns 500 instead of hiding the stale asset node.",
        ],
    )
    def test_concrete_failures_are_defect_candidates(self, body: str) -> None:
        assert is_likely_defect_comment(body)

    @pytest.mark.parametrize(
        "body",
        [
            "Nitpick, can we keep this concise and use a tuple?",
            "Can you add a docstring explaining this wrapper?",
            "Please remove the stale comment here.",
            "I could be wrong, but this looks weird and may fail.",
            "I may be wrong, but wouldn't this make the decoder simpler?",
            "Can't this be handled by a page-level redirect?",
            "```suggestion\nraise BetterError()\n```",
            "We should add a scenario to the integration tests for this.",
            "What do you think about adding a deserialization hook here?",
            "Not a blocker; I would approve this PR as is.",
            "Code LGTM but not sure if it should error or return empty.",
        ],
    )
    def test_style_and_documentation_requests_are_not_defect_candidates(self, body: str) -> None:
        assert not is_likely_defect_comment(body)

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("src/app.py", True),
            ("package/runtime.py", True),
            ("tests/test_runtime.py", False),
            ("docs/conf.py", False),
            ("examples/demo.py", False),
            ("src/native.pyx", False),
        ],
    )
    def test_ground_truth_candidates_target_product_python(self, path: str, expected: bool) -> None:
        assert is_reviewable_source_path(path) is expected

    def test_review_revision_uses_original_commit_line_and_author_resolution(self) -> None:
        comments = [
            {
                "id": 7,
                "pull_request_review_id": 99,
                "body": "This duplicates mode in the constructor docstring and misstates the API.",
                "path": "src/app.py",
                "line": None,
                "original_line": 12,
                "commit_id": "final",
                "original_commit_id": "reviewed",
                "user": {"login": "reviewer", "type": "User"},
            },
            {
                "id": 8,
                "in_reply_to_id": 7,
                "body": "Thanks, fixed in the next commit.",
                "commit_id": "final",
                "user": {"login": "author", "type": "User"},
            },
        ]

        labels = labels_from_review_revision(
            comments,
            reviews={99: "CHANGES_REQUESTED"},
            pr_author_login="author",
            revision_sha="reviewed",
        )

        assert len(labels) == 1
        assert labels[0].line == 12
        assert labels[0].review_commit_sha == "reviewed"
        assert labels[0].review_state == "CHANGES_REQUESTED"
        assert labels[0].author_resolution_comment_id == 8
        assert labels[0].author_resolution_commit_sha == "final"
        assert labels[0].author_resolution.startswith("Thanks, fixed")

    def test_requested_changes_signal_does_not_require_an_author_reply(self) -> None:
        comments = [
            {
                "id": 7,
                "pull_request_review_id": 99,
                "body": "This will crash when the optional value is None at runtime.",
                "path": "src/app.py",
                "original_line": 12,
                "original_commit_id": "reviewed",
                "user": {"login": "reviewer", "type": "User"},
            }
        ]

        accepted = labels_from_review_revision(
            comments,
            reviews={99: "CHANGES_REQUESTED"},
            pr_author_login="author",
            revision_sha="reviewed",
            signal="accepted",
        )
        requested = labels_from_review_revision(
            comments,
            reviews={99: "CHANGES_REQUESTED"},
            pr_author_login="author",
            revision_sha="reviewed",
            signal="requested_changes",
        )
        defect = labels_from_review_revision(
            comments,
            reviews={99: "CHANGES_REQUESTED"},
            pr_author_login="author",
            revision_sha="reviewed",
            signal="defect",
        )

        assert accepted == []
        assert len(requested) == 1
        assert len(defect) == 1

    def test_deferred_issue_is_not_a_defect_candidate_for_the_current_pr(self) -> None:
        comments = [
            {
                "id": 7,
                "pull_request_review_id": 99,
                "body": "This leaks memory whenever a new client is created.",
                "path": "src/app.py",
                "original_line": 12,
                "original_commit_id": "reviewed",
                "user": {"login": "reviewer", "type": "User"},
            },
            {
                "id": 8,
                "in_reply_to_id": 7,
                "body": "This is non-blocking; let's handle it in a follow-up PR.",
                "user": {"login": "reviewer", "type": "User"},
            },
        ]

        labels = labels_from_review_revision(
            comments,
            reviews={99: "COMMENTED"},
            pr_author_login="author",
            revision_sha="reviewed",
            signal="defect",
        )

        assert labels == []

    def test_author_rejection_is_not_a_defect_candidate(self) -> None:
        comments = [
            {
                "id": 7,
                "pull_request_review_id": 99,
                "body": "This branch will fail whenever the queue is empty.",
                "path": "src/app.py",
                "original_line": 12,
                "original_commit_id": "reviewed",
                "user": {"login": "reviewer", "type": "User"},
            },
            {
                "id": 8,
                "in_reply_to_id": 7,
                "body": "This is impossible because the earlier result call raises first.",
                "user": {"login": "author", "type": "User"},
            },
        ]

        labels = labels_from_review_revision(
            comments,
            reviews={99: "COMMENTED"},
            pr_author_login="author",
            revision_sha="reviewed",
            signal="defect",
        )

        assert labels == []


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
                    "human_approved": True,
                    "adjudicator": "project-owner",
                    "category": "error_handling",
                    "severity": "medium",
                }
            ],
        )

        assert updated[0].labels[0].adjudication == "excluded"
        assert updated[0].labels[0].adjudication_notes == "author reply"
        assert updated[0].labels[0].human_approved is True
        assert updated[0].labels[0].adjudicator == "project-owner"
        assert updated[0].labels[0].category == "error_handling"
        assert updated[0].labels[0].severity == "medium"

    def test_missing_adjudications_fail_closed(self) -> None:
        restored = read_after_write(self_case())

        with pytest.raises(ValueError, match="Missing adjudications"):
            apply_adjudications([restored], [])

    def test_a_decision_without_notes_fails_closed(self) -> None:
        restored = read_after_write(self_case())

        with pytest.raises(ValueError, match="needs notes"):
            apply_adjudications(
                [restored],
                [
                    {
                        "label_id": restored.labels[0].label_id,
                        "adjudication": "confirmed_defect",
                    }
                ],
            )

    def test_provisional_final_decision_is_not_treated_as_human_ground_truth(self) -> None:
        restored = read_after_write(self_case())

        updated = apply_adjudications(
            [restored],
            [
                {
                    "label_id": restored.labels[0].label_id,
                    "adjudication": "confirmed_defect",
                    "adjudication_notes": "Agent evidence review; human sign-off pending.",
                }
            ],
        )

        decision = updated[0].labels[0]
        assert decision.adjudication == "confirmed_defect"
        assert decision.human_approved is False
        assert decision.adjudicator == ""
        assert not decision.confirmed
        assert not decision.excluded

    def test_provisional_exclusion_is_not_treated_as_a_human_exclusion(self) -> None:
        restored = read_after_write(self_case())

        updated = apply_adjudications(
            [restored],
            [
                {
                    "label_id": restored.labels[0].label_id,
                    "adjudication": "excluded",
                    "adjudication_notes": "Agent evidence review; human sign-off pending.",
                }
            ],
        )

        decision = updated[0].labels[0]
        assert decision.adjudication == "excluded"
        assert decision.human_approved is False
        assert not decision.confirmed
        assert not decision.excluded

    def test_human_approval_requires_an_identified_adjudicator(self) -> None:
        restored = read_after_write(self_case())

        with pytest.raises(ValueError, match="needs an adjudicator"):
            apply_adjudications(
                [restored],
                [
                    {
                        "label_id": restored.labels[0].label_id,
                        "adjudication": "confirmed_defect",
                        "adjudication_notes": "Verified against the changed behavior.",
                        "human_approved": True,
                    }
                ],
            )

    def test_adjudicator_must_be_a_string(self) -> None:
        restored = read_after_write(self_case())

        with pytest.raises(ValueError, match="non-string adjudicator"):
            apply_adjudications(
                [restored],
                [
                    {
                        "label_id": restored.labels[0].label_id,
                        "adjudication": "confirmed_defect",
                        "adjudication_notes": "Verified against the changed behavior.",
                        "human_approved": True,
                        "adjudicator": 42,
                    }
                ],
            )

    def test_queue_contains_provenance_and_decision_fields(self) -> None:
        restored = read_after_write(self_case())

        row = adjudication_queue([restored])[0]

        assert row["label_id"] == "legacy:c:001"
        assert row["case_id"] == "c"
        assert row["adjudication"] == "confirmed_defect"
        assert row["category"] == "bug"
        assert row["severity"] == "high"
        assert row["human_approved"] is True
        assert row["adjudicator"] == "test-human"

    def test_queue_includes_replies_and_marks_the_pr_author(self, tmp_path: Path) -> None:
        fixture = {
            "metadata": {"author_login": "author"},
            "review_comments": [
                {
                    "id": 8,
                    "in_reply_to_id": 7,
                    "body": "This object is not importable by that name, so it is not picklable.",
                    "user": "author",
                    "user_type": "User",
                    "commit_id": "head",
                }
            ],
        }
        (tmp_path / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
        case = BenchmarkCase(
            id="threaded",
            repo="example/repo",
            pr_number=1,
            base_sha="base",
            head_sha="head",
            languages=["python"],
            diff_path="fixture.json",
            human_comments_path="fixture.json",
            labels=[
                label(
                    label_id="github-review-comment:7",
                    reviewer_login="reviewer",
                    source_comment_id=7,
                    review_commit_sha="head",
                )
            ],
        )

        row = adjudication_queue([case], root=tmp_path)[0]

        assert row["pr_author_login"] == "author"
        assert row["thread_replies"][0]["is_pr_author"] is True
        assert "not picklable" in row["thread_replies"][0]["human_comment"]

    def test_confirmed_case_selection_keeps_only_scored_ground_truth(self) -> None:
        confirmed = self_case()
        excluded = BenchmarkCase(
            id="excluded",
            repo="example/repo",
            pr_number=2,
            base_sha="a",
            head_sha="b",
            languages=["python"],
            diff_path="x",
            human_comments_path="x",
            labels=[
                label(
                    label_id="github-review-comment:2",
                    adjudication="excluded",
                    adjudication_notes="Style preference, not a defect.",
                )
            ],
        )

        selected = select_confirmed_cases([confirmed, excluded])

        assert [case.id for case in selected] == [confirmed.id]

    @pytest.mark.parametrize("adjudicator", ["", "   ", 42])
    def test_approval_without_a_valid_human_identity_never_certifies(
        self, adjudicator: object
    ) -> None:
        case = self_case()
        invalid = label(adjudicator=adjudicator)
        case = BenchmarkCase(**{**case.to_json(), "labels": [invalid]})

        outcome = score_case(case, [finding()])

        assert not invalid.confirmed
        assert not case_is_certified(case)
        assert outcome.confirmed_labels == 0
        assert outcome.unadjudicated_labels == 1
        assert not aggregate([outcome]).dataset_certified


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


class TestHumanAdjudicationArtifacts:
    @staticmethod
    def _decisions(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @pytest.mark.parametrize(
        "filename",
        ["adjudications.jsonl", "adjudications-with-context.jsonl"],
    )
    def test_historical_human_queues_have_explicit_approval(self, filename: str) -> None:
        source = read_dataset(REPO_ROOT / "eval" / "candidates" / "dataset.jsonl")
        decisions = self._decisions(REPO_ROOT / "eval" / "candidates" / filename)
        expected_ids = {label.label_id for case in source for label in case.labels}

        assert len(decisions) == 18
        assert {str(row["label_id"]) for row in decisions} == expected_ids
        assert all(row["adjudication"] == "excluded" for row in decisions)
        assert all(str(row["adjudication_notes"]).strip() for row in decisions)
        assert all(row["human_approved"] is True for row in decisions)
        assert all(row["adjudicator"] == "project-owner" for row in decisions)

        once = apply_adjudications(source, decisions)
        twice = apply_adjudications(once, decisions)
        assert [case.to_json() for case in twice] == [case.to_json() for case in once]

    @pytest.mark.parametrize(
        "relative_path",
        [
            "eval/candidates/dataset-adjudicated.jsonl",
            "eval/dataset-certified.jsonl",
        ],
    )
    def test_historical_derived_datasets_preserve_human_approval(self, relative_path: str) -> None:
        labels = [
            label for case in read_dataset(REPO_ROOT / relative_path) for label in case.labels
        ]

        assert len(labels) == 18
        assert all(label.excluded for label in labels)
        assert all(label.adjudicator == "project-owner" for label in labels)

    def test_recovery_queue_is_explicitly_provisional(self) -> None:
        decisions = self._decisions(
            REPO_ROOT / "eval" / "ground-truth-candidates" / "adjudications.jsonl"
        )

        assert len(decisions) == 7
        assert sum(row["adjudication"] == "confirmed_defect" for row in decisions) == 5
        assert sum(row["adjudication"] == "excluded" for row in decisions) == 2
        assert all(row["human_approved"] is False for row in decisions)
        assert all(row["adjudicator"] == "" for row in decisions)


class TestDatasetCertification:
    def _write_case(
        self,
        tmp_path: Path,
        *,
        adjudication: str = "confirmed_defect",
        human_approved: bool | str | None = None,
        adjudicator: object | None = None,
        review_commit_sha: str = "head",
        label_id: str = "github-review-comment:7",
        source_comment_id: int | None = 7,
    ) -> BenchmarkCase:
        fixture = {
            "metadata": {
                "repo": "example/repo",
                "number": 1,
                "title": "Fix division",
                "state": "closed",
                "base_sha": "base",
                "head_sha": "head",
                "author_login": "author",
                "html_url": "https://github.com/example/repo/pull/1",
            },
            "files": [
                {
                    "filename": "src/app.py",
                    "status": "modified",
                    "additions": 2,
                    "deletions": 0,
                    "changes": 2,
                    "patch": "@@ -1,2 +1,4 @@\n import os\n \n+def retry(n):\n+    return 1 / n\n",
                    "previous_filename": None,
                }
            ],
            "review_comments": [
                {
                    "id": 7,
                    "path": "src/app.py",
                    "line": 4,
                    "original_line": 4,
                    "body": "This divides by n without checking for a zero argument first.",
                    "user": "reviewer",
                    "user_type": "User",
                    "commit_id": "head",
                    "in_reply_to_id": None,
                }
            ],
        }
        (tmp_path / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
        return BenchmarkCase(
            id="case",
            repo="example/repo",
            pr_number=1,
            base_sha="base",
            head_sha="head",
            languages=["python"],
            diff_path="fixture.json",
            human_comments_path="fixture.json",
            labels=[
                label(
                    label_id=label_id,
                    reviewer_login="reviewer",
                    source_comment_id=source_comment_id,
                    review_commit_sha=review_commit_sha,
                    adjudication=adjudication,
                    adjudication_notes="Verified against the changed behavior.",
                    human_approved=(
                        adjudication != "unreviewed" if human_approved is None else human_approved
                    ),
                    adjudicator=(
                        "project-owner"
                        if adjudicator is None and adjudication != "unreviewed"
                        else (adjudicator or "")
                    ),
                )
            ],
        )

    def test_exact_revision_human_label_is_certified(self, tmp_path: Path) -> None:
        result = certify_dataset([self._write_case(tmp_path)], tmp_path)

        assert result.certified
        assert result.certified_cases == 1
        assert result.confirmed_labels == 1
        assert result.issues == ()

    def test_unreviewed_label_is_not_certified(self, tmp_path: Path) -> None:
        result = certify_dataset([self._write_case(tmp_path, adjudication="unreviewed")], tmp_path)

        assert not result.certified
        assert any("not human-confirmed" in issue for issue in result.issues)

    @pytest.mark.parametrize("adjudication", ["confirmed_defect", "excluded"])
    def test_provisional_final_decision_is_not_certified(
        self, tmp_path: Path, adjudication: str
    ) -> None:
        result = certify_dataset(
            [
                self._write_case(
                    tmp_path,
                    adjudication=adjudication,
                    human_approved=False,
                    adjudicator="agent-evidence-review",
                )
            ],
            tmp_path,
        )

        assert not result.certified
        assert any("decision is not human-approved" in issue for issue in result.issues)

    def test_non_boolean_human_approval_is_not_certified(self, tmp_path: Path) -> None:
        result = certify_dataset(
            [self._write_case(tmp_path, human_approved="yes")],
            tmp_path,
        )

        assert not result.certified
        assert any("human_approved must be a boolean" in issue for issue in result.issues)

    def test_non_string_adjudicator_is_not_certified(self, tmp_path: Path) -> None:
        result = certify_dataset(
            [self._write_case(tmp_path, adjudicator=42)],
            tmp_path,
        )

        assert not result.certified
        assert any("adjudicator must be a string" in issue for issue in result.issues)

    def test_provisional_exclusion_cannot_hide_a_confirmed_label(self, tmp_path: Path) -> None:
        case = self._write_case(tmp_path)
        provisional_exclusion = label(
            label_id="github-review-comment:8",
            source_comment_id=7,
            reviewer_login="reviewer",
            review_commit_sha="head",
            adjudication="excluded",
            human_approved=False,
            adjudicator="",
        )
        mixed = BenchmarkCase(**{**case.to_json(), "labels": [*case.labels, provisional_exclusion]})

        result = certify_dataset([mixed], tmp_path)

        assert not result.certified
        assert any("decision is not human-approved" in issue for issue in result.issues)

    def test_legacy_or_wrong_revision_provenance_is_not_certified(self, tmp_path: Path) -> None:
        result = certify_dataset(
            [
                self._write_case(
                    tmp_path,
                    label_id="legacy:case:001",
                    source_comment_id=None,
                    review_commit_sha="old",
                )
            ],
            tmp_path,
        )

        assert not result.certified
        assert any("stable v2 source id" in issue for issue in result.issues)
        assert any("source comment id" in issue for issue in result.issues)
        assert any("review revision" in issue for issue in result.issues)


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
        assert "explicit approval" in report["limitations"][0]

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
