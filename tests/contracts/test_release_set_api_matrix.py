"""
Executable compatibility matrix for the coordinated release set.

``release_set_api_matrix.json`` transcribes the Gate_SDK surface that
IB-Odoo_19, Constellation.Gate, and Enrichment.Inference.Engine import at
named call sites. These tests resolve every entry against the SDK, so a
symbol that disappears — or a keyword parameter that is renamed — fails
here, in this repository, instead of inside an application repository days
later.

No consuming repository is imported, installed, or contacted. The matrix is
a transcription of their call sites, not a link to them.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

_MATRIX_PATH = Path(__file__).resolve().parent / "release_set_api_matrix.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]

MATRIX: dict[str, Any] = json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))
CONSUMERS: dict[str, Any] = MATRIX["consumers"]


def _resolve(module_name: str, dotted_symbol: str) -> Any:
    """Resolve ``Symbol`` or ``Class.member`` out of an SDK module."""
    obj: Any = importlib.import_module(module_name)
    for part in dotted_symbol.split("."):
        obj = getattr(obj, part)
    return obj


def _keyword_parameters(func: Any) -> set[str]:
    signature = inspect.signature(func)
    return {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind
        in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    }


def _cases(section: str) -> list[Any]:
    return [
        pytest.param(consumer, entry, id=f"{consumer}-{entry.get('symbol', entry.get('module'))}")
        for consumer, spec in CONSUMERS.items()
        for entry in spec.get(section, [])
    ]


def test_matrix_covers_every_release_set_consumer() -> None:
    """The matrix is not silently emptied of a consumer."""
    assert set(CONSUMERS) == {"odoo", "gate", "eie"}
    for consumer, spec in CONSUMERS.items():
        assert spec["imports"], f"{consumer} declares no imports"
        assert spec["call_sites"], f"{consumer} declares no call sites"
        assert spec["behaviors"], f"{consumer} declares no required behaviors"


@pytest.mark.parametrize(("consumer", "entry"), _cases("imports"))
def test_consumed_import_path_still_resolves(consumer: str, entry: dict[str, Any]) -> None:
    """Every module path a consumer imports from still exists and exports its symbols."""
    module = importlib.import_module(entry["module"])
    missing = [symbol for symbol in entry["symbols"] if not hasattr(module, symbol)]
    assert not missing, (
        f"{consumer} imports {missing} from {entry['module']}; the SDK no longer exports them"
    )


@pytest.mark.parametrize(("consumer", "entry"), _cases("signatures"))
def test_consumed_callable_keeps_its_keyword_parameters(
    consumer: str, entry: dict[str, Any]
) -> None:
    """A consumer's call-site keywords still bind to real parameters."""
    target = _resolve(entry["module"], entry["symbol"])
    available = _keyword_parameters(target)
    missing = [name for name in entry["keyword_parameters"] if name not in available]
    assert not missing, (
        f"{consumer} calls {entry['symbol']} with {missing}, which the signature no longer accepts"
    )


@pytest.mark.parametrize(("consumer", "entry"), _cases("signatures"))
def test_observed_call_site_keywords_are_a_subset_of_the_locked_signature(
    consumer: str, entry: dict[str, Any]
) -> None:
    """
    Keep the matrix honest.

    ``observed_call_site_keywords`` records what the consumer passes today;
    ``keyword_parameters`` is the wider surface the coordinated release
    depends on. The first must never drift outside the second.
    """
    observed = set(entry.get("observed_call_site_keywords", ()))
    locked = set(entry["keyword_parameters"])
    assert observed <= locked, (
        f"{consumer} matrix entry for {entry['symbol']} observes {sorted(observed - locked)} "
        "outside the locked signature"
    )


@pytest.mark.parametrize(("consumer", "entry"), _cases("members"))
def test_consumed_class_keeps_its_members(consumer: str, entry: dict[str, Any]) -> None:
    """Attributes and methods a consumer reaches for are still present."""
    target = _resolve(entry["module"], entry["symbol"])
    missing = [name for name in entry["attributes"] if not hasattr(target, name)]
    assert not missing, f"{consumer} uses {entry['symbol']}.{missing} which no longer exists"


@pytest.mark.parametrize(("consumer", "entry"), _cases("model_fields"))
def test_consumed_model_keeps_its_fields(consumer: str, entry: dict[str, Any]) -> None:
    """Pydantic model fields a consumer sets or reads are still declared."""
    model = _resolve(entry["module"], entry["symbol"])
    declared = set(model.model_fields)
    missing = [name for name in entry["fields"] if name not in declared]
    assert not missing, f"{consumer} relies on {entry['symbol']} fields {missing}, now absent"


def test_every_declared_behavior_names_a_proof() -> None:
    """A behavior a consumer depends on must point at a test, not at prose."""
    proofs = MATRIX["behavior_proofs"]
    for consumer, spec in CONSUMERS.items():
        for behavior in spec["behaviors"]:
            assert behavior in proofs, f"{consumer} behavior {behavior!r} names no proof"


def test_every_named_proof_exists_in_the_suite() -> None:
    """
    The proof pointers are checked, not trusted.

    A renamed or deleted test breaks this, which is the point: the matrix
    must not decay into a list of tests that used to exist.
    """
    for behavior, node_id in MATRIX["behavior_proofs"].items():
        relative_path, _, test_name = node_id.partition("::")
        path = _REPO_ROOT / relative_path
        assert path.is_file(), f"proof for {behavior!r} points at missing file {relative_path}"
        source = path.read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, (
            f"proof for {behavior!r} names {test_name}, absent from {relative_path}"
        )
