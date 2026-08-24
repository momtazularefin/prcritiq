"""Tests for source chunking."""

from __future__ import annotations

from prcritiq.chunking import (
    chunk_javascript,
    chunk_python,
    chunk_source,
    python_imports,
    tokenize_identifiers,
)

MODULE = '''"""Service module."""

import os
from app.models import Order

TIMEOUT = 30


@dataclass
class OrderService:
    """Handles orders."""

    def submit(self, order: Order) -> bool:
        validated = self.validate(order)
        return validated

    def validate(self, order: Order) -> bool:
        return order.total > 0


def helper_function(value):
    doubled = value * 2
    return doubled
'''


class TestPythonChunking:
    def test_classes_methods_and_functions_are_chunked(self) -> None:
        chunks = chunk_python("src/app/service.py", MODULE)
        symbols = {chunk.symbol: chunk.symbol_type for chunk in chunks}

        assert symbols["OrderService"] == "class"
        assert symbols["OrderService.submit"] == "method"
        assert symbols["helper_function"] == "function"

    def test_tiny_methods_stay_inside_their_class(self) -> None:
        """A two-line method should not compete with the class that contains it."""

        chunks = chunk_python("src/app/service.py", MODULE)
        symbols = {chunk.symbol for chunk in chunks}

        assert "OrderService.validate" not in symbols
        service = next(c for c in chunks if c.symbol == "OrderService")
        assert "def validate" in service.text

    def test_short_top_level_functions_are_still_chunked(self) -> None:
        """The folding rule applies to nested symbols only."""

        chunks = chunk_python("m.py", "def tiny():\n    return 1\n")

        assert [chunk.symbol for chunk in chunks] == ["tiny"]

    def test_module_level_code_is_kept(self) -> None:
        """Imports and constants are common review targets and must be indexed."""

        chunks = chunk_python("src/app/service.py", MODULE)
        module_chunks = [c for c in chunks if c.symbol_type == "module_level"]

        assert module_chunks
        assert any("TIMEOUT = 30" in chunk.text for chunk in module_chunks)

    def test_decorator_lines_belong_to_the_symbol(self) -> None:
        chunks = chunk_python("src/app/service.py", MODULE)
        service = next(c for c in chunks if c.symbol == "OrderService")

        assert "@dataclass" in service.text

    def test_line_ranges_match_the_source(self) -> None:
        lines = MODULE.splitlines()
        for chunk in chunk_python("src/app/service.py", MODULE):
            assert chunk.text == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])

    def test_unparsable_file_still_yields_a_chunk(self) -> None:
        """A syntax error in a PR is exactly when a reviewer wants to see the code."""

        chunks = chunk_python("broken.py", "def oops(:\n    pass\n")

        assert len(chunks) == 1
        assert chunks[0].symbol_type == "unparsed_module"

    def test_empty_file_yields_nothing(self) -> None:
        assert chunk_python("empty.py", "") == []

    def test_chunk_id_tracks_content(self) -> None:
        first = chunk_python("a.py", "def f():\n    return 1\n")[0]
        second = chunk_python("a.py", "def f():\n    return 2\n")[0]

        assert first.chunk_id != second.chunk_id
        assert first.content_hash != second.content_hash

    def test_covers_and_overlaps(self) -> None:
        chunk = chunk_python("a.py", "def f():\n    return 1\n")[0]

        assert chunk.covers(chunk.start_line)
        assert chunk.overlaps({chunk.end_line})
        assert not chunk.overlaps({chunk.end_line + 500})


class TestJavaScriptChunking:
    def test_top_level_declarations_are_chunked(self) -> None:
        source = (
            "import x from 'y';\n"
            "\n"
            "export function alpha() {\n  return 1;\n}\n"
            "\n"
            "class Beta {\n  run() {}\n}\n"
        )

        chunks = chunk_javascript("web/main.ts", source)
        symbols = {chunk.symbol for chunk in chunks}

        assert "alpha" in symbols
        assert "Beta" in symbols
        assert any(chunk.symbol_type == "module_level" for chunk in chunks)

    def test_file_without_declarations_is_one_chunk(self) -> None:
        chunks = chunk_javascript("web/config.js", "const a = 1;\n".replace("const a", "a"))

        assert len(chunks) == 1
        assert chunks[0].symbol_type == "module_level"


class TestChunkSource:
    def test_unsupported_language_is_not_chunked(self) -> None:
        assert chunk_source("README.md", "# Title\n") == []

    def test_python_and_javascript_dispatch(self) -> None:
        assert chunk_source("a.py", "x = 1\n")
        assert chunk_source("a.ts", "export function f() {}\n")


class TestImportsAndTokens:
    def test_imports_are_extracted(self) -> None:
        modules = python_imports(MODULE)

        assert "os" in modules
        assert "app.models" in modules

    def test_unparsable_source_has_no_imports(self) -> None:
        assert python_imports("def oops(:\n") == set()

    def test_tokenizer_splits_snake_and_camel_case(self) -> None:
        tokens = set(tokenize_identifiers("parse_repo_reference parseRepoURL"))

        assert {"parse_repo_reference", "parse", "repo", "reference"} <= tokens
        assert {"parserepourl", "url"} <= tokens
