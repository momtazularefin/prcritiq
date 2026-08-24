"""Split source files into retrievable chunks.

Python uses the standard library `ast`, so chunk boundaries follow real symbol
definitions rather than a guess. JavaScript and TypeScript use deliberately
shallow heuristics: v1 reviews Python first (ADR-004), and a hand-rolled JS
parser would cost more than the retrieval quality it buys.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Final

from .diff import detect_language

#: Chunks below this many lines are folded into their parent rather than indexed
#: separately, so a one-line getter does not compete with the class it lives in.
_MIN_STANDALONE_LINES: Final = 3

_JS_SYMBOL: Final = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?"
    r"(?P<kind>class|function|const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
)

_IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class SourceChunk:
    """One retrievable region of a source file."""

    chunk_id: str
    path: str
    language: str
    symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    text: str
    content_hash: str

    def covers(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    def overlaps(self, lines: frozenset[int] | set[int]) -> bool:
        return any(self.covers(line) for line in lines)


def _build_chunk(
    *,
    path: str,
    language: str,
    symbol: str,
    symbol_type: str,
    start_line: int,
    end_line: int,
    text: str,
) -> SourceChunk:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return SourceChunk(
        chunk_id=f"{path}:{start_line}-{end_line}:{content_hash[:8]}",
        path=path,
        language=language,
        symbol=symbol,
        symbol_type=symbol_type,
        start_line=start_line,
        end_line=end_line,
        text=text,
        content_hash=content_hash,
    )


def _slice(lines: list[str], start_line: int, end_line: int) -> str:
    return "\n".join(lines[start_line - 1 : end_line])


def chunk_python(path: str, source: str) -> list[SourceChunk]:
    """Chunk a Python file by its top-level and nested symbol definitions.

    A file that does not parse yields a single whole-file chunk rather than
    nothing, because a syntax error in a pull request is exactly when a reviewer
    still wants to see the code.
    """

    lines = source.splitlines()
    if not lines:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [
            _build_chunk(
                path=path,
                language="python",
                symbol=path.rsplit("/", 1)[-1],
                symbol_type="unparsed_module",
                start_line=1,
                end_line=len(lines),
                text=source,
            )
        ]

    chunks: list[SourceChunk] = []
    claimed: set[int] = set()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            start = min([child.lineno] + [decorator.lineno for decorator in child.decorator_list])
            end = child.end_lineno or start
            qualified = f"{prefix}.{child.name}" if prefix else child.name
            symbol_type = "class" if isinstance(child, ast.ClassDef) else "function"

            if isinstance(child, ast.ClassDef):
                # Index the class as a whole, then its methods, so a query can
                # match either the type or the specific behavior.
                chunks.append(
                    _build_chunk(
                        path=path,
                        language="python",
                        symbol=qualified,
                        symbol_type=symbol_type,
                        start_line=start,
                        end_line=end,
                        text=_slice(lines, start, end),
                    )
                )
                claimed.update(range(start, end + 1))
                visit(child, qualified)
                continue

            if end - start + 1 < _MIN_STANDALONE_LINES and prefix:
                continue
            chunks.append(
                _build_chunk(
                    path=path,
                    language="python",
                    symbol=qualified,
                    symbol_type="method" if prefix else symbol_type,
                    start_line=start,
                    end_line=end,
                    text=_slice(lines, start, end),
                )
            )
            claimed.update(range(start, end + 1))

    visit(tree, "")

    # Module-level code outside any definition still matters: imports, constants,
    # and configuration are common review targets.
    remaining = [number for number in range(1, len(lines) + 1) if number not in claimed]
    for start, end in _contiguous_ranges(remaining):
        if not _slice(lines, start, end).strip():
            continue
        chunks.append(
            _build_chunk(
                path=path,
                language="python",
                symbol=path.rsplit("/", 1)[-1],
                symbol_type="module_level",
                start_line=start,
                end_line=end,
                text=_slice(lines, start, end),
            )
        )

    return sorted(chunks, key=lambda chunk: (chunk.start_line, chunk.end_line))


def _contiguous_ranges(numbers: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for number in numbers:
        if start is None:
            start = previous = number
            continue
        if previous is not None and number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous or start))
        start = previous = number
    if start is not None:
        ranges.append((start, previous or start))
    return ranges


def chunk_javascript(path: str, source: str) -> list[SourceChunk]:
    """Chunk a JS/TS file at top-level declaration boundaries."""

    lines = source.splitlines()
    if not lines:
        return []

    language = detect_language(path)
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        found = _JS_SYMBOL.match(line)
        if found:
            starts.append((index, found.group("name"), found.group("kind")))

    if not starts:
        return [
            _build_chunk(
                path=path,
                language=language,
                symbol=path.rsplit("/", 1)[-1],
                symbol_type="module_level",
                start_line=1,
                end_line=len(lines),
                text=source,
            )
        ]

    chunks: list[SourceChunk] = []
    if starts[0][0] > 1:
        chunks.append(
            _build_chunk(
                path=path,
                language=language,
                symbol=path.rsplit("/", 1)[-1],
                symbol_type="module_level",
                start_line=1,
                end_line=starts[0][0] - 1,
                text=_slice(lines, 1, starts[0][0] - 1),
            )
        )

    for position, (start, name, kind) in enumerate(starts):
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(lines)
        chunks.append(
            _build_chunk(
                path=path,
                language=language,
                symbol=name,
                symbol_type="class" if kind == "class" else "function",
                start_line=start,
                end_line=end,
                text=_slice(lines, start, end),
            )
        )
    return chunks


def chunk_source(path: str, source: str) -> list[SourceChunk]:
    """Chunk a source file according to its language."""

    language = detect_language(path)
    if language == "python":
        return chunk_python(path, source)
    if language in {"javascript", "typescript"}:
        return chunk_javascript(path, source)
    return []


def python_imports(source: str) -> set[str]:
    """Return the module names a Python file imports, dotted roots included."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def tokenize_identifiers(text: str) -> list[str]:
    """Split text into lowercase identifier tokens, including sub-words.

    `parse_repo_reference` contributes `parse`, `repo`, `reference` and the whole
    identifier, so a query matches whether it names the function or a concept
    inside it.
    """

    tokens: list[str] = []
    for match in _IDENTIFIER.finditer(text):
        identifier = match.group(0)
        lowered = identifier.lower()
        tokens.append(lowered)
        parts = [part for part in identifier.split("_") if part]
        for part in parts:
            for camel in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", part):
                lowered_part = camel.lower()
                if lowered_part != lowered:
                    tokens.append(lowered_part)
    return tokens
