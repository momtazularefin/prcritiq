"""The PRCritiq review graph.

The node sequence is the one design.md specifies:

    fetch_diff -> guardrail_gate -> static_analysis -> retrieve_context
               -> reason_and_draft -> self_critique -> post_or_summarize

Each node is a plain function over the shared state, so every stage can be
tested on its own and the control points stay visible. Building it as a graph
rather than one prompt behind a webhook is the point of ADR-007: the suppression
and validation stages are named, inspectable steps, not prompt instructions the
model may or may not have honored.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .critique import critique
from .diff import DiffIndex, FileDiff, build_diff_index, file_diff_from_changed_file
from .findings import CandidateFinding, ReviewedFinding
from .github import ChangedFile, PullRequestMetadata
from .guardrails import GuardrailOutcome, apply_guardrails, reviewable_files
from .prompts import SYSTEM_PROMPT, build_review_prompt
from .providers import ModelChoice, ModelProvider, Usage, build_provider, route
from .retrieval import RetrievalResult
from .tools import ToolRun


class ReviewState(TypedDict, total=False):
    """Everything one review run carries between nodes."""

    settings: Settings
    metadata: PullRequestMetadata
    changed_files: Sequence[ChangedFile]
    provider: ModelProvider | None
    task: str

    file_diffs: list[FileDiff]
    outcomes: tuple[GuardrailOutcome, ...]
    in_scope: tuple[FileDiff, ...]
    diff_index: DiffIndex
    retrieval: RetrievalResult | None
    tool_runs: tuple[ToolRun, ...] | None

    model_choice: ModelChoice | None
    usage: Usage | None
    drafted: list[CandidateFinding]
    reviewed: tuple[ReviewedFinding, ...]
    summary: str
    notes: Annotated[list[str], lambda left, right: [*left, *right]]


def fetch_diff(state: ReviewState) -> dict[str, Any]:
    """Parse the changed files this run was given into diffs."""

    file_diffs = [file_diff_from_changed_file(item) for item in state["changed_files"]]
    return {
        "file_diffs": file_diffs,
        "diff_index": build_diff_index(file_diffs),
        "notes": [f"fetch_diff: {len(file_diffs)} changed files"],
    }


def guardrail_gate(state: ReviewState) -> dict[str, Any]:
    """Decide which changed files are in scope for review."""

    outcomes = apply_guardrails(state["file_diffs"], state["settings"])
    in_scope = reviewable_files(state["file_diffs"], outcomes)
    return {
        "outcomes": outcomes,
        "in_scope": in_scope,
        "notes": [f"guardrail_gate: {len(in_scope)} of {len(outcomes)} files reviewable"],
    }


def static_analysis(state: ReviewState) -> dict[str, Any]:
    """Carry the tool evidence this run was given, if any.

    Tools run inside the workspace before the graph starts, because the snapshot
    is removed as soon as evidence gathering finishes. The node is kept so the
    graph matches the design and so a later milestone can move execution here.
    """

    runs = state.get("tool_runs")
    count = sum(len(run.diagnostics) for run in runs) if runs else 0
    return {"notes": [f"static_analysis: {count} diagnostics"]}


def retrieve_context(state: ReviewState) -> dict[str, Any]:
    """Carry the retrieved context this run was given, if any."""

    retrieval = state.get("retrieval")
    size = len(retrieval.chunk_ids) if retrieval else 0
    return {"notes": [f"retrieve_context: {size} chunks"]}


def reason_and_draft(state: ReviewState) -> dict[str, Any]:
    """Ask the routed model for candidate findings."""

    settings = state["settings"]
    in_scope = state.get("in_scope") or ()
    if not in_scope:
        return {
            "model_choice": None,
            "notes": ["reason_and_draft: skipped, no reviewable files"],
        }

    prompt = build_review_prompt(
        repo=state["metadata"].repo,
        pr_number=state["metadata"].number,
        title=state["metadata"].title,
        file_diffs=in_scope,
        retrieval=state.get("retrieval"),
        tool_runs=state.get("tool_runs"),
    )
    choice = route(
        settings=settings,
        prompt_characters=len(prompt),
        task=state.get("task", "review_synthesis"),
    )
    provider = state.get("provider") or build_provider(choice, settings)
    result = provider.draft(system=SYSTEM_PROMPT, user=prompt, choice=choice)
    drafted = result.findings

    return {
        "model_choice": choice,
        "drafted": drafted.findings,
        "usage": result.usage,
        "notes": [
            f"reason_and_draft: {choice.provider}/{choice.model} drafted "
            f"{len(drafted.findings)} candidates"
        ],
    }


def self_critique(state: ReviewState) -> dict[str, Any]:
    """Validate every candidate against the run's own evidence."""

    candidates = state.get("drafted") or []
    reviewed = critique(
        candidates,
        diff_index=state["diff_index"],
        settings=state["settings"],
        retrieval=state.get("retrieval"),
        tool_runs=state.get("tool_runs"),
    )
    published = sum(1 for item in reviewed if item.published)
    return {
        "reviewed": reviewed,
        "notes": [f"self_critique: {published} published, {len(reviewed) - published} suppressed"],
    }


def post_or_summarize(state: ReviewState) -> dict[str, Any]:
    """Produce the run summary. Posting arrives in M7."""

    reviewed = state.get("reviewed") or ()
    published = [item for item in reviewed if item.published]
    if not reviewed:
        summary = "No candidate findings were drafted."
    elif not published:
        summary = (
            f"All {len(reviewed)} candidate findings were suppressed by self-critique. "
            "Nothing met the bar for publication."
        )
    else:
        summary = f"{len(published)} of {len(reviewed)} candidate findings met the bar."
    return {"summary": summary, "notes": [f"post_or_summarize: {summary}"]}


def build_review_graph() -> Any:
    """Compile the review graph."""

    graph = StateGraph(ReviewState)
    graph.add_node("fetch_diff", fetch_diff)
    graph.add_node("guardrail_gate", guardrail_gate)
    graph.add_node("static_analysis", static_analysis)
    graph.add_node("retrieve_context", retrieve_context)
    graph.add_node("reason_and_draft", reason_and_draft)
    graph.add_node("self_critique", self_critique)
    graph.add_node("post_or_summarize", post_or_summarize)

    graph.add_edge(START, "fetch_diff")
    graph.add_edge("fetch_diff", "guardrail_gate")
    graph.add_edge("guardrail_gate", "static_analysis")
    graph.add_edge("static_analysis", "retrieve_context")
    graph.add_edge("retrieve_context", "reason_and_draft")
    graph.add_edge("reason_and_draft", "self_critique")
    graph.add_edge("self_critique", "post_or_summarize")
    graph.add_edge("post_or_summarize", END)
    return graph.compile()


NODE_SEQUENCE: tuple[str, ...] = (
    "fetch_diff",
    "guardrail_gate",
    "static_analysis",
    "retrieve_context",
    "reason_and_draft",
    "self_critique",
    "post_or_summarize",
)


def run_review_graph(
    *,
    settings: Settings,
    metadata: PullRequestMetadata,
    changed_files: Sequence[ChangedFile],
    retrieval: RetrievalResult | None = None,
    tool_runs: Sequence[ToolRun] | None = None,
    provider: ModelProvider | None = None,
    task: str = "review_synthesis",
) -> ReviewState:
    """Run one review through the graph and return its final state."""

    graph = build_review_graph()
    initial: ReviewState = {
        "settings": settings,
        "metadata": metadata,
        "changed_files": list(changed_files),
        "retrieval": retrieval,
        "tool_runs": tuple(tool_runs) if tool_runs is not None else None,
        "provider": provider,
        "task": task,
        "notes": [],
    }
    return graph.invoke(initial)
