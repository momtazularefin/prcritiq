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
from collections.abc import Iterable, Mapping, Sequence
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
_ALLOWED_LABEL_CATEGORIES: Final = {
    "bug",
    "regression",
    "contract",
    "test_gap",
    "error_handling",
    "security",
    "tool",
}
_ALLOWED_LABEL_SEVERITIES: Final = {"low", "medium", "high", "critical"}

LabelAdjudication = Literal["unreviewed", "confirmed_defect", "excluded"]
CandidateSignal = Literal["defect", "accepted", "requested_changes", "all"]


_RESOLUTION_REPLY: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(?:thanks[,! ]+)?(?:fixed|done|addressed|updated|changed|removed|reverted)\b",
        r"^\s*(?:thanks[,! ]+)?(?:added|implemented)\b",
        r"\b(?:good catch|good point|you(?:'re| are) right)[.!,:; -]+"
        r"(?:fixed|addressed|updated|changed|removed|reverted|added|implemented)\b",
        r"\b(?:fixed|addressed|updated|removed|reverted|added (?:a )?tests?)\s+(?:now|in\b)",
        r"\b(?:i(?:'ve| have) )?(?:fixed|addressed|updated|changed|removed|reverted|"
        r"implemented)\b",
        r"\bmade (?:all )?(?:the )?requested changes\b",
    )
)

_NEGATED_RESOLUTION: Final = re.compile(
    r"\b(?:not|isn't|wasn't|won't|wouldn't|can't|cannot|don't|didn't)\s+"
    r"(?:fix|fixed|change|changed|address|addressed|remove|removed|add|added)\b",
    re.IGNORECASE,
)

_NON_DEFECT_REQUEST: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"^\s*(?:nit|nitpick)\b",
        r"\b(?:more elegant|easier to read|clearer to read|readability|"
        r"naming preference|no abbreviation policy)\b",
        r"\b(?:i (?:could|may|might) be wrong|weak preference|not a blocker|"
        r"would approve (?:this|the) (?:pr|pull request) as is)\b",
        r"\bcode lgtm but not sure\b",
        r"\bseparately from (?:this|the) (?:pr|pull request)\b",
        r"^\s*can'?t (?:these|this|that) be\b",
        r"^\s*```suggestion\b",
        r"^\s*(?:(?:can|could|would) (?:you |we )?|we should |please )?"
        r"add\b.{0,80}\b(?:tests?|scenarios?|coverage)\b",
        r"^\s*what do you think about (?:adding|introducing|supporting)\b",
        r"^\s*(?:(?:can|could|would) (?:you |we )?|please )?"
        r"(?:add|remove|update|rename|reword|change)\b.{0,80}"
        r"\b(?:comments?|docstrings?|documentation|docs?|name|naming)\b",
    )
)

_DEFECT_LANGUAGE: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\b(?:crash|exception|incorrect|wrong|broken|bug|regression|hang|deadlock|"
        r"leak|race|security|corrupt|data loss|infinite loop|overflow|underflow|"
        r"unreachable|dead code|silently)\b",
        r"\b(?:fails?|failure|errors?)\b",
        r"\b[45]\d\d\b",
        r"\b(?:doesn't|does not|won't|will not|cannot|can't)\b",
        r"\b(?:type|value|key|attribute|index|runtime)error\b",
        r"\b(?:can|could|may|might|will|would)\s+(?:still\s+)?"
        r"(?:raise|crash|fail|break|return|skip|drop|overwrite|leak|corrupt|"
        r"reject|accept|allow|ignore|lose|misclassify|dispatch|push|write)\b",
    )
)

_DEFERRAL_REPLY: Final = re.compile(
    r"\b(?:follow[- ]?up|another (?:pr|pull request)|separately from (?:this|the) "
    r"(?:pr|pull request)|not blocking|non[- ]blocking)\b",
    re.IGNORECASE,
)

_REJECTION_REPLY: Final = re.compile(
    r"\b(?:this is impossible|works? as intended|not (?:a|an) (?:bug|issue)|"
    r"cannot reproduce|no change (?:is )?needed)\b",
    re.IGNORECASE,
)


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
    review_state: str = ""
    author_resolution_comment_id: int | None = None
    author_resolution_commit_sha: str = ""
    author_resolution: str = ""
    adjudication: LabelAdjudication = "unreviewed"
    adjudication_notes: str = ""
    human_approved: bool = False
    adjudicator: str = ""

    @property
    def human_adjudicated(self) -> bool:
        """Whether this decision has explicit approval from an identified human."""

        return (
            self.adjudication in {"confirmed_defect", "excluded"}
            and self.human_approved is True
            and isinstance(self.adjudicator, str)
            and bool(self.adjudicator.strip())
        )

    @property
    def confirmed(self) -> bool:
        """Whether an explicitly identified human confirmed this defect."""

        return self.adjudication == "confirmed_defect" and self.human_adjudicated

    @property
    def excluded(self) -> bool:
        """Whether an explicitly identified human excluded this candidate."""

        return self.adjudication == "excluded" and self.human_adjudicated


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


def is_resolution_reply(body: str) -> bool:
    """Whether a PR-author reply says the requested change was carried out.

    This is only a candidate-quality signal.  It never certifies the parent
    comment as a defect; the human adjudication gate remains mandatory.
    """

    text = body.strip()
    if not text or _NEGATED_RESOLUTION.search(text):
        return False
    return any(pattern.search(text) for pattern in _RESOLUTION_REPLY)


def is_likely_defect_comment(body: str) -> bool:
    """Whether a human comment describes a concrete behavioral failure.

    This deliberately conservative lexical gate is for candidate discovery,
    not certification.  It removes obvious prose/style requests before the
    mandatory human adjudication step.
    """

    text = body.strip()
    if not is_meaningful_comment(text):
        return False
    if any(pattern.search(text) for pattern in _NON_DEFECT_REQUEST):
        return False
    return any(pattern.search(text) for pattern in _DEFECT_LANGUAGE)


def is_reviewable_source_path(path: str) -> bool:
    """Whether a label targets Python product code rather than support prose/tests."""

    normalized = path.replace("\\", "/").casefold().strip("/")
    if not normalized.endswith(".py"):
        return False
    parts = normalized.split("/")
    excluded_segments = {
        "doc",
        "docs",
        "example",
        "examples",
        "test",
        "tests",
        "testing",
        "benchmark",
        "benchmarks",
    }
    return not excluded_segments.intersection(parts) and not parts[-1].startswith("test_")


def _direct_author_resolutions(
    comments: Sequence[dict[str, Any]],
    *,
    pr_author_login: str,
) -> dict[int, dict[str, Any]]:
    """Return one explicit completed-action reply for each root comment."""

    resolutions: dict[int, dict[str, Any]] = {}
    if not pr_author_login:
        return resolutions
    for comment in comments:
        parent = comment.get("in_reply_to_id")
        if parent is None:
            continue
        reviewer_login, account_type = _reviewer(comment)
        if _is_bot(reviewer_login, account_type):
            continue
        if reviewer_login.casefold() != pr_author_login.casefold():
            continue
        if not is_resolution_reply(str(comment.get("body") or "")):
            continue
        resolutions.setdefault(int(parent), comment)
    return resolutions


def _direct_thread_replies(comments: Sequence[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Group direct replies by their root review-comment id."""

    replies: dict[int, list[dict[str, Any]]] = {}
    for comment in comments:
        parent = comment.get("in_reply_to_id")
        if parent is not None:
            replies.setdefault(int(parent), []).append(comment)
    return replies


def labels_from_review_revision(
    comments: Sequence[dict[str, Any]],
    *,
    reviews: Mapping[int, str],
    pr_author_login: str,
    revision_sha: str,
    signal: CandidateSignal = "accepted",
) -> list[Label]:
    """Build candidates against the exact revision a reviewer saw.

    GitHub rewrites ``commit_id`` and ``line`` as a pull request evolves, while
    ``original_commit_id`` and ``original_line`` preserve the reviewed code.
    Ground-truth recovery therefore freezes a fixture at the original revision
    and treats an author's explicit completed-action reply or a formal
    ``CHANGES_REQUESTED`` review only as a high-signal lead for human review.
    """

    if signal not in {"defect", "accepted", "requested_changes", "all"}:
        raise ValueError(f"Unknown candidate signal: {signal}")

    resolutions = _direct_author_resolutions(comments, pr_author_login=pr_author_login)
    thread_replies = _direct_thread_replies(comments)
    labels: list[Label] = []
    for comment in comments:
        if comment.get("in_reply_to_id") is not None:
            continue
        body = str(comment.get("body") or "")
        path = str(comment.get("path") or "")
        line = comment.get("original_line") or comment.get("line")
        reviewer_login, account_type = _reviewer(comment)
        comment_id = int(comment["id"]) if comment.get("id") is not None else None
        review_commit_sha = str(comment.get("original_commit_id") or comment.get("commit_id") or "")
        review_id = comment.get("pull_request_review_id")
        review_state = reviews.get(int(review_id), "") if review_id is not None else ""
        resolution = resolutions.get(comment_id) if comment_id is not None else None
        replies = thread_replies.get(comment_id, []) if comment_id is not None else []

        if not path or not line or not is_meaningful_comment(body):
            continue
        if not is_reviewable_source_path(path):
            continue
        if reviewer_login.casefold() == pr_author_login.casefold():
            continue
        if _is_bot(reviewer_login, account_type):
            continue
        if review_commit_sha != revision_sha:
            continue
        if signal == "defect" and not is_likely_defect_comment(body):
            continue
        if (
            signal == "defect"
            and resolution is None
            and any(_DEFERRAL_REPLY.search(str(reply.get("body") or "")) for reply in replies)
        ):
            continue
        if (
            signal == "defect"
            and resolution is None
            and any(
                _REJECTION_REPLY.search(str(reply.get("body") or ""))
                and _reviewer(reply)[0].casefold() == pr_author_login.casefold()
                for reply in replies
            )
        ):
            continue
        if signal == "accepted" and resolution is None:
            continue
        if signal == "requested_changes" and review_state.upper() != "CHANGES_REQUESTED":
            continue

        category, severity = classify_comment(body)
        resolution_id = (
            int(resolution["id"])
            if resolution is not None and resolution.get("id") is not None
            else None
        )
        labels.append(
            Label(
                file_path=path,
                line=int(line),
                category=category,
                severity=severity,
                human_comment=body.strip(),
                expected_issue=body.strip()[:280],
                match_notes=(
                    "unadjudicated human review comment with concrete defect language"
                    if signal == "defect"
                    else (
                        "unadjudicated inline review comment with explicit PR-author resolution"
                        if resolution is not None
                        else "unadjudicated inline comment from a changes-requested review"
                    )
                ),
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
                review_state=review_state,
                author_resolution_comment_id=resolution_id,
                author_resolution_commit_sha=(
                    str(resolution.get("commit_id") or "") if resolution is not None else ""
                ),
                author_resolution=(
                    str(resolution.get("body") or "").strip() if resolution is not None else ""
                ),
            )
        )
    return labels


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

    scored = [label for label in case.labels if not label.excluded]
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
                "review_state": label.review_state,
                "source_comment_id": label.source_comment_id,
                "human_comment": label.human_comment,
                "category": label.category,
                "severity": label.severity,
                "author_resolution_comment_id": label.author_resolution_comment_id,
                "author_resolution_commit_sha": label.author_resolution_commit_sha,
                "author_resolution": label.author_resolution,
                "thread_replies": (
                    thread_replies.get(label.source_comment_id, [])
                    if label.source_comment_id is not None
                    else []
                ),
                "adjudication": label.adjudication,
                "adjudication_notes": label.adjudication_notes,
                "human_approved": label.human_approved,
                "adjudicator": label.adjudicator,
            }
            queue.append(row)
    return queue


def apply_adjudications(
    cases: Sequence[BenchmarkCase],
    decisions: Iterable[dict[str, Any]],
    *,
    require_complete: bool = True,
) -> list[BenchmarkCase]:
    """Apply decisions without mistaking provisional agent review for human approval."""

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
            raise ValueError(f"Adjudication for {label_id} needs notes explaining the decision")
        human_approved = decision.get("human_approved", False)
        if not isinstance(human_approved, bool):
            raise ValueError(f"Adjudication for {label_id} has a non-boolean human_approved")
        adjudicator_value = decision.get("adjudicator", "")
        if not isinstance(adjudicator_value, str):
            raise ValueError(f"Adjudication for {label_id} has a non-string adjudicator")
        adjudicator = adjudicator_value.strip()
        if verdict == "unreviewed" and human_approved:
            raise ValueError(
                f"Adjudication for {label_id} cannot be human-approved while unreviewed"
            )
        if human_approved and not adjudicator:
            raise ValueError(
                f"Adjudication for {label_id} needs an adjudicator when human_approved is true"
            )
        category = decision.get("category")
        if category is not None and (
            not isinstance(category, str) or category not in _ALLOWED_LABEL_CATEGORIES
        ):
            raise ValueError(f"Adjudication for {label_id} has an invalid category")
        severity = decision.get("severity")
        if severity is not None and (
            not isinstance(severity, str) or severity not in _ALLOWED_LABEL_SEVERITIES
        ):
            raise ValueError(f"Adjudication for {label_id} has an invalid severity")
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
                    human_approved=bool(decision.get("human_approved", False)),
                    adjudicator=str(decision.get("adjudicator", "")).strip(),
                    category=str(decision.get("category", label.category)),
                    severity=str(decision.get("severity", label.severity)),
                )
            )
        updated.append(replace(case, labels=labels))
    return updated
