"""Pure, bounded source evidence for one candidate's semantic verification.

The caller supplies revision-pinned file contents. This module never opens,
imports, or executes repository files. Snippets retain complete source lines;
an oversized line is omitted with an issue rather than silently cut in half.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .findings import CandidateFinding

Side = Literal["base", "head"]
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")


@dataclass(frozen=True)
class EvidenceSnippet:
    source_id: str
    side: Side
    path: str
    start_line: int
    end_line: int
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class CandidateContext:
    snippets: tuple[EvidenceSnippet, ...]
    issues: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class _Symbol:
    qualified_name: tuple[str, ...]
    start: int
    end: int
    is_function: bool


@dataclass(frozen=True)
class _Source:
    lines: tuple[str, ...]
    symbols: tuple[_Symbol, ...]
    parse_failed: bool = False


@dataclass(frozen=True)
class _Region:
    side: Side
    sha: str
    path: str
    source: _Source
    start: int
    end: int
    anchor: int


def _safe_path(path: str) -> bool:
    return bool(path) and not (
        path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    )


def _source(path: str, text: str) -> _Source:
    # Split on physical newlines, not str.splitlines' other Unicode separators.
    lines = tuple(re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]) if text else ()
    if not path.endswith((".py", ".pyi")):
        return _Source(lines, ())
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return _Source(lines, (), parse_failed=True)
    symbols = []
    pending: list[tuple[ast.AST, tuple[str, ...]]] = [(tree, ())]
    while pending:
        node, prefix = pending.pop()
        if isinstance(node, _DEFINITIONS):
            prefix = (*prefix, node.name)
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            if node.decorator_list:
                # In @(...), the expression can begin below the actual @ line.
                while start > 1 and not lines[start - 1].lstrip().startswith("@"):
                    start -= 1
            symbols.append(
                _Symbol(
                    prefix,
                    start,
                    node.end_lineno or node.lineno,
                    not isinstance(node, ast.ClassDef),
                )
            )
        pending.extend((child, prefix) for child in ast.iter_child_nodes(node))
    return _Source(lines, tuple(sorted(symbols, key=lambda item: (item.start, item.end))))


def _enclosing(source: _Source, line: int) -> _Symbol | None:
    functions = [
        symbol
        for symbol in source.symbols
        if symbol.is_function and symbol.start <= line <= symbol.end
    ]
    return min(functions, key=lambda item: (item.end - item.start, -item.start), default=None)


def _region(
    side: Side, sha: str, path: str, source: _Source, line: int, symbol: _Symbol | None
) -> _Region:
    if symbol is not None:
        return _Region(side, sha, path, source, symbol.start, symbol.end, line)
    return _Region(side, sha, path, source, 1, len(source.lines), line)


def _bounded_span(region: _Region, max_lines: int) -> tuple[int, int]:
    count = min(max_lines, region.end - region.start + 1)
    start = max(region.start, min(region.anchor - count // 2, region.end - count + 1))
    return start, start + count - 1


def _size(region: _Region, max_lines: int) -> int:
    start, end = _bounded_span(region, max_lines)
    return len("".join(region.source.lines[start - 1 : end]).encode("utf-8"))


def _snippet(region: _Region, max_bytes: int, max_lines: int) -> EvidenceSnippet | None:
    start, end = _bounded_span(region, max_lines)
    lengths = {
        line: len(region.source.lines[line - 1].encode("utf-8")) for line in range(start, end + 1)
    }
    size = sum(lengths.values())
    while size > max_bytes and start < end:
        if region.anchor - start > end - region.anchor:
            size -= lengths[start]
            start += 1
        else:
            size -= lengths[end]
            end -= 1
    if size > max_bytes:
        return None
    text = "".join(region.source.lines[start - 1 : end])
    payload = json.dumps(
        [region.side, region.sha, region.path, start, end, text], ensure_ascii=False
    ).encode("utf-8")
    source_id = f"verify:{region.side}:{hashlib.sha256(payload).hexdigest()}"
    return EvidenceSnippet(
        source_id,
        region.side,
        region.path,
        start,
        end,
        text,
        truncated=(start, end) != (region.start, region.end),
    )


def build_candidate_context(
    candidate: CandidateFinding,
    *,
    base_sources: Mapping[str, str],
    head_sources: Mapping[str, str],
    base_sha: str,
    head_sha: str,
    previous_path: str | None = None,
    max_bytes: int = 24_000,
    max_lines: int = 160,
    related_paths: Sequence[str] = (),
    base_absent: bool = False,
) -> CandidateContext:
    """Select the target function, its same-qualified-name base, and up to two helpers.

    Line/byte limits can clip a function, but always announce that loss. If no
    Python function encloses the target, a line-centred window is used instead.
    A missing/ambiguous base symbol is not replaced by an unrelated function at
    the same line number. Related paths must already be allowlisted by the caller.
    """
    if max_bytes <= 0 or max_lines <= 0:
        raise ValueError("Context byte and line limits must be positive")
    path = candidate.file_path
    if not _safe_path(path):
        return CandidateContext((), (f"unsafe_head_path: {path!r}",))
    if path not in head_sources:
        return CandidateContext((), (f"missing_head_source: {path}",))
    head = _source(path, head_sources[path])
    if not 1 <= candidate.line <= len(head.lines):
        return CandidateContext((), (f"head_line_out_of_range: {path}:{candidate.line}",))

    issues: list[str] = []
    if head.parse_failed:
        issues.append(f"head_parse_failed: {path}; using a bounded window")
    symbol = _enclosing(head, candidate.line)
    primary = [_region("head", head_sha, path, head, candidate.line, symbol)]
    old_path = previous_path if previous_path is not None else path
    if not base_absent:
        if not _safe_path(old_path):
            issues.append(f"unsafe_base_path: {old_path!r}")
        elif old_path not in base_sources or not base_sources[old_path]:
            issues.append(f"missing_base_source: {old_path}")
        else:
            base = _source(old_path, base_sources[old_path])
            counterparts = [
                item
                for item in base.symbols
                if symbol is not None
                and item.is_function
                and item.qualified_name == symbol.qualified_name
            ]
            if base.parse_failed:
                issues.append(f"base_parse_failed: {old_path}; using a bounded window")
            if symbol is not None and not base.parse_failed and len(counterparts) != 1:
                kind = "missing" if not counterparts else "ambiguous"
                issues.append(f"base_symbol_{kind}: {old_path}:{'.'.join(symbol.qualified_name)}")
            else:
                counterpart = counterparts[0] if counterparts else None
                anchor = min(candidate.line, len(base.lines))
                if counterpart is not None and symbol is not None:
                    anchor = min(counterpart.end, counterpart.start + candidate.line - symbol.start)
                primary.append(_region("base", base_sha, old_path, base, anchor, counterpart))

    snippets: list[EvidenceSnippet] = []
    remaining = max_bytes
    truncated = False
    for index, region in enumerate(primary):
        budget = remaining
        if index == 0 and len(primary) == 2:
            # Reserve space for the base before spending anything on helpers.
            budget -= min(_size(primary[1], max_lines), max_bytes // 2)
        snippet = _snippet(region, budget, max_lines)
        if snippet is None:
            issues.append(f"{region.side}_line_exceeds_byte_budget: {region.path}:{region.anchor}")
            truncated = True
            continue
        snippets.append(snippet)
        remaining -= len(snippet.text.encode("utf-8"))
        truncated |= snippet.truncated

    identifiers = set(_IDENTIFIER.findall(f"{candidate.finding} {candidate.evidence}"))
    # A named call in the actual enclosing code outranks prose mentions such as
    # "serialize", which otherwise select unrelated same-named class methods.
    called_names: set[str] = set()
    try:
        primary_text = "".join(head.lines[primary[0].start - 1 : primary[0].end])
        tree = ast.parse(textwrap.dedent(primary_text))
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
    except (SyntaxError, ValueError, RecursionError):
        pass
    identifiers.update(called_names)
    helpers: list[tuple[str, _Symbol, _Source]] = []
    for related in sorted(set(related_paths)):
        if not _safe_path(related):
            issues.append(f"unsafe_related_path: {related!r}")
            continue
        if related not in head_sources:
            issues.append(f"missing_related_source: {related}")
            continue
        source = head if related == path else _source(related, head_sources[related])
        for helper in source.symbols:
            if not helper.is_function or helper.qualified_name[-1] not in identifiers:
                continue
            if (
                related == path
                and primary[0].start <= helper.start
                and helper.end <= primary[0].end
            ):
                continue
            helpers.append((related, helper, source))
    # Prefer direct calls and module-level definitions, then stable path/span.
    # This is a bounded heuristic, not import/call-graph resolution.
    helpers.sort(
        key=lambda item: (
            item[1].qualified_name[-1] not in called_names,
            len(item[1].qualified_name) != 1,
            item[0],
            item[1].start,
            item[1].end,
        )
    )
    for related, helper, source in helpers[:2]:
        region = _region("head", head_sha, related, source, helper.start, helper)
        snippet = _snippet(region, remaining, max_lines)
        if snippet is None:
            issues.append(f"helper_exceeds_byte_budget: {related}:{helper.start}")
            truncated = True
            continue
        snippets.append(snippet)
        remaining -= len(snippet.text.encode("utf-8"))
        truncated |= snippet.truncated
    return CandidateContext(tuple(snippets), tuple(issues), truncated)
