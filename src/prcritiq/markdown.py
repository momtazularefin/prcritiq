"""Markdown rendering of a review report.

The JSON report is the machine record. This is the one a person reads in a
README or an evaluation write-up, so it leads with what was found and keeps the
stages that ran visible, rather than presenting an empty findings list as a
verdict it has not earned.
"""

from __future__ import annotations

from .schemas import ReviewReport


def _escape(text: str) -> str:
    """Keep untrusted pull request text from breaking out of a table cell."""

    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_report(report: ReviewReport) -> str:
    """Render one review report as Markdown."""

    lines: list[str] = [
        f"# PRCritiq review of {report.repo}#{report.pr_number}",
        "",
        f"**{_escape(report.title)}** by @{report.author_login} "
        f"([open on GitHub]({report.html_url}))",
        "",
        f"- Head: `{report.head_sha[:12]}`",
        f"- Files changed: {report.total_files} "
        f"({report.reviewable_files} reviewable, {report.skipped_files} skipped)",
        f"- Commentable lines: {report.commentable_lines}",
    ]

    if report.run is not None:
        lines.append(f"- Run: `{report.run.run_id}` ({report.run.status})")
        if report.run.trace_id:
            lines.append(f"- Trace: `{report.run.trace_id}` ({report.run.trace_provider})")

    if report.review is not None:
        lines.append(f"- Model: `{report.review.provider}/{report.review.model}`")

    lines.extend(["", "## Findings", ""])

    if report.review is None:
        lines.append(
            "No review was run. This report covers diff parsing and guardrails only, "
            "so the absence of findings is not a clean bill of health."
        )
    elif not report.findings:
        suppressed = report.review.suppressed
        if suppressed:
            lines.append(
                f"No findings met the bar. {suppressed} of {report.review.candidates} "
                "candidates were suppressed by self-critique."
            )
        else:
            lines.append("No findings. The review drafted no candidates for these changes.")
    else:
        for item in report.findings:
            lines.extend(
                [
                    f"### {item.file_path}:{item.line} — {item.severity} ({item.confidence}%)",
                    "",
                    f"*{item.category}*",
                    "",
                    f"{item.finding}",
                    "",
                    f"**Evidence:** {item.evidence}",
                    "",
                    f"**Suggested fix:** {item.suggested_fix}",
                    "",
                ]
            )
    lines.append("")

    if report.review is not None and report.review.suppressed_by_reason:
        lines.extend(["## Suppressed", "", "| Reason | Count |", "| --- | --- |"])
        lines.extend(
            f"| {reason} | {count} |"
            for reason, count in sorted(report.review.suppressed_by_reason.items())
        )
        lines.extend(["", f"Invalid-line rate: {report.review.invalid_line_rate:.1%}", ""])

    skipped = [item for item in report.files if item.decision != "reviewable"]
    if skipped:
        lines.extend(
            ["## Files not reviewed", "", "| File | Decision | Reason |", "| --- | --- | --- |"]
        )
        lines.extend(
            f"| `{item.path}` | {item.decision} | {_escape(item.reason)} |" for item in skipped
        )
        lines.append("")

    if report.tools:
        lines.extend(
            [
                "## Tools",
                "",
                "| Tool | Status | Diagnostics | Notes |",
                "| --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| {tool.tool} | {tool.status} | {len(tool.diagnostics)} "
            f"({tool.diagnostics_on_changed_lines} on changed lines) | {_escape(tool.reason)} |"
            for tool in report.tools
        )
        lines.append("")

    lines.extend(["---", "", f"_{report.message}_"])
    return "\n".join(lines) + "\n"
