"""Benchmark execution and the metrics the eval plan defines.

Recall alone is not the goal. A reviewer that finds real issues by posting many
weak comments fails the product, so every report carries what was suppressed
next to what was published.

The matcher here is mechanical. The eval plan calls for manual adjudication of
ambiguous matches, and this does not do that; it applies a stated rule and
records the rule, so a reader can judge the number rather than trust it.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from .chunking import tokenize_identifiers
from .dataset import BenchmarkCase, Label
from .findings import ReviewedFinding, SuppressionReason

if TYPE_CHECKING:
    from .retrieval import RetrievalResult
    from .tools import ToolRun

#: A finding may sit this many lines from the human comment and still match.
#: Reviewers often comment on the line above or below the one they mean.
LINE_WINDOW: Final = 5

#: Share of the human comment's distinctive words a finding must share.
TOKEN_OVERLAP: Final = 0.18

_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the",
        "this",
        "that",
        "with",
        "from",
        "have",
        "has",
        "was",
        "were",
        "are",
        "and",
        "but",
        "for",
        "not",
        "you",
        "your",
        "can",
        "could",
        "should",
        "would",
        "here",
        "there",
        "when",
        "what",
        "which",
        "will",
        "into",
        "than",
        "then",
        "self",
        "def",
        "return",
        "if",
        "else",
        "is",
        "it",
        "in",
        "of",
        "to",
        "a",
        "an",
        "we",
        "be",
        "on",
        "as",
        "at",
        "or",
        "by",
        "do",
        "does",
        "did",
        "so",
        "also",
    }
)


def _distinctive(text: str) -> set[str]:
    return {
        token for token in tokenize_identifiers(text) if len(token) > 2 and token not in _STOPWORDS
    }


def matches(finding: ReviewedFinding, label: Label) -> bool:
    """Decide whether a finding addresses the same issue a human raised.

    Same file, a nearby line, and meaningful shared vocabulary. Category is
    deliberately not required to agree, because a reviewer and the agent can
    describe the same defect under different headings.
    """

    candidate = finding.candidate
    if candidate.file_path != label.file_path:
        return False
    if abs(candidate.line - label.line) > LINE_WINDOW:
        return False

    human_words = _distinctive(label.human_comment)
    if not human_words:
        return False
    agent_words = _distinctive(f"{candidate.finding} {candidate.evidence}")
    shared = human_words & agent_words
    return len(shared) / len(human_words) >= TOKEN_OVERLAP


@dataclass
class CaseOutcome:
    """What one benchmark case produced."""

    case_id: str
    repo: str
    pr_number: int
    total_labels: int = 0
    labels: int = 0
    confirmed_labels: int = 0
    unadjudicated_labels: int = 0
    excluded_labels: list[str] = field(default_factory=list)
    invalid_label_targets: list[str] = field(default_factory=list)
    published: int = 0
    suppressed: int = 0
    matched_labels: list[str] = field(default_factory=list)
    matched_findings: int = 0
    matched_pairs: list[dict[str, Any]] = field(default_factory=list)
    missed_labels: list[str] = field(default_factory=list)
    unmatched_findings: list[str] = field(default_factory=list)
    published_findings: list[dict[str, Any]] = field(default_factory=list)
    suppressed_findings: list[dict[str, Any]] = field(default_factory=list)
    suppressed_by_reason: dict[str, int] = field(default_factory=dict)
    invalid_line: int = 0
    no_evidence: int = 0
    spam: int = 0
    latency_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    cost_usd: float = 0.0
    provider: str | None = None
    model: str | None = None
    model_effort: str | None = None
    route_reason: str | None = None
    error: str | None = None


@dataclass
class BenchmarkMetrics:
    """The numbers the eval plan's gates are judged on."""

    cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    labels: int = 0
    confirmed_labels: int = 0
    unadjudicated_labels: int = 0
    invalid_label_targets: int = 0
    dataset_certified: bool = False
    findings: int = 0
    matched: int = 0
    issue_recall: float = 0.0
    comment_precision: float = 0.0
    invalid_line_rate: float = 0.0
    no_evidence_rate: float = 0.0
    spam_rate: float = 0.0
    quiet_runs: int = 0
    suppressed_by_reason: dict[str, int] = field(default_factory=dict)
    median_latency_seconds: float = 0.0
    p95_latency_seconds: float = 0.0
    total_cost_usd: float = 0.0
    cost_per_pr_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0


@dataclass(frozen=True)
class DatasetCertification:
    """Preflight result for a dataset that is eligible for paid evaluation."""

    certified: bool
    cases: int
    certified_cases: int
    confirmed_labels: int
    issues: tuple[str, ...] = ()


#: Suppression reasons that count as spam the agent avoided emitting.
_SPAM_REASONS: Final[frozenset[SuppressionReason]] = frozenset(
    {SuppressionReason.GENERIC, SuppressionReason.DUPLICATE}
)


def _count_reasons(suppressed: Sequence[ReviewedFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in suppressed:
        if item.suppression_reason is not None:
            key = item.suppression_reason.value
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _finding_payload(index: int, finding: ReviewedFinding) -> dict[str, Any]:
    """Keep enough output for independent adjudication after a paid run."""

    return {
        "finding_index": index,
        "candidate": finding.candidate.model_dump(mode="json"),
        "publish_decision": finding.publish_decision,
        "suppression_reason": (
            finding.suppression_reason.value if finding.suppression_reason is not None else None
        ),
    }


def _maximum_matching(
    findings: Sequence[ReviewedFinding], labels: Sequence[Label]
) -> list[tuple[int, int]]:
    """Return a maximum-cardinality, one-to-one finding/label matching.

    The predicate is still a mechanical proxy, but one finding can no longer
    claim several labels and several findings can no longer claim one label.
    """

    edges = [
        sorted(
            (index for index, label in enumerate(labels) if matches(finding, label)),
            key=lambda index: abs(finding.candidate.line - labels[index].line),
        )
        for finding in findings
    ]
    label_owner: dict[int, int] = {}

    def assign(finding_index: int, visited: set[int]) -> bool:
        for label_index in edges[finding_index]:
            if label_index in visited:
                continue
            visited.add(label_index)
            owner = label_owner.get(label_index)
            if owner is None or assign(owner, visited):
                label_owner[label_index] = finding_index
                return True
        return False

    for finding_index in range(len(findings)):
        assign(finding_index, set())
    return sorted(
        (finding_index, label_index) for label_index, finding_index in label_owner.items()
    )


def score_case(
    case: BenchmarkCase,
    reviewed: Sequence[ReviewedFinding],
    *,
    valid_targets: set[tuple[str, int]] | None = None,
    latency_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    cost_usd: float = 0.0,
    provider: str | None = None,
    model: str | None = None,
    model_effort: str | None = None,
    route_reason: str | None = None,
    error: str | None = None,
) -> CaseOutcome:
    """Score one case against its labels."""

    published = [item for item in reviewed if item.published]
    suppressed = [item for item in reviewed if not item.published]

    eligible: list[tuple[int, Label]] = []
    excluded_labels: list[str] = []
    invalid_label_targets: list[str] = []
    for original_index, label in enumerate(case.labels):
        target = f"{label.file_path}:{label.line}"
        if label.excluded:
            excluded_labels.append(target)
        elif valid_targets is not None and (label.file_path, label.line) not in valid_targets:
            invalid_label_targets.append(target)
        else:
            eligible.append((original_index, label))

    labels = [label for _, label in eligible]
    pairs = _maximum_matching(published, labels)
    consumed_findings = {finding_index for finding_index, _ in pairs}
    consumed_labels = {label_index for _, label_index in pairs}
    matched_labels = [
        f"{labels[index].file_path}:{labels[index].line}" for index in sorted(consumed_labels)
    ]
    unmatched = [
        f"{item.candidate.file_path}:{item.candidate.line}"
        for index, item in enumerate(published)
        if index not in consumed_findings
    ]

    return CaseOutcome(
        case_id=case.id,
        repo=case.repo,
        pr_number=case.pr_number,
        total_labels=len(case.labels),
        labels=len(labels),
        confirmed_labels=sum(1 for label in labels if label.confirmed),
        unadjudicated_labels=sum(1 for label in labels if not label.confirmed),
        excluded_labels=excluded_labels,
        invalid_label_targets=invalid_label_targets,
        published=len(published),
        suppressed=len(suppressed),
        matched_labels=matched_labels,
        matched_findings=len(pairs),
        matched_pairs=[
            {
                "finding_index": finding_index,
                "label_index": eligible[label_index][0],
                "finding_target": (
                    f"{published[finding_index].candidate.file_path}:"
                    f"{published[finding_index].candidate.line}"
                ),
                "label_target": f"{labels[label_index].file_path}:{labels[label_index].line}",
                "source_comment_id": labels[label_index].source_comment_id,
                "rule": "same file, nearby line, distinctive-token overlap",
            }
            for finding_index, label_index in pairs
        ],
        missed_labels=[
            f"{label.file_path}:{label.line}"
            for index, label in enumerate(labels)
            if index not in consumed_labels
        ],
        unmatched_findings=unmatched,
        published_findings=[_finding_payload(index, item) for index, item in enumerate(published)],
        suppressed_findings=[
            _finding_payload(index, item) for index, item in enumerate(suppressed)
        ],
        # A run that suppressed everything must say why, or the report states a
        # recall of zero without explaining whether the model found nothing or
        # the gate rejected everything it found.
        suppressed_by_reason=_count_reasons(suppressed),
        # Counts what reached a reader, checked against the pull request's real
        # changed lines rather than assumed from the fact that self-critique ran.
        # A suppressed invalid line is the gate working, not a defect, so it must
        # not inflate the rate AC8 sets to zero.
        invalid_line=sum(
            1
            for item in published
            if valid_targets is not None
            and (item.candidate.file_path, item.candidate.line) not in valid_targets
        ),
        no_evidence=sum(1 for item in published if not item.candidate.evidence.strip()),
        spam=sum(1 for item in suppressed if item.suppression_reason in _SPAM_REASONS),
        latency_seconds=latency_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        cost_usd=cost_usd,
        provider=provider,
        model=model,
        model_effort=model_effort,
        route_reason=route_reason,
        error=error,
    )


def _merge_reasons(outcomes: Sequence[CaseOutcome]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for outcome in outcomes:
        for reason, count in outcome.suppressed_by_reason.items():
            merged[reason] = merged.get(reason, 0) + count
    return dict(sorted(merged.items()))


def aggregate(outcomes: Sequence[CaseOutcome]) -> BenchmarkMetrics:
    """Combine case outcomes into the reported metrics."""

    if not outcomes:
        return BenchmarkMetrics()

    completed = [item for item in outcomes if item.error is None]
    labels = sum(item.labels for item in completed)
    confirmed_labels = sum(item.confirmed_labels for item in completed)
    unadjudicated_labels = sum(item.unadjudicated_labels for item in completed)
    invalid_label_targets = sum(len(item.invalid_label_targets) for item in completed)
    matched = sum(len(item.matched_labels) for item in completed)
    findings = sum(item.published for item in completed)
    invalid = sum(item.invalid_line for item in completed)
    no_evidence = sum(item.no_evidence for item in completed)
    spam = sum(item.spam for item in completed)
    latencies = [item.latency_seconds for item in outcomes if item.latency_seconds > 0]
    total_cost = sum(item.cost_usd for item in outcomes)

    ordered = sorted(latencies)
    p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] if ordered else 0.0

    return BenchmarkMetrics(
        cases=len(outcomes),
        completed_cases=sum(1 for item in outcomes if item.error is None),
        failed_cases=sum(1 for item in outcomes if item.error is not None),
        labels=labels,
        confirmed_labels=confirmed_labels,
        unadjudicated_labels=unadjudicated_labels,
        invalid_label_targets=invalid_label_targets,
        dataset_certified=(labels > 0 and unadjudicated_labels == 0 and invalid_label_targets == 0),
        findings=findings,
        matched=matched,
        issue_recall=round(matched / labels, 4) if labels else 0.0,
        # Findings a human also raised, over findings reported. Counting matched
        # labels here instead would let one finding that answers two comments
        # push precision above 1.
        comment_precision=round(sum(item.matched_findings for item in completed) / findings, 4)
        if findings
        else 0.0,
        invalid_line_rate=round(invalid / findings, 4) if findings else 0.0,
        no_evidence_rate=round(no_evidence / findings, 4) if findings else 0.0,
        spam_rate=round(spam / (findings + spam), 4) if (findings + spam) else 0.0,
        quiet_runs=sum(1 for item in completed if item.published == 0),
        suppressed_by_reason=_merge_reasons(completed),
        median_latency_seconds=round(statistics.median(latencies), 2) if latencies else 0.0,
        p95_latency_seconds=round(p95, 2),
        total_cost_usd=round(total_cost, 4),
        cost_per_pr_usd=round(total_cost / len(outcomes), 4),
        input_tokens=sum(item.input_tokens for item in outcomes),
        output_tokens=sum(item.output_tokens for item in outcomes),
        cached_input_tokens=sum(item.cached_input_tokens for item in outcomes),
        cache_write_input_tokens=sum(item.cache_write_input_tokens for item in outcomes),
        reasoning_output_tokens=sum(item.reasoning_output_tokens for item in outcomes),
    )


@dataclass(frozen=True)
class Gate:
    """One pass/fail gate from the eval plan."""

    name: str
    target: str
    passed: bool
    actual: str


def evaluate_gates(metrics: BenchmarkMetrics) -> tuple[Gate, ...]:
    """Judge the metrics against the eval plan's PASS requirements."""

    return (
        # Counts cases that actually produced a review. A case that errored is
        # not evidence, and letting it fill the corpus would let a run that never
        # reviewed anything report a passing corpus size.
        Gate(
            "corpus size",
            ">= 20 reviewed cases",
            metrics.completed_cases >= 20,
            f"{metrics.completed_cases} of {metrics.cases}",
        ),
        Gate(
            "run completeness",
            "0 failed cases",
            metrics.failed_cases == 0,
            f"{metrics.failed_cases} failed",
        ),
        Gate(
            "dataset certification",
            "all scored labels adjudicated and revision-valid",
            metrics.dataset_certified,
            (
                f"{metrics.confirmed_labels}/{metrics.labels} confirmed, "
                f"{metrics.invalid_label_targets} invalid targets"
            ),
        ),
        Gate(
            "issue recall",
            "> 0.50",
            metrics.issue_recall > 0.50,
            f"{metrics.issue_recall:.2f}",
        ),
        Gate(
            "comment precision",
            ">= 0.70",
            metrics.comment_precision >= 0.70,
            f"{metrics.comment_precision:.2f}",
        ),
        Gate(
            "invalid-line rate",
            "= 0.00",
            metrics.invalid_line_rate == 0.0,
            f"{metrics.invalid_line_rate:.2f}",
        ),
        Gate(
            "no-evidence rate",
            "= 0.00",
            metrics.no_evidence_rate == 0.0,
            f"{metrics.no_evidence_rate:.2f}",
        ),
        Gate(
            "candidate-noise rate",
            "<= 0.10",
            metrics.spam_rate <= 0.10,
            f"{metrics.spam_rate:.2f}",
        ),
    )


def build_report(
    outcomes: Sequence[CaseOutcome],
    metrics: BenchmarkMetrics,
    *,
    model_policy: str,
    model: str,
    dataset_version: str,
    mode: str,
) -> dict[str, Any]:
    """Assemble the JSON report the eval plan specifies."""

    gates = evaluate_gates(metrics)
    return {
        "dataset_version": dataset_version,
        "mode": mode,
        "model_policy": model_policy,
        "model": model,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": metrics.__dict__,
        "gates": [gate.__dict__ for gate in gates],
        "passed": all(gate.passed for gate in gates),
        "dataset_certified": metrics.dataset_certified,
        "cases": [outcome.__dict__ for outcome in outcomes],
        "limitations": [
            "A label remains provisional until a named human records a final verdict "
            "and explicit approval; provisional labels force the dataset-certification "
            "gate to fail.",
            "Finding-to-label matching is mechanical: same file, a line within "
            f"{LINE_WINDOW}, and at least {TOKEN_OVERLAP:.0%} shared distinctive "
            "vocabulary. The eval plan asks for manual adjudication of ambiguous "
            "matches, which this run does not perform.",
            "Label-overlap precision is a matching proxy, not adjudicated correctness. "
            "The report retains full findings so a human can assess unmatched output.",
        ],
    }


def write_json_report(report: dict[str, Any], path: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def certify_dataset(cases: Sequence[BenchmarkCase], root: Any) -> DatasetCertification:
    """Verify human decisions, source provenance, revision, and diff targets.

    This runs before any live provider is constructed. A post-run gate is too
    late: it can report that the corpus was invalid only after spending money.
    Excluded candidates remain auditable but do not need positive provenance;
    every scored defect does.
    """

    from .diff import build_diff_index, file_diff_from_changed_file

    issues: list[str] = []
    certified_cases = 0
    confirmed_labels = 0
    if not cases:
        issues.append("dataset: no cases selected")

    for case in cases:
        case_issues: list[str] = []
        prefix = case.id
        if case.dataset_schema_version < 2:
            case_issues.append(f"{prefix}: dataset schema is not v2")

        fixture: dict[str, Any] | None = None
        valid_targets: set[tuple[str, int]] = set()
        try:
            fixture_path = root / case.diff_path
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            metadata, changed = load_case_inputs(case, root)
            index = build_diff_index([file_diff_from_changed_file(item) for item in changed])
            valid_targets = {
                (file_diff.path, line)
                for file_diff in index.files
                for line in file_diff.commentable_lines
            }
            if metadata.repo != case.repo or metadata.number != case.pr_number:
                case_issues.append(f"{prefix}: fixture identifies a different pull request")
            if metadata.base_sha != case.base_sha or metadata.head_sha != case.head_sha:
                case_issues.append(f"{prefix}: fixture revision does not match the dataset")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            case_issues.append(f"{prefix}: fixture cannot be validated: {exc}")

        scored = [label for label in case.labels if not label.excluded]
        if not scored:
            case_issues.append(f"{prefix}: no confirmed defect labels remain after exclusions")

        source_comments: dict[int, dict[str, Any]] = {}
        if fixture is not None:
            source_comments = {
                int(item["id"]): item
                for item in fixture.get("review_comments", [])
                if isinstance(item, dict) and item.get("id") is not None
            }
            author = str(fixture.get("metadata", {}).get("author_login") or "")
        else:
            author = ""

        for label in case.labels:
            label_prefix = f"{prefix}/{label.label_id or '<missing-label-id>'}"
            if not isinstance(label.human_approved, bool):
                case_issues.append(f"{label_prefix}: human_approved must be a boolean")
                continue
            if not isinstance(label.adjudicator, str):
                case_issues.append(f"{label_prefix}: adjudicator must be a string")
                continue
            if label.adjudication != "unreviewed" and not label.human_approved:
                case_issues.append(f"{label_prefix}: decision is not human-approved")
                continue
            if label.human_approved and not label.adjudicator.strip():
                case_issues.append(f"{label_prefix}: human adjudicator is missing")
                continue
            if label.adjudication != "unreviewed" and not label.adjudication_notes.strip():
                case_issues.append(f"{label_prefix}: adjudication notes are missing")
                continue
            if label.excluded:
                continue
            if not label.confirmed:
                case_issues.append(f"{label_prefix}: label is not human-confirmed")
                continue

            confirmed_labels += 1
            if not label.label_id or label.label_id.startswith("legacy:"):
                case_issues.append(f"{label_prefix}: stable v2 source id is missing")
            if not label.reviewer_login:
                case_issues.append(f"{label_prefix}: reviewer provenance is missing")
            if label.source_comment_id is None:
                case_issues.append(f"{label_prefix}: source comment id is missing")
            if label.review_commit_sha != case.head_sha:
                case_issues.append(f"{label_prefix}: review revision does not match the head SHA")
            if (label.file_path, label.line) not in valid_targets:
                case_issues.append(f"{label_prefix}: target is not an added line in the fixture")

            source = (
                source_comments.get(label.source_comment_id)
                if label.source_comment_id is not None
                else None
            )
            if source is None:
                case_issues.append(f"{label_prefix}: source comment is absent from the fixture")
                continue
            source_user = source.get("user")
            reviewer = (
                str(source_user.get("login") or "")
                if isinstance(source_user, dict)
                else str(source_user or "")
            )
            source_line = source.get("line") or source.get("original_line")
            if (
                str(source.get("path") or "") != label.file_path
                or int(source_line or 0) != label.line
                or str(source.get("body") or "").strip() != label.human_comment.strip()
                or reviewer != label.reviewer_login
                or str(source.get("commit_id") or "") != label.review_commit_sha
            ):
                case_issues.append(f"{label_prefix}: source comment provenance does not match")
            if source.get("in_reply_to_id") is not None:
                case_issues.append(f"{label_prefix}: source comment is a reply")
            if author and reviewer.casefold() == author.casefold():
                case_issues.append(f"{label_prefix}: source comment was written by the PR author")
            user_type = str(source.get("user_type") or "")
            if user_type.casefold() == "bot" or reviewer.casefold().endswith("[bot]"):
                case_issues.append(f"{label_prefix}: source comment was written by a bot")

        if not case_issues:
            certified_cases += 1
        issues.extend(case_issues)

    return DatasetCertification(
        certified=bool(cases) and certified_cases == len(cases),
        cases=len(cases),
        certified_cases=certified_cases,
        confirmed_labels=confirmed_labels,
        issues=tuple(issues),
    )


def load_case_inputs(case: BenchmarkCase, root: Any) -> tuple[Any, list[Any]]:
    """Rebuild a pull request from its frozen fixture.

    The benchmark never calls GitHub. A published number must be reproducible
    from the repository alone, and a corpus that re-fetches would drift as those
    pull requests change.
    """

    from .github import ChangedFile, PullRequestMetadata

    payload = json.loads((root / case.diff_path).read_text(encoding="utf-8"))
    meta = payload["metadata"]
    metadata = PullRequestMetadata(
        repo=meta["repo"],
        number=int(meta["number"]),
        title=meta["title"],
        state=meta["state"],
        base_sha=meta["base_sha"],
        head_sha=meta["head_sha"],
        author_login=meta["author_login"],
        html_url=meta["html_url"],
    )
    changed = [
        ChangedFile(
            filename=item["filename"],
            status=item["status"],
            additions=int(item["additions"]),
            deletions=int(item["deletions"]),
            changes=int(item["changes"]),
            patch=item.get("patch"),
            previous_filename=item.get("previous_filename"),
        )
        for item in payload["files"]
    ]
    return metadata, changed


def run_case(
    case: BenchmarkCase,
    *,
    root: Any,
    settings: Any,
    provider: Any = None,
) -> CaseOutcome:
    """Compatibility wrapper: execute once and score at the configured threshold."""

    raw = execute_case(case, root=root, settings=settings, provider=provider)
    return score_raw(raw, settings=settings, threshold=settings.min_publish_confidence)


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render the benchmark report for a human reader."""

    metrics = report["metrics"]
    lines = [
        "# PRCritiq benchmark",
        "",
        f"- Dataset: `{report['dataset_version']}` ({metrics['cases']} pull requests, "
        f"{metrics['labels']} labels)",
        f"- Reviewed: {metrics['completed_cases']} of {metrics['cases']} "
        f"({metrics['failed_cases']} failed)",
        f"- Mode: {report['mode']}",
        f"- Model: `{report['model_policy']}` / `{report['model']}`",
        f"- Generated: {report['generated_at']}",
        "",
        "## Gates",
        "",
        "| Gate | Target | Actual | Result |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {gate['name']} | {gate['target']} | {gate['actual']} | "
        f"{'PASS' if gate['passed'] else 'FAIL'} |"
        for gate in report["gates"]
    )
    lines.extend(
        [
            "",
            f"**Overall: {'PASS' if report['passed'] else 'FAIL'}**",
            "",
            "## Metrics",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Issue recall | {metrics['issue_recall']:.2f} |",
            f"| Label-overlap precision (proxy) | {metrics['comment_precision']:.2f} |",
            f"| Confirmed labels | {metrics['confirmed_labels']} of {metrics['labels']} |",
            f"| Invalid label targets excluded | {metrics['invalid_label_targets']} |",
            f"| Invalid-line rate | {metrics['invalid_line_rate']:.2f} |",
            f"| No-evidence rate | {metrics['no_evidence_rate']:.2f} |",
            f"| Candidate-noise rate | {metrics['spam_rate']:.2f} |",
            f"| Findings published | {metrics['findings']} |",
            f"| Human labels matched | {metrics['matched']} of {metrics['labels']} |",
            f"| Quiet runs | {metrics['quiet_runs']} of {metrics['cases']} |",
            f"| Suppressed by reason | {metrics['suppressed_by_reason'] or 'none'} |",
            f"| Median latency | {metrics['median_latency_seconds']:.2f}s |",
            f"| P95 latency | {metrics['p95_latency_seconds']:.2f}s |",
            f"| Cost per PR | ${metrics['cost_per_pr_usd']:.4f} |",
            f"| Total cost | ${metrics['total_cost_usd']:.4f} |",
            (
                f"| Tokens | {metrics['input_tokens']} in "
                f"({metrics['cached_input_tokens']} cached read, "
                f"{metrics.get('cache_write_input_tokens', 'unknown')} cache write), "
                f"{metrics['output_tokens']} out "
                f"({metrics['reasoning_output_tokens']} reasoning) |"
            ),
            "",
            "## Per case",
            "",
            "| Case | Labels | Published | Matched | Suppressed | Latency | Cost |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        f"| {case['repo']}#{case['pr_number']} | {case['labels']} | {case['published']} | "
        f"{len(case['matched_labels'])} | {case['suppressed']} | "
        f"{case['latency_seconds']:.1f}s | ${case['cost_usd']:.4f} |"
        for case in report["cases"]
    )
    if report.get("threshold_sweep"):
        lines.extend(
            [
                "",
                "## Publish-threshold sweep",
                "",
                "Scored from one set of model calls: suppression is post-processing over the",
                "same drafted candidates, so the curve costs nothing extra.",
                "",
                "| Min confidence | Recall | Precision | Findings | Quiet runs |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| {row['min_publish_confidence']} | {row['issue_recall']:.2f} | "
            f"{row['comment_precision']:.2f} | {row['findings']} | {row['quiet_runs']} |"
            for row in report["threshold_sweep"]
        )

    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


@dataclass
class RawCaseRun:
    """One case reviewed once, kept so it can be scored at several thresholds.

    Suppression is post-processing over candidates the model already drafted, so
    a threshold sweep costs no extra model calls. Reporting a single recall
    figure without the curve behind it would hide whether the reviewer found
    nothing or the publish gate rejected what it found.
    """

    case: BenchmarkCase
    candidates: tuple[Any, ...] = ()
    valid_targets: set[tuple[str, int]] = field(default_factory=set)
    diff_index: Any = None
    retrieval: RetrievalResult | None = None
    tool_runs: tuple[ToolRun, ...] | None = None
    latency_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    cost_usd: float = 0.0
    provider: str | None = None
    model: str | None = None
    model_effort: str | None = None
    route_reason: str | None = None
    error: str | None = None


def _github_client(settings: Any) -> Any:
    from .github import GitHubClient

    return GitHubClient(
        token=settings.github_token,
        api_base_url=settings.github_api_base_url,
        timeout_seconds=settings.github_request_timeout_seconds,
    )


def execute_case(
    case: BenchmarkCase,
    *,
    root: Any,
    settings: Any,
    provider: Any = None,
    include_context: bool = False,
    client: Any = None,
    task: str = "batch_eval",
) -> RawCaseRun:
    """Review one case once and keep everything needed to score it.

    With `include_context`, the repository is snapshotted at the case's pinned
    head SHA and surrounding code is retrieved, which is how the product runs
    with `--context`. That needs the network and a token, so the benchmark's
    usual offline property does not hold for those runs.
    """

    from .diff import build_diff_index, file_diff_from_changed_file
    from .graph import run_review_graph
    from .guardrails import apply_guardrails, reviewable_files
    from .providers import ProviderBillingError
    from .review import gather_evidence

    started = time.monotonic()
    try:
        metadata, changed = load_case_inputs(case, root)
        retrieval = None
        if include_context:
            diffs = [file_diff_from_changed_file(item) for item in changed]
            in_scope = reviewable_files(diffs, apply_guardrails(diffs, settings))
            owned = client is None
            resolved = client or _github_client(settings)
            try:
                _, retrieval, _ = gather_evidence(
                    client=resolved,
                    repo=case.repo,
                    ref=case.head_sha,
                    file_diffs=in_scope,
                    settings=settings,
                    want_context=True,
                    want_tools=False,
                )
            finally:
                if owned:
                    resolved.close()
        state = run_review_graph(
            settings=settings,
            metadata=metadata,
            changed_files=changed,
            retrieval=retrieval,
            provider=provider,
            task=task,
        )
    except ProviderBillingError:
        raise
    except Exception as exc:
        return RawCaseRun(case=case, latency_seconds=time.monotonic() - started, error=str(exc))

    usage = state.get("usage")
    choice = state.get("model_choice")
    index = build_diff_index([file_diff_from_changed_file(item) for item in changed])
    return RawCaseRun(
        case=case,
        candidates=tuple(state.get("drafted") or ()),
        valid_targets={
            (file_diff.path, line)
            for file_diff in index.files
            for line in file_diff.commentable_lines
        },
        diff_index=index,
        retrieval=state.get("retrieval"),
        tool_runs=state.get("tool_runs"),
        latency_seconds=time.monotonic() - started,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cached_input_tokens=usage.cached_input_tokens if usage else 0,
        cache_write_input_tokens=getattr(usage, "cache_write_input_tokens", 0) if usage else 0,
        reasoning_output_tokens=usage.reasoning_output_tokens if usage else 0,
        cost_usd=usage.cost_usd(choice.model) if usage and choice else 0.0,
        provider=choice.provider if choice else None,
        model=choice.model if choice else None,
        model_effort=settings.model_effort if choice else None,
        route_reason=choice.reason if choice else None,
    )


def score_raw(raw: RawCaseRun, *, settings: Any, threshold: int) -> CaseOutcome:
    """Score one executed case at a given publish-confidence threshold."""

    from dataclasses import replace

    from .critique import critique

    if raw.error is not None:
        return score_case(
            raw.case,
            (),
            latency_seconds=raw.latency_seconds,
            provider=raw.provider,
            model=raw.model,
            model_effort=raw.model_effort,
            route_reason=raw.route_reason,
            error=raw.error,
        )

    reviewed = critique(
        raw.candidates,
        diff_index=raw.diff_index,
        settings=replace(settings, min_publish_confidence=threshold),
        retrieval=raw.retrieval,
        tool_runs=raw.tool_runs,
    )
    return score_case(
        raw.case,
        reviewed,
        valid_targets=raw.valid_targets,
        latency_seconds=raw.latency_seconds,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cached_input_tokens=raw.cached_input_tokens,
        cache_write_input_tokens=raw.cache_write_input_tokens,
        reasoning_output_tokens=raw.reasoning_output_tokens,
        cost_usd=raw.cost_usd,
        provider=raw.provider,
        model=raw.model,
        model_effort=raw.model_effort,
        route_reason=raw.route_reason,
    )


def threshold_sweep(
    raws: Sequence[RawCaseRun], *, settings: Any, thresholds: Sequence[int]
) -> list[dict[str, Any]]:
    """Metrics at each publish threshold, from one set of model calls."""

    sweep: list[dict[str, Any]] = []
    for threshold in thresholds:
        metrics = aggregate(
            [score_raw(raw, settings=settings, threshold=threshold) for raw in raws]
        )
        sweep.append(
            {
                "min_publish_confidence": threshold,
                "issue_recall": metrics.issue_recall,
                "comment_precision": metrics.comment_precision,
                "findings": metrics.findings,
                "matched": metrics.matched,
                "invalid_line_rate": metrics.invalid_line_rate,
                "spam_rate": metrics.spam_rate,
                "quiet_runs": metrics.quiet_runs,
            }
        )
    return sweep
