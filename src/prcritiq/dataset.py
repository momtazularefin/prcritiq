"""Benchmark dataset construction from pull request review history.

GitHub comments are *label candidates*, not ground truth.  A trustworthy label
must come from an independent human reviewer, refer to the exact revision in the
fixture, and be adjudicated as a real defect.  The mechanical filters here remove
known provenance failures; they deliberately do not pretend to replace human
adjudication.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal

#: Comments that carry no reviewable claim. These implement the eval plan's
#: exclusion list: praise, social replies, bikeshedding, and project management.
_SOCIAL: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(lgtm|nit|thanks?|thank you|nice|good catch|ok|okay|done|\+1|👍|同意)\b",
        r"^\s*(ship it|looks good|great work|awesome|perfect)\b",
        r"^\s*@[\w-]+\s*$",
        r"^\s*(can you rebase|please rebase|needs? rebase)\b",
        r"^\s*(typo|nitpick)\s*[:.]?\s*$",
    )
)

#: Formatting and naming preferences, which the eval plan excludes from labels
#: because PRCritiq is not supposed to report them either.
_STYLE: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(black|isort|ruff format|formatting|whitespace|blank line|indentation)\b",
        r"\b(rename this|naming|call it|better name)\b",
        r"\b(alphabetical|sort these|ordering of imports)\b",
    )
)

_MIN_COMMENT_CHARACTERS: Final = 40
_MIN_LABELS_PER_CASE: Final = 1

LabelAdjudication = Literal["unreviewed", "confirmed_defect", "excluded"]


@dataclass(frozen=True)
class Label:
    """One review comment candidate and the provenance needed to trust it."""

    file_path: str
    line: int
    category: str
    severity: str
    human_comment: str
    expected_issue: str
    match_notes: str = ""
    label_id: str = ""
    reviewer_login: str = ""
    source_comment_id: int | None = None
    review_commit_sha: str = ""
    adjudication: LabelAdjudication = "unreviewed"
    adjudication_notes: str = ""

    @property
    def confirmed(self) -> bool:
        """Whether a human has confirmed that this comment describes a defect."""

        return self.adjudication == "confirmed_defect"


@dataclass(frozen=True)
class BenchmarkCase:
    """One pull request in the benchmark corpus."""

    id: str
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    languages: list[str]
    diff_path: str
    human_comments_path: str
    dataset_schema_version: int = 2
    labels: list[Label] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["labels"] = [asdict(label) for label in self.labels]
        return payload


def is_meaningful_comment(body: str) -> bool:
    """Apply the eval plan's exclusion rules to one review comment.

    Deliberately conservative: a comment that survives is not guaranteed to name
    a defect, only to be substantive enough that a reviewer raising it counts as
    a signal worth measuring against.
    """

    text = body.strip()
    if len(text) < _MIN_COMMENT_CHARACTERS:
        return False
    if any(pattern.search(text) for pattern in _SOCIAL):
        return False
    return all(not pattern.search(text) for pattern in _STYLE)


def classify_comment(body: str) -> tuple[str, str]:
    """Guess a category and severity from the reviewer's own words.

    A heuristic, recorded as one. It shapes reporting only; matching a finding
    to a label never depends on the category agreeing.
    """

    text = body.lower()
    if re.search(r"\b(security|injection|escape|sanitiz|auth|secret|token leak)\b", text):
        return "security", "high"
    if re.search(r"\b(race|deadlock|leak|crash|None|null|exception|traceback)\b", text):
        return "bug", "high"
    if re.search(r"\b(test|coverage|assert)\b", text):
        return "test_gap", "medium"
    if re.search(r"\b(error handling|raise|except|catch|fail)\b", text):
        return "error_handling", "medium"
    if re.search(r"\b(api|signature|contract|backward compat|breaking)\b", text):
        return "contract", "medium"
    if re.search(r"\b(regress|broke|no longer)\b", text):
        return "regression", "high"
    return "bug", "medium"


def _reviewer(comment: dict[str, Any]) -> tuple[str, str]:
    """Return the reviewer's login and GitHub account type."""

    user = comment.get("user")
    if isinstance(user, dict):
        return str(user.get("login") or ""), str(user.get("type") or "")
    return str(user or ""), str(comment.get("user_type") or "")


def _is_bot(login: str, account_type: str) -> bool:
    """Recognize GitHub bot accounts without maintaining a vendor allow-list."""

    normalized = login.strip().lower()
    return account_type.strip().lower() == "bot" or normalized.endswith("[bot]")


def _candidate_id(*, comment_id: int | None, path: str, line: int, reviewer: str, body: str) -> str:
    """Build a stable id that an adjudication file can address."""

    if comment_id is not None:
        return f"github-review-comment:{comment_id}"
    digest = sha256(f"{path}\0{line}\0{reviewer}\0{body}".encode()).hexdigest()[:20]
    return f"review-comment-digest:{digest}"


def labels_from_comments(
    comments: Iterable[dict[str, Any]],
    *,
    pr_author_login: str = "",
    head_sha: str = "",
) -> list[Label]:
    """Turn primary, independent review comments into label candidates.

    When ``head_sha`` is supplied, comments on earlier revisions are excluded.
    Supporting several review revisions correctly requires one frozen fixture
    per revision; silently scoring them against the final diff is invalid.
    """

    labels: list[Label] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        path = str(comment.get("path") or "")
        line = comment.get("line") or comment.get("original_line")
        reviewer_login, account_type = _reviewer(comment)
        review_commit_sha = str(comment.get("commit_id") or "")
        comment_id = int(comment["id"]) if comment.get("id") is not None else None
        if not path or not line or not is_meaningful_comment(body):
            continue
        if comment.get("in_reply_to_id") is not None:
            continue
        if pr_author_login and reviewer_login.casefold() == pr_author_login.casefold():
            continue
        if _is_bot(reviewer_login, account_type):
            continue
        if head_sha and review_commit_sha != head_sha:
            continue
        category, severity = classify_comment(body)
        labels.append(
            Label(
                file_path=path,
                line=int(line),
                category=category,
                severity=severity,
                human_comment=body.strip(),
                expected_issue=body.strip()[:280],
                match_notes="unadjudicated primary inline review comment",
                label_id=_candidate_id(
                    comment_id=comment_id,
                    path=path,
                    line=int(line),
                    reviewer=reviewer_login,
                    body=body.strip(),
                ),
                reviewer_login=reviewer_login,
                source_comment_id=comment_id,
                review_commit_sha=review_commit_sha,
            )
        )
    return labels


def write_dataset(cases: Sequence[BenchmarkCase], path: Path) -> int:
    """Write cases as JSON Lines, one case per line."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_json(), sort_keys=True) + "\n")
    return len(cases)


def read_dataset(path: Path) -> list[BenchmarkCase]:
    """Read a dataset written by `write_dataset`."""

    cases: list[BenchmarkCase] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        labels = []
        for index, item in enumerate(payload.pop("labels", []), start=1):
            if not item.get("label_id"):
                item["label_id"] = f"legacy:{payload['id']}:{index:03d}"
            labels.append(Label(**item))
        cases.append(BenchmarkCase(**payload, labels=labels))
    return cases


def case_is_usable(case: BenchmarkCase) -> bool:
    """A candidate case needs at least one mechanically eligible comment."""

    return len(case.labels) >= _MIN_LABELS_PER_CASE


def case_is_certified(case: BenchmarkCase) -> bool:
    """A publishable benchmark case contains only adjudicated defect labels."""

    scored = [label for label in case.labels if label.adjudication != "excluded"]
    return bool(scored) and all(label.confirmed for label in scored)


def select_confirmed_cases(cases: Sequence[BenchmarkCase]) -> list[BenchmarkCase]:
    """Keep cases with at least one human-confirmed defect label.

    Excluded labels remain attached to a retained case so its adjudication trail
    stays auditable.  Only cases with no scored ground truth are removed.
    """

    return [case for case in cases if any(label.confirmed for label in case.labels)]


def adjudication_queue(
    cases: Sequence[BenchmarkCase],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Flatten labels into an editable, provenance-rich decision queue.

    When ``root`` is supplied, direct replies from the frozen review thread are
    included.  A primary comment can be corrected, narrowed, or rejected in the
    discussion that follows it; adjudicating without that context can turn a
    disputed suggestion into false ground truth.
    """

    queue: list[dict[str, Any]] = []
    for case in cases:
        thread_replies: dict[int, list[dict[str, Any]]] = {}
        author_login = ""
        if root is not None:
            fixture_path = root / case.human_comments_path
            try:
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Cannot load adjudication context for {case.id} from {fixture_path}: {exc}"
                ) from exc
            author_login = str(fixture.get("metadata", {}).get("author_login") or "")
            for comment in fixture.get("review_comments", []):
                if not isinstance(comment, dict) or comment.get("in_reply_to_id") is None:
                    continue
                parent_id = int(comment["in_reply_to_id"])
                reviewer_login, _account_type = _reviewer(comment)
                thread_replies.setdefault(parent_id, []).append(
                    {
                        "source_comment_id": comment.get("id"),
                        "reviewer_login": reviewer_login,
                        "is_pr_author": bool(
                            author_login and reviewer_login.casefold() == author_login.casefold()
                        ),
                        "review_commit_sha": str(comment.get("commit_id") or ""),
                        "human_comment": str(comment.get("body") or "").strip(),
                    }
                )
        for label in case.labels:
            row = {
                "label_id": label.label_id,
                "case_id": case.id,
                "repo": case.repo,
                "pr_number": case.pr_number,
                "target": f"{label.file_path}:{label.line}",
                "pr_author_login": author_login,
                "reviewer_login": label.reviewer_login,
                "review_commit_sha": label.review_commit_sha,
                "source_comment_id": label.source_comment_id,
                "human_comment": label.human_comment,
                "thread_replies": (
                    thread_replies.get(label.source_comment_id, [])
                    if label.source_comment_id is not None
                    else []
                ),
                "adjudication": label.adjudication,
                "adjudication_notes": label.adjudication_notes,
            }
            queue.append(row)
    return queue


def apply_adjudications(
    cases: Sequence[BenchmarkCase],
    decisions: Iterable[dict[str, Any]],
    *,
    require_complete: bool = True,
) -> list[BenchmarkCase]:
    """Apply a human decision file without silently ignoring missing or extra ids."""

    allowed = {"unreviewed", "confirmed_defect", "excluded"}
    by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        label_id = str(decision.get("label_id") or "")
        if not label_id:
            raise ValueError("Every adjudication decision needs label_id")
        if label_id in by_id:
            raise ValueError(f"Duplicate adjudication decision for {label_id}")
        verdict = str(decision.get("adjudication") or "")
        if verdict not in allowed:
            raise ValueError(
                f"Adjudication for {label_id} must be one of: {', '.join(sorted(allowed))}"
            )
        notes = str(decision.get("adjudication_notes") or "").strip()
        if verdict != "unreviewed" and not notes:
            raise ValueError(
                f"Adjudication for {label_id} needs notes explaining the human decision"
            )
        by_id[label_id] = decision

    expected_ids = {label.label_id for case in cases for label in case.labels}
    extra = sorted(set(by_id) - expected_ids)
    if extra:
        raise ValueError(f"Unknown adjudication label ids: {', '.join(extra)}")
    missing = sorted(expected_ids - set(by_id))
    if require_complete and missing:
        raise ValueError(
            f"Missing adjudications for {len(missing)} labels: {', '.join(missing[:5])}"
        )

    updated: list[BenchmarkCase] = []
    for case in cases:
        labels: list[Label] = []
        for label in case.labels:
            decision = by_id.get(label.label_id)
            if decision is None:
                labels.append(label)
                continue
            labels.append(
                replace(
                    label,
                    adjudication=str(decision["adjudication"]),  # type: ignore[arg-type]
                    adjudication_notes=str(decision.get("adjudication_notes") or ""),
                )
            )
        updated.append(replace(case, labels=labels))
    return updated
