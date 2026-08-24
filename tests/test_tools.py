"""Tests for the static-analysis tool runner.

The security boundary carries the weight here: an analysis step that can be
steered by the pull request under review is worse than no analysis at all.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from prcritiq.config import Settings
from prcritiq.diff import build_file_diff
from prcritiq.tools import (
    ToolError,
    ToolStatus,
    build_child_environment,
    execute_command,
    parse_ruff_diagnostics,
    resolve_executable,
    run_static_analysis,
)

PATCH = "@@ -1,1 +1,3 @@\n import os\n+VALUE = 1\n+OTHER = 2\n"


def python_file_diff(path: str = "src/app.py", language_path: str | None = None):
    return build_file_diff(
        path=language_path or path,
        status="modified",
        additions=2,
        deletions=0,
        changes=2,
        patch=PATCH,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import os\nVALUE = 1\nOTHER = 2\n", encoding="utf-8")
    return root


class TestChildEnvironment:
    def test_secrets_are_never_passed_to_a_tool(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "GITHUB_TOKEN": "ghp_secret",
            "ANTHROPIC_API_KEY": "sk-secret",
            "DATABASE_URL": "postgres://user:pw@host/db",
        }

        child = build_child_environment(parent)

        assert child == {"PATH": "/usr/bin"}

    def test_an_unknown_variable_is_dropped_by_default(self) -> None:
        """An allowlist, so a newly added secret is excluded without being listed."""

        child = build_child_environment({"PATH": "/usr/bin", "SOME_NEW_SECRET": "value"})

        assert "SOME_NEW_SECRET" not in child

    def test_a_real_process_does_not_see_a_token(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_must_not_leak")

        outcome = execute_command(
            Path(sys.executable),
            ["-c", "import os; print(os.environ.get('GITHUB_TOKEN', 'ABSENT'))"],
            workspace_root=workspace,
            settings=Settings(),
        )

        assert outcome.stdout.strip() == "ABSENT"


class TestExecutableResolution:
    def test_an_executable_inside_the_workspace_is_refused(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repository must never supply the binary that analyzes it."""

        planted = workspace / "ruff"
        planted.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(shutil, "which", lambda _name: str(planted))

        assert resolve_executable("ruff", workspace) is None

    def test_a_missing_executable_resolves_to_none(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert resolve_executable("ruff", workspace) is None

    def test_an_executable_outside_the_workspace_is_accepted(self, workspace: Path) -> None:
        assert resolve_executable(Path(sys.executable).stem, workspace) is not None


class TestExecuteCommand:
    def test_output_and_exit_code_are_captured(self, workspace: Path) -> None:
        outcome = execute_command(
            Path(sys.executable),
            ["-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
            workspace_root=workspace,
            settings=Settings(),
        )

        assert outcome.exit_code == 3
        assert "out" in outcome.stdout
        assert "err" in outcome.stderr
        assert outcome.timed_out is False

    def test_a_slow_command_is_timed_out(self, workspace: Path) -> None:
        outcome = execute_command(
            Path(sys.executable),
            ["-c", "import time; time.sleep(30)"],
            workspace_root=workspace,
            settings=Settings(tool_timeout_seconds=1),
        )

        assert outcome.timed_out is True
        assert outcome.exit_code is None

    def test_output_is_capped(self, workspace: Path) -> None:
        outcome = execute_command(
            Path(sys.executable),
            ["-c", "print('x' * 100000)"],
            workspace_root=workspace,
            settings=Settings(max_tool_output_bytes=500),
        )

        assert len(outcome.stdout) <= 500

    def test_the_command_runs_inside_the_workspace(self, workspace: Path) -> None:
        outcome = execute_command(
            Path(sys.executable),
            ["-c", "import os; print(os.getcwd())"],
            workspace_root=workspace,
            settings=Settings(),
        )

        assert Path(outcome.stdout.strip()).resolve() == workspace.resolve()

    def test_arguments_are_never_reinterpreted_by_a_shell(self, workspace: Path) -> None:
        """No shell, so shell metacharacters stay inert data."""

        marker = workspace / "pwned.txt"
        outcome = execute_command(
            Path(sys.executable),
            ["-c", "print('safe')", f"; touch {marker}"],
            workspace_root=workspace,
            settings=Settings(),
        )

        assert outcome.exit_code == 0
        assert not marker.exists()


class TestRuffParsing:
    def test_diagnostics_are_structured_and_relative(self, workspace: Path) -> None:
        payload = json.dumps(
            [
                {
                    "code": "F401",
                    "message": "`os` imported but unused",
                    "filename": str(workspace / "src" / "app.py"),
                    "location": {"row": 1, "column": 8},
                }
            ]
        )

        diagnostics = parse_ruff_diagnostics(payload, {}, workspace)

        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/app.py"
        assert diagnostics[0].code == "F401"
        assert diagnostics[0].line == 1

    def test_changed_lines_are_flagged(self, workspace: Path) -> None:
        payload = json.dumps(
            [
                {
                    "code": "F401",
                    "message": "unused",
                    "filename": str(workspace / "src" / "app.py"),
                    "location": {"row": 1, "column": 1},
                },
                {
                    "code": "E225",
                    "message": "spacing",
                    "filename": str(workspace / "src" / "app.py"),
                    "location": {"row": 2, "column": 1},
                },
            ]
        )

        diagnostics = parse_ruff_diagnostics(payload, {"src/app.py": frozenset({2})}, workspace)

        assert [item.on_changed_line for item in diagnostics] == [False, True]

    def test_empty_output_is_no_diagnostics(self, workspace: Path) -> None:
        assert parse_ruff_diagnostics("", {}, workspace) == ()
        assert parse_ruff_diagnostics("[]", {}, workspace) == ()

    def test_malformed_output_raises_rather_than_reading_as_clean(self, workspace: Path) -> None:
        """A broken parser must not be indistinguishable from a clean result."""

        with pytest.raises(ToolError, match="not valid JSON"):
            parse_ruff_diagnostics("not json at all", {}, workspace)

    def test_unexpected_json_shape_raises(self, workspace: Path) -> None:
        with pytest.raises(ToolError, match="not a list"):
            parse_ruff_diagnostics('{"code": "F401"}', {}, workspace)


class TestRunStaticAnalysis:
    def test_ruff_reports_diagnostics_for_changed_python(self, workspace: Path) -> None:
        if resolve_executable("ruff", workspace) is None:
            pytest.skip("ruff is not installed on PATH")

        runs = run_static_analysis(workspace, [python_file_diff()], Settings())
        ruff = next(run for run in runs if run.tool == "ruff")

        assert ruff.status is ToolStatus.OK
        assert any(item.code == "F401" for item in ruff.diagnostics)

    def test_files_absent_from_the_snapshot_are_skipped(self, workspace: Path) -> None:
        runs = run_static_analysis(workspace, [python_file_diff("src/missing.py")], Settings())
        ruff = next(run for run in runs if run.tool == "ruff")

        assert ruff.status is ToolStatus.NOT_APPLICABLE

    def test_missing_ruff_is_reported_not_silently_clean(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        runs = run_static_analysis(workspace, [python_file_diff()], Settings())
        ruff = next(run for run in runs if run.tool == "ruff")

        assert ruff.status is ToolStatus.NOT_INSTALLED
        assert ruff.diagnostics == ()


class TestJavaScriptGate:
    def javascript_workspace(self, workspace: Path) -> Path:
        (workspace / "web").mkdir(parents=True, exist_ok=True)
        (workspace / "web" / "main.ts").write_text("export const a = 1;\n", encoding="utf-8")
        return workspace

    def js_diff(self):
        return build_file_diff(
            path="web/main.ts",
            status="modified",
            additions=1,
            deletions=0,
            changes=1,
            patch="@@ -1,1 +1,2 @@\n const a = 1;\n+const b = 2;\n",
        )

    def test_eslint_is_refused_when_a_config_exists(self, workspace: Path) -> None:
        """Running eslint on an untrusted repo executes that repo's own code."""

        root = self.javascript_workspace(workspace)
        (root / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")

        runs = run_static_analysis(root, [self.js_diff()], Settings())
        eslint = next(run for run in runs if run.tool == "eslint")

        assert eslint.status is ToolStatus.REFUSED_UNSAFE
        assert "execute that pull request" in eslint.reason

    def test_eslint_without_config_is_not_applicable(self, workspace: Path) -> None:
        root = self.javascript_workspace(workspace)

        runs = run_static_analysis(root, [self.js_diff()], Settings())
        eslint = next(run for run in runs if run.tool == "eslint")

        assert eslint.status is ToolStatus.NOT_APPLICABLE

    def test_tsc_is_refused_when_a_tsconfig_exists(self, workspace: Path) -> None:
        root = self.javascript_workspace(workspace)
        (root / "tsconfig.json").write_text("{}\n", encoding="utf-8")

        runs = run_static_analysis(root, [self.js_diff()], Settings())
        tsc = next(run for run in runs if run.tool == "tsc")

        assert tsc.status is ToolStatus.REFUSED_UNSAFE
        assert "node_modules" in tsc.reason

    def test_no_javascript_files_means_no_javascript_runs(self, workspace: Path) -> None:
        """A reader should not see a JS verdict on a pull request with no JS."""

        runs = run_static_analysis(workspace, [python_file_diff()], Settings())

        assert {run.tool for run in runs} == {"ruff"}
