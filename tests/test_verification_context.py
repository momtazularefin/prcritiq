"""Candidate-local evidence is bounded, revision-pinned, and never executed."""

from __future__ import annotations

import pytest

from prcritiq.findings import CandidateFinding
from prcritiq.verification_context import CandidateContext, build_candidate_context


def candidate(**overrides) -> CandidateFinding:
    fields = {
        "file_path": "src/app.py",
        "line": 2,
        "severity": "high",
        "confidence": 90,
        "category": "bug",
        "finding": "The helper rejects an input that the caller previously accepted.",
        "evidence": "The changed call invokes helper without the previous guard.",
        "suggested_fix": "Preserve the guard.",
    }
    return CandidateFinding(**{**fields, **overrides})


def context(source: str, *, base: str | None = None, finding=None, **kwargs) -> CandidateContext:
    return build_candidate_context(
        finding or candidate(),
        head_sources={"src/app.py": source},
        base_sources={"src/app.py": source if base is None else base},
        base_sha="base-sha",
        head_sha="head-sha",
        **kwargs,
    )


def assert_exact_snippets(result: CandidateContext, sources: dict[tuple[str, str], str]) -> None:
    for snippet in result.snippets:
        lines = sources[snippet.side, snippet.path].splitlines(keepends=True)
        assert 1 <= snippet.start_line <= snippet.end_line <= len(lines)
        assert snippet.text == "".join(lines[snippet.start_line - 1 : snippet.end_line])


def test_black_like_function_keeps_loop_break_after_exception_handler() -> None:
    source = (
        "def parse(source):\n"
        "    errors = []\n"
        "    for grammar in grammars:\n"
        "        try:\n"
        "            tree = grammar.parse(source)\n"
        "        except SyntaxError as exc:\n"
        "            errors.append(str(exc))\n"
        "        else:\n"
        "            break\n"
        "    else:\n"
        "        raise ValueError(errors)\n"
        "    return tree\n"
        "\n"
        "def unrelated():\n"
        "    return 1\n"
    )
    result = context(source, finding=candidate(line=7))
    assert not result.issues
    assert not result.truncated
    assert [item.side for item in result.snippets] == ["head", "base"]
    assert "            break\n" in result.snippets[0].text
    assert "    else:\n        raise ValueError(errors)" in result.snippets[0].text
    assert "unrelated" not in result.snippets[0].text
    assert_exact_snippets(result, {(side, "src/app.py"): source for side in ("head", "base")})


def test_ansible_like_function_keeps_collection_setup_and_loop() -> None:
    source = (
        "def select(values, attr):\n"
        "    fullcollection = list(values)\n"
        "    matches = []\n"
        "    for item in fullcollection:\n"
        "        try:\n"
        "            matches.append(item[attr])\n"
        "        except KeyError:\n"
        "            continue\n"
        "    if not matches:\n"
        "        raise ValueError(attr)\n"
        "    return matches\n"
    )
    result = context(source, finding=candidate(line=10))
    assert result.snippets[0].text == source
    assert "fullcollection = list(values)" in result.snippets[0].text
    assert "for item in fullcollection" in result.snippets[0].text


@pytest.mark.parametrize("line", [3, 5, 7])
def test_multiline_decorator_and_nested_control_flow_are_included(line: int) -> None:
    source = (
        "if enabled:\n"
        "    @register(\n"
        "        mode='safe',\n"
        "    )\n"
        "    async def helper(value):\n"
        "        if value:\n"
        "            return value\n"
    )
    result = context(source, finding=candidate(line=line))
    assert result.snippets[0].start_line == 2
    assert result.snippets[0].end_line == 7
    assert result.snippets[0].text.startswith("    @register(\n")


def test_parenthesized_decorator_keeps_at_sign_above_expression() -> None:
    source = "@(\n    register\n)\ndef helper():\n    return 1\n"
    result = context(source, finding=candidate(line=5))
    assert result.snippets[0].start_line == 1
    assert result.snippets[0].text == source


def test_base_counterpart_tracks_qualified_symbol_across_line_shifts_and_rename() -> None:
    base = "class First:\n    def target(self):\n        return 1\n"
    head = (
        "class Other:\n    def target(self):\n        return 0\n\n"
        "class First:\n    def target(self):\n        return 2\n"
    )
    result = build_candidate_context(
        candidate(file_path="new.py", line=7),
        head_sources={"new.py": head},
        base_sources={"old.py": base},
        head_sha="new",
        base_sha="old",
        previous_path="old.py",
    )
    assert not result.issues
    assert result.snippets[0].start_line == 6
    assert result.snippets[1].start_line == 2
    assert result.snippets[1].path == "old.py"
    assert "return 1" in result.snippets[1].text


def test_innermost_function_has_same_nested_qualified_counterpart() -> None:
    base = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    head = "\n\n" + base.replace("return 1", "return 2")
    result = context(head, base=base, finding=candidate(line=5))
    assert result.snippets[0].start_line == 4
    assert result.snippets[1].start_line == 2
    assert "def outer" not in result.snippets[0].text


@pytest.mark.parametrize("base", ["def other():\n    return 1\n", ""])
def test_missing_base_symbol_or_source_is_explicit_not_wrong_same_line(base: str) -> None:
    result = context("def helper():\n    return 2\n", base=base)
    assert len(result.snippets) == 1
    assert any(
        issue.startswith("base_symbol_missing:") or issue.startswith("missing_base_source:")
        for issue in result.issues
    )


def test_ambiguous_base_definition_is_not_silently_selected() -> None:
    source = "def helper():\n    return 2\n"
    result = context(source, base=source + source)
    assert len(result.snippets) == 1
    assert result.issues[0].startswith("base_symbol_ambiguous:")


def test_declared_new_file_needs_no_base_and_never_executes_source() -> None:
    source = "raise RuntimeError('must not execute')\ndef helper():\n    return 1\n"
    result = context(source, base="", finding=candidate(line=3), base_absent=True)
    assert not result.issues
    assert len(result.snippets) == 1


@pytest.mark.parametrize(
    "path", ["../a.py", "/a.py", "C:/a.py", "a\\b.py", "a/../b.py", "a//b.py", "a\x00.py"]
)
def test_unsafe_target_paths_are_not_read(path: str) -> None:
    result = build_candidate_context(
        candidate(file_path=path), head_sources={}, base_sources={}, base_sha="b", head_sha="h"
    )
    assert not result.snippets
    assert result.issues[0].startswith("unsafe_head_path:")


@pytest.mark.parametrize("line", [-1, 0, 3])
def test_out_of_range_head_line_is_explicit(line: int) -> None:
    result = context("def helper():\n    return 1\n", finding=candidate(line=line))
    assert not result.snippets
    assert result.issues[0].startswith("head_line_out_of_range:")


def test_missing_head_and_unsafe_base_are_explicit() -> None:
    missing = build_candidate_context(
        candidate(), head_sources={}, base_sources={}, base_sha="b", head_sha="h"
    )
    assert missing.issues[0].startswith("missing_head_source:")
    unsafe = context("def helper():\n    return 1\n", previous_path="../old.py")
    assert len(unsafe.snippets) == 1
    assert unsafe.issues[0].startswith("unsafe_base_path:")


def test_unparseable_python_uses_bounded_window_on_both_sides() -> None:
    source = "def broken(:\n" + "    something\n" * 10
    result = context(source, finding=candidate(line=6), max_lines=3)
    assert len(result.snippets) == 2
    assert all(item.start_line <= 6 <= item.end_line for item in result.snippets)
    assert all(item.end_line - item.start_line + 1 == 3 for item in result.snippets)
    assert all(item.truncated for item in result.snippets)
    assert result.truncated
    assert len(result.issues) == 2


def test_non_python_uses_bounded_window() -> None:
    result = build_candidate_context(
        candidate(file_path="app.ts", line=5),
        head_sources={"app.ts": "a();\n" * 10},
        base_sources={"app.ts": "a();\n" * 10},
        base_sha="b",
        head_sha="h",
        max_lines=3,
    )
    assert not result.issues
    assert all(item.end_line - item.start_line == 2 for item in result.snippets)
    assert result.truncated


@pytest.mark.parametrize("max_bytes", [1, 30, 80, 200])
def test_unicode_byte_and_line_caps_preserve_complete_source_lines(max_bytes: int) -> None:
    source = "def helper():\n" + "    print('বাংলা☃')\n" * 20
    result = context(source, finding=candidate(line=10), max_bytes=max_bytes, max_lines=5)
    assert sum(len(item.text.encode("utf-8")) for item in result.snippets) <= max_bytes
    assert all(item.end_line - item.start_line + 1 <= 5 for item in result.snippets)
    assert result.truncated
    assert_exact_snippets(result, {(side, "src/app.py"): source for side in ("head", "base")})


def test_unicode_line_separator_is_not_an_extra_source_line() -> None:
    source = "def helper():\n    return 'one\u2028two'\n"
    result = context(source)
    assert result.snippets[0].end_line == 2
    assert result.snippets[0].text == source


def test_stable_ids_change_with_revision_side_or_content() -> None:
    source = "def helper():\n    return 1\n"
    first = context(source)
    assert first == context(source)
    assert first.snippets[0].source_id != first.snippets[1].source_id
    changed = context(source.replace("return 1", "return 2"))
    assert first.snippets[0].source_id != changed.snippets[0].source_id
    revision = build_candidate_context(
        candidate(),
        head_sources={"src/app.py": source},
        base_sources={"src/app.py": source},
        base_sha="base-sha",
        head_sha="other-head",
    )
    assert first.snippets[0].source_id != revision.snippets[0].source_id
    assert first.snippets[1].source_id == revision.snippets[1].source_id


def test_helpers_are_identifier_matched_deterministic_and_at_most_two() -> None:
    source = "def target():\n    return helper()\n"
    helper = "import os\n\ndef helper():\n    return 1\n\ndef unrelated():\n    return 2\n"
    kwargs = {
        "head_sources": {"src/app.py": source, "b.py": helper, "a.py": helper, "c.py": helper},
        "base_sources": {"src/app.py": source},
        "base_sha": "b",
        "head_sha": "h",
    }
    first = build_candidate_context(candidate(), related_paths=["c.py", "b.py", "a.py"], **kwargs)
    second = build_candidate_context(candidate(), related_paths=["a.py", "c.py", "b.py"], **kwargs)
    assert first == second
    assert [item.path for item in first.snippets] == ["src/app.py", "src/app.py", "a.py", "b.py"]
    assert all(
        "import os" not in item.text and "unrelated" not in item.text for item in first.snippets[2:]
    )


def test_unsafe_and_missing_related_files_report_issues() -> None:
    result = context("def helper():\n    return 1\n", related_paths=["../bad.py", "missing.py"])
    assert len(result.snippets) == 2
    assert any(issue.startswith("unsafe_related_path:") for issue in result.issues)
    assert any(issue.startswith("missing_related_source:") for issue in result.issues)


def test_actual_bare_call_and_top_level_helper_outrank_same_named_methods() -> None:
    source = "def target():\n    return qualname(value)\n"
    result = build_candidate_context(
        candidate(finding="serialize may fail", evidence="ValueError in serialize", line=2),
        head_sources={
            "src/app.py": source,
            "a.py": "class A:\n    def serialize(self):\n        pass\n"
            "    def qualname(self):\n        pass\n",
            "z.py": "def qualname(value):\n    raise ValueError(value)\n",
        },
        base_sources={"src/app.py": source},
        base_sha="b",
        head_sha="h",
        related_paths=["a.py", "z.py"],
    )
    assert result.snippets[2].path == "z.py"
    assert "raise ValueError" in result.snippets[2].text


@pytest.mark.parametrize("kwargs", [{"max_bytes": 0}, {"max_lines": 0}, {"max_bytes": -1}])
def test_invalid_limits_fail_explicitly(kwargs) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        context("def helper():\n    return 1\n", **kwargs)
