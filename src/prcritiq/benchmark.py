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
from typing import Any, Final

from .chunking import tokenize_identifiers
from .dataset import BenchmarkCase, Label
from .findings import ReviewedFinding, SuppressionReason

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
    labels: int = 0
    published: int = 0
    suppressed: int = 0
    matched_labels: list[str] = field(default_factory=list)
    matched_findings: int = 0
    missed_labels: list[str] = field(default_factory=list)
    unmatched_findings: list[str] = field(default_factory=list)
    invalid_line: int = 0
    no_evidence: int = 0
    spam: int = 0
    latency_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class BenchmarkMetrics:
    """The numbers the eval plan's gates are judged on."""

    cases: int = 0
    labels: int = 0
    findings: int = 0
    matched: int = 0
    issue_recall: float = 0.0
    comment_precision: float = 0.0
    invalid_line_rate: float = 0.0
    no_evidence_rate: float = 0.0
    spam_rate: float = 0.0
    quiet_runs: int = 0
    median_latency_seconds: float = 0.0
    p95_latency_seconds: float = 0.0
    total_cost_usd: float = 0.0
    cost_per_pr_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


#: Suppression reasons that count as spam the agent avoided emitting.
_SPAM_REASONS: Final[frozenset[SuppressionReason]] = frozenset(
    {SuppressionReason.GENERIC, SuppressionReason.DUPLICATE}
)


def score_case(
    case: BenchmarkCase,
    reviewed: Sequence[ReviewedFinding],
    *,
    valid_targets: set[tuple[str, int]] | None = None,
    latency_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    error: str | None = None,
) -> CaseOutcome:
    """Score one case against its labels."""

    published = [item for item in reviewed if item.published]
    suppressed = [item for item in reviewed if not item.published]

    matched_labels: list[str] = []
    consumed: set[int] = set()
    for index, label in enumerate(case.labels):
        for finding in published:
            if matches(finding, label):
                matched_labels.append(f"{label.file_path}:{label.line}")
                consumed.add(index)
                break

    matched_findings = sum(
        1 for item in published if any(matches(item, label) for label in case.labels)
    )
    unmatched = [
        f"{item.candidate.file_path}:{item.candidate.line}"
        for item in published
        if not any(matches(item, label) for label in case.labels)
    ]

    return CaseOutcome(
        case_id=case.id,
        repo=case.repo,
        pr_number=case.pr_number,
        labels=len(case.labels),
        published=len(published),
        suppressed=len(suppressed),
        matched_labels=matched_labels,
        matched_findings=matched_findings,
        missed_labels=[
            f"{label.file_path}:{label.line}"
            for index, label in enumerate(case.labels)
            if index not in consumed
        ],
        unmatched_findings=unmatched,
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
        cost_usd=cost_usd,
        error=error,
    )


def aggregate(outcomes: Sequence[CaseOutcome]) -> BenchmarkMetrics:
    """Combine case outcomes into the reported metrics."""

    if not outcomes:
        return BenchmarkMetrics()

    labels = sum(item.labels for item in outcomes)
    matched = sum(len(item.matched_labels) for item in outcomes)
    findings = sum(item.published for item in outcomes)
    invalid = sum(item.invalid_line for item in outcomes)
    no_evidence = sum(item.no_evidence for item in outcomes)
    spam = sum(item.spam for item in outcomes)
    latencies = [item.latency_seconds for item in outcomes if item.latency_seconds > 0]
    total_cost = sum(item.cost_usd for item in outcomes)

    ordered = sorted(latencies)
    p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] if ordered else 0.0

    return BenchmarkMetrics(
        cases=len(outcomes),
        labels=labels,
        findings=findings,
        matched=matched,
        issue_recall=round(matched / labels, 4) if labels else 0.0,
        # Findings a human also raised, over findings reported. Counting matched
        # labels here instead would let one finding that answers two comments
        # push precision above 1.
        comment_precision=round(sum(item.matched_findings for item in outcomes) / findings, 4)
        if findings
        else 0.0,
        invalid_line_rate=round(invalid / findings, 4) if findings else 0.0,
        no_evidence_rate=round(no_evidence / findings, 4) if findings else 0.0,
        spam_rate=round(spam / (findings + spam), 4) if (findings + spam) else 0.0,
        quiet_runs=sum(1 for item in outcomes if item.published == 0),
        median_latency_seconds=round(statistics.median(latencies), 2) if latencies else 0.0,
        p95_latency_seconds=round(p95, 2),
        total_cost_usd=round(total_cost, 4),
        cost_per_pr_usd=round(total_cost / len(outcomes), 4),
        input_tokens=sum(item.input_tokens for item in outcomes),
        output_tokens=sum(item.output_tokens for item in outcomes),
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
        Gate("corpus size", ">= 20 cases", metrics.cases >= 20, f"{metrics.cases}"),
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
        Gate("spam rate", "<= 0.10", metrics.spam_rate <= 0.10, f"{metrics.spam_rate:.2f}"),
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
        "cases": [outcome.__dict__ for outcome in outcomes],
        "limitations": [
            "Labels are inline review comments filtered by documented heuristics, "
            "not hand-adjudicated by a human.",
            "Finding-to-label matching is mechanical: same file, a line within "
            f"{LINE_WINDOW}, and at least {TOKEN_OVERLAP:.0%} shared distinctive "
            "vocabulary. The eval plan asks for manual adjudication of ambiguous "
            "matches, which this run does not perform.",
            "A human comment can be a question or a design discussion rather than a "
            "defect, so recall against these labels understates nothing but also "
            "proves less than recall against curated defects would.",
        ],
    }


def write_json_report(report: dict[str, Any], path: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    """Review one case and score it."""

    from .diff import build_diff_index, file_diff_from_changed_file
    from .graph import run_review_graph

    started = time.monotonic()
    try:
        metadata, changed = load_case_inputs(case, root)
        state = run_review_graph(
            settings=settings,
            metadata=metadata,
            changed_files=changed,
            provider=provider,
        )
    except Exception as exc:
        return score_case(case, (), latency_seconds=time.monotonic() - started, error=str(exc))

    reviewed = state.get("reviewed") or ()
    usage = state.get("usage")
    choice = state.get("model_choice")
    index = build_diff_index([file_diff_from_changed_file(item) for item in changed])
    valid_targets = {
        (file_diff.path, line) for file_diff in index.files for line in file_diff.commentable_lines
    }

    return score_case(
        case,
        reviewed,
        valid_targets=valid_targets,
        latency_seconds=time.monotonic() - started,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cost_usd=usage.cost_usd(choice.model) if usage and choice else 0.0,
    )


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render the benchmark report for a human reader."""

    metrics = report["metrics"]
    lines = [
        "# PRCritiq benchmark",
        "",
        f"- Dataset: `{report['dataset_version']}` ({metrics['cases']} pull requests, "
        f"{metrics['labels']} labels)",
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
            f"| Comment precision | {metrics['comment_precision']:.2f} |",
            f"| Invalid-line rate | {metrics['invalid_line_rate']:.2f} |",
            f"| No-evidence rate | {metrics['no_evidence_rate']:.2f} |",
            f"| Spam rate | {metrics['spam_rate']:.2f} |",
            f"| Findings published | {metrics['findings']} |",
            f"| Human labels matched | {metrics['matched']} of {metrics['labels']} |",
            f"| Quiet runs | {metrics['quiet_runs']} of {metrics['cases']} |",
            f"| Median latency | {metrics['median_latency_seconds']:.2f}s |",
            f"| P95 latency | {metrics['p95_latency_seconds']:.2f}s |",
            f"| Cost per PR | ${metrics['cost_per_pr_usd']:.4f} |",
            f"| Total cost | ${metrics['total_cost_usd']:.4f} |",
            f"| Tokens | {metrics['input_tokens']} in, {metrics['output_tokens']} out |",
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
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
