"""Safe static-analysis evidence from allowlisted tools.

Pull request code is untrusted (ADR-006). Every protection here follows from
that: commands come from a fixed allowlist rather than from anything in the
repository, no shell is involved, the executable must resolve outside the
workspace so a repository cannot supply its own binary, the child process gets a
scrubbed environment so credentials never reach it, and runs are bounded by a
timeout and an output cap.

Tool output is evidence data. Nothing a tool prints is ever treated as an
instruction (FR15).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final

from .config import Settings
from .diff import FileDiff

#: Tool name to the executable it needs. Nothing outside this mapping can run.
_ALLOWLIST: Final[dict[str, str]] = {"ruff": "ruff"}

#: Environment variables a child process may inherit. Everything else is dropped
#: so that tokens and API keys in this process never reach an analysis tool.
_ENV_PASSTHROUGH: Final[tuple[str, ...]] = (
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "HOME",
)

_ESLINT_CONFIGS: Final[tuple[str, ...]] = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
)

_ESLINT_REFUSAL: Final = (
    "eslint loads the repository's own configuration as JavaScript and resolves "
    "plugins from its node_modules, so running it against an untrusted pull "
    "request would execute that pull request's code. ADR-006 rules that out."
)

_TSC_REFUSAL: Final = (
    "the workspace snapshot deliberately excludes node_modules, so tsc would "
    "report a missing module for every dependency instead of real findings. "
    "Installing dependencies would execute the pull request's install scripts."
)


class ToolStatus(StrEnum):
    """How a tool run ended."""

    OK = "ok"
    NOT_APPLICABLE = "not_applicable"
    NOT_INSTALLED = "not_installed"
    REFUSED_UNSAFE = "refused_unsafe"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True)
class Diagnostic:
    """One structured finding reported by a tool."""

    tool: str
    path: str
    line: int
    column: int
    code: str
    message: str
    on_changed_line: bool


@dataclass(frozen=True)
class CommandOutcome:
    """The raw result of one subprocess."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class ToolRun:
    """One tool's contribution to the evidence for a review."""

    tool: str
    status: ToolStatus
    reason: str
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    duration_seconds: float = 0.0
    diagnostics: tuple[Diagnostic, ...] = ()
    stderr_excerpt: str = ""

    @property
    def ran(self) -> bool:
        return self.status is ToolStatus.OK


class ToolError(RuntimeError):
    """Raised when a tool cannot be run safely."""


def build_child_environment(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the minimal environment an analysis tool may see.

    An allowlist rather than a denylist: a new secret added to this process must
    not silently become readable by a subprocess because nobody remembered to
    add it to a list of things to strip.
    """

    source = os.environ if parent is None else parent
    return {name: source[name] for name in _ENV_PASSTHROUGH if name in source}


def resolve_executable(name: str, workspace_root: Path) -> Path | None:
    """Find an executable, refusing anything inside the untrusted workspace.

    A repository that ships its own `ruff`, or that persuades the resolver to
    prefer a binary it controls, would otherwise get arbitrary code execution
    out of a static-analysis step.
    """

    found = shutil.which(name)
    if not found:
        return None
    resolved = Path(found).resolve()
    if resolved.is_relative_to(workspace_root.resolve()):
        return None
    return resolved


def execute_command(
    executable: Path,
    arguments: Sequence[str],
    *,
    workspace_root: Path,
    settings: Settings,
) -> CommandOutcome:
    """Run one command inside the workspace under a timeout and an output cap.

    Never uses a shell, so no argument can be reinterpreted as a command.
    """

    started = time.monotonic()
    try:
        # Fixed executable from the allowlist, argument list, and no shell.
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=str(workspace_root),
            env=build_child_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.tool_timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandOutcome(
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            timed_out=True,
        )

    cap = settings.max_tool_output_bytes
    return CommandOutcome(
        exit_code=completed.returncode,
        stdout=(completed.stdout or "")[:cap],
        stderr=(completed.stderr or "")[:cap],
        duration_seconds=time.monotonic() - started,
        timed_out=False,
    )


def parse_ruff_diagnostics(
    payload: str,
    changed_lines: Mapping[str, frozenset[int]],
    workspace_root: Path,
) -> tuple[Diagnostic, ...]:
    """Turn ruff JSON output into structured diagnostics.

    Malformed output raises rather than being silently read as "no findings",
    because a clean report that actually means "the parser broke" is exactly the
    kind of false assurance this project must not produce.
    """

    if not payload.strip():
        return ()
    try:
        entries = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ToolError(f"ruff produced output that is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise ToolError("ruff JSON output was not a list of diagnostics")

    diagnostics: list[Diagnostic] = []
    for entry in entries:
        location = entry.get("location") or {}
        line = int(location.get("row", 0) or 0)
        relative = _relative_path(entry.get("filename", ""), workspace_root)
        diagnostics.append(
            Diagnostic(
                tool="ruff",
                path=relative,
                line=line,
                column=int(location.get("column", 0) or 0),
                code=str(entry.get("code") or "unknown"),
                message=str(entry.get("message") or ""),
                on_changed_line=line in changed_lines.get(relative, frozenset()),
            )
        )
    return tuple(diagnostics)


def _relative_path(filename: str, workspace_root: Path) -> str:
    if not filename:
        return ""
    try:
        relative = Path(filename).resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return filename
    return str(PurePosixPath(*relative.parts))


def _run_ruff(
    workspace_root: Path,
    python_paths: Sequence[str],
    changed_lines: Mapping[str, frozenset[int]],
    settings: Settings,
) -> ToolRun:
    if not python_paths:
        return ToolRun("ruff", ToolStatus.NOT_APPLICABLE, "no reviewable Python files changed")

    executable = resolve_executable(_ALLOWLIST["ruff"], workspace_root)
    if executable is None:
        return ToolRun(
            "ruff",
            ToolStatus.NOT_INSTALLED,
            "ruff was not found on PATH outside the workspace",
        )

    # The repository's own ruff configuration is honored. It is declarative TOML
    # that ruff never executes, and respecting a project's chosen rules produces
    # far fewer false positives than imposing one ruleset on every repository.
    arguments = ["check", "--output-format", "json", "--no-cache", "--", *python_paths]
    outcome = execute_command(
        executable, arguments, workspace_root=workspace_root, settings=settings
    )

    if outcome.timed_out:
        return ToolRun(
            "ruff",
            ToolStatus.TIMED_OUT,
            f"ruff exceeded the {settings.tool_timeout_seconds}s timeout",
            command=("ruff", *arguments),
            duration_seconds=outcome.duration_seconds,
        )

    # ruff exits 1 when it finds violations, which is a successful run.
    if outcome.exit_code not in (0, 1):
        return ToolRun(
            "ruff",
            ToolStatus.FAILED,
            f"ruff exited with code {outcome.exit_code}",
            command=("ruff", *arguments),
            exit_code=outcome.exit_code,
            duration_seconds=outcome.duration_seconds,
            stderr_excerpt=outcome.stderr[:1000],
        )

    try:
        diagnostics = parse_ruff_diagnostics(outcome.stdout, changed_lines, workspace_root)
    except ToolError as exc:
        return ToolRun(
            "ruff",
            ToolStatus.FAILED,
            str(exc),
            command=("ruff", *arguments),
            exit_code=outcome.exit_code,
            duration_seconds=outcome.duration_seconds,
            stderr_excerpt=outcome.stderr[:1000],
        )

    return ToolRun(
        "ruff",
        ToolStatus.OK,
        f"ruff reported {len(diagnostics)} diagnostics across {len(python_paths)} files",
        command=("ruff", *arguments),
        exit_code=outcome.exit_code,
        duration_seconds=outcome.duration_seconds,
        diagnostics=diagnostics,
        stderr_excerpt=outcome.stderr[:1000],
    )


def _javascript_runs(workspace_root: Path, has_js_files: bool) -> list[ToolRun]:
    """Evaluate the JS/TS gate: config must exist and the command must be safe.

    Both tools are reported rather than omitted. A reader deserves to know a
    check was considered and why it did not run, instead of inferring from
    silence that JavaScript was simply clean.
    """

    if not has_js_files:
        return []

    runs: list[ToolRun] = []
    eslint_config = next(
        (name for name in _ESLINT_CONFIGS if (workspace_root / name).exists()), None
    )
    if eslint_config is None:
        runs.append(ToolRun("eslint", ToolStatus.NOT_APPLICABLE, "no eslint configuration found"))
    else:
        runs.append(ToolRun("eslint", ToolStatus.REFUSED_UNSAFE, _ESLINT_REFUSAL))

    if (workspace_root / "tsconfig.json").exists():
        runs.append(ToolRun("tsc", ToolStatus.REFUSED_UNSAFE, _TSC_REFUSAL))
    else:
        runs.append(ToolRun("tsc", ToolStatus.NOT_APPLICABLE, "no tsconfig.json found"))
    return runs


def run_static_analysis(
    workspace_root: Path,
    file_diffs: Sequence[FileDiff],
    settings: Settings,
) -> tuple[ToolRun, ...]:
    """Run the allowlisted static checks over the reviewable changed files."""

    present = [
        file_diff
        for file_diff in file_diffs
        if (workspace_root / Path(*PurePosixPath(file_diff.path).parts)).is_file()
    ]
    python_paths = [item.path for item in present if item.language == "python"]
    changed_lines = {item.path: item.commentable_lines for item in present}
    has_js = any(item.language in {"javascript", "typescript"} for item in present)

    runs = [_run_ruff(workspace_root, python_paths, changed_lines, settings)]
    runs.extend(_javascript_runs(workspace_root, has_js))
    return tuple(runs)
