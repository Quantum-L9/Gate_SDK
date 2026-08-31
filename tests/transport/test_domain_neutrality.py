"""
SDK5 / SDK18 / SDK19 — the SDK source carries no application-domain vocabulary.

Transport is domain-neutral by construction, not by convention. This test walks
the authoritative package's abstract syntax trees and fails if an application's
vocabulary — Odoo model names, enrichment field spellings, provider names —
appears in an identifier or a string literal.

Comments and docstrings are deliberately out of scope: prose may name a
downstream node to explain why a transport decision exists (``hashing.py``
records which integrity checks a timezone bug once broke). Executable code and
the string constants it acts on may not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "constellation_node_sdk"

# Each pattern names something an application repository owns. If one of these
# turns up in SDK code, transport has started translating a domain contract.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bodoo\b", "Odoo is an application node; transport must not know it"),
    (r"\beie\b", "EIE is an application node; transport must not know it"),
    (r"res\.partner", "Odoo model names are domain data, not transport data"),
    (r"entity_snapshot", "an application's field spelling, never a transport field"),
    (r"final_fields", "an application's field spelling, never a transport field"),
    (r"writeback", "writeback is an application concern"),
    (r"perplexity", "provider names belong to the application node"),
    (r"enrich_?request", "the enrichment domain contract is not transport"),
    (r"enrich_?response", "the enrichment domain contract is not transport"),
)


def _source_modules() -> list[Path]:
    return sorted(path for path in SRC_ROOT.rglob("*.py"))


def _strip_docstrings(tree: ast.AST) -> None:
    """Drop docstring expressions in place so prose is not scanned as code."""
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:]


def _executable_text(path: Path) -> list[str]:
    """Return every identifier and non-docstring string literal in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    _strip_docstrings(tree)

    text: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            text.append(node.id)
        elif isinstance(node, ast.Attribute):
            text.append(node.attr)
        elif isinstance(node, ast.arg):
            text.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text.append(node.name)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            text.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text.append(node.value)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                text.append(node.module)
            text.extend(alias.name for alias in node.names)
    return text


def test_source_tree_is_discoverable() -> None:
    """Guard the guard: an empty scan must not read as a pass."""
    modules = _source_modules()
    assert len(modules) > 20, f"expected the SDK package, found {len(modules)} modules"


@pytest.mark.parametrize("module_path", _source_modules(), ids=lambda p: p.name)
def test_module_contains_no_application_domain_vocabulary(module_path: Path) -> None:
    fragments = _executable_text(module_path)

    for pattern, reason in FORBIDDEN_PATTERNS:
        matcher = re.compile(pattern, re.IGNORECASE)
        offenders = [fragment for fragment in fragments if matcher.search(fragment)]
        assert offenders == [], (
            f"{module_path.relative_to(SRC_ROOT)} references {pattern!r} "
            f"in {offenders!r} — {reason}"
        )
