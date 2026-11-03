"""The dependency-lock gate must discriminate, not just pass (issue #53).

``scripts/validate_locks.py`` is what keeps every CI install hash-locked after
the S8541/S8544 fix: a lock that drifts from ``pyproject.toml``, two locks that
pin one package differently inside one environment, or a workflow that goes
back to ``pip install -e ".[dev]"`` must each fail. A validator that passes the
real tree proves nothing on its own, so these tests drive every failure side
through the pure functions and a fixture tree. No test touches the network.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_locks.py"
HASH = "--hash=sha256:" + "a" * 64


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_locks", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{SCRIPT} did not load")
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_locks"] = module
    spec.loader.exec_module(module)
    return module


V = _load()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the real lock surface: pyproject, requirements/, workflows."""
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(REPO / "requirements", tmp_path / "requirements")
    shutil.copytree(REPO / ".github" / "workflows", tmp_path / ".github" / "workflows")
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture drift: {old!r} not in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _pin(lock: Path, name: str) -> str:
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}=="):
            return line.split()[0]
    raise AssertionError(f"{name} not in {lock.name}")


# ------------------------------------------------------------------ real tree


def test_the_real_tree_passes() -> None:
    assert V.check_tree(REPO) == []


def test_every_lock_in_the_registry_is_committed() -> None:
    for spec in V.LOCKS:
        assert (REPO / spec.path).is_file(), spec.path


# --------------------------------------------------------------- lock format


def test_a_hashed_exact_pin_parses() -> None:
    pins, errors = V.lock_pins("x.lock", f"Types_PyYAML==6.0.1 {HASH} {HASH}\n")
    assert errors == []
    assert pins == {"types-pyyaml": "6.0.1"}


@pytest.mark.parametrize(
    "line",
    [
        "fastapi==0.162.0",  # no hash: --require-hashes would refuse it
        f"fastapi>=0.115 {HASH}",  # a range is not a lock
        f"fastapi @ https://example.invalid/f.whl {HASH}",  # direct URL
        f"fastapi==0.162.0 --hash=sha256:{'a' * 63}",  # truncated digest
    ],
)
def test_a_line_that_is_not_a_hashed_exact_pin_fails(line: str) -> None:
    _, errors = V.lock_pins("x.lock", line + "\n")
    assert any("not an exact '==' pin" in item for item in errors), errors


def test_an_empty_lock_fails() -> None:
    _, errors = V.lock_pins("x.lock", "# header only\n")
    assert errors == ["x.lock: lock is empty"]


def test_a_missing_lock_fails(tree: Path) -> None:
    (tree / "requirements" / "audit.lock").unlink()
    errors = V.check_tree(tree)
    assert any("requirements/audit.lock: lock is missing" in e for e in errors), errors


# ----------------------------------------------- one environment, one version


def test_a_tool_lock_that_disagrees_with_its_base_fails(tree: Path) -> None:
    lock = tree / "requirements" / "audit.lock"
    pin = _pin(lock, "packaging")
    _edit(lock, pin, "packaging==1.0")
    errors = V.check_tree(tree)
    assert any("audit.lock: packaging==1.0 but requirements/dev.lock pins" in e for e in errors), (
        errors
    )


def test_a_dev_lock_missing_a_runtime_package_fails(tree: Path) -> None:
    lock = tree / "requirements" / "dev.lock"
    line = next(
        raw for raw in lock.read_text(encoding="utf-8").splitlines() if raw.startswith("fastapi==")
    )
    _edit(lock, line + "\n", "")
    errors = V.check_tree(tree)
    assert any("dev.lock: missing fastapi==" in e for e in errors), errors


# -------------------------------------------------------- lock freshness


def test_a_declared_dependency_missing_from_its_lock_fails(tree: Path) -> None:
    _edit(tree / "pyproject.toml", '  "pyyaml>=6.0.2",\n', '  "pyyaml>=6.0.2",\n  "attrs>=1",\n')
    errors = V.check_tree(tree)
    assert any("runtime.lock: attrs>=1 is declared but not locked" in e for e in errors), errors
    assert any("dev.lock: attrs>=1 is declared but not locked" in e for e in errors), errors


def test_a_lock_that_no_longer_satisfies_its_input_fails(tree: Path) -> None:
    _edit(tree / "requirements" / "precommit.in", "pre-commit==3.7.1", "pre-commit==9.9.9")
    errors = V.check_tree(tree)
    assert any(
        "precommit.lock: locks pre-commit==3.7.1, which does not satisfy" in e for e in errors
    ), errors


# ------------------------------------------------------------ workflow guard


@pytest.mark.parametrize(
    "line",
    [
        "python -m pip install --upgrade pip",
        'python -m pip install -e ".[dev]"',
        "python -m pip install pip-audit",
        # Hash-locked but missing --only-binary :all: (S8541).
        "python -m pip install --require-hashes -r requirements/dev.lock",
        # --only-binary but not hash-locked (S8544).
        'python -m pip install --only-binary :all: -e ".[dev]"',
    ],
)
def test_an_unlocked_workflow_install_fails(line: str) -> None:
    errors = V.check_workflow_line("ci.yml", 7, f"          {line}")
    assert len(errors) == 1, errors
    assert "unlocked pip install" in errors[0], errors


@pytest.mark.parametrize(
    "line",
    [
        "python -m pip install --require-hashes --only-binary :all: -r requirements/dev.lock",
        "python -m pip install --no-index --no-deps --no-build-isolation -e .",
        "python -m pip install --no-index --no-deps dist/*.whl",
        '# a comment that mentions pip install -e ".[dev]"',
    ],
)
def test_a_locked_or_offline_install_passes(line: str) -> None:
    assert V.check_workflow_line("ci.yml", 7, f"          {line}") == []


def test_a_workflow_regression_is_caught_in_the_tree(tree: Path) -> None:
    workflow = tree / ".github" / "workflows" / "integration.yml"
    _edit(
        workflow,
        "python -m pip install --require-hashes --only-binary :all: -r requirements/dev.lock",
        'python -m pip install -e ".[dev]"',
    )
    errors = V.check_tree(tree)
    assert any(e.startswith(".github/workflows/integration.yml:") for e in errors), errors


def test_a_workflow_installing_an_unknown_lock_fails(tree: Path) -> None:
    workflow = tree / ".github" / "workflows" / "integration.yml"
    _edit(workflow, "-r requirements/dev.lock", "-r requirements/nosuch.lock")
    errors = V.check_tree(tree)
    assert any("installs missing lock requirements/nosuch.lock" in e for e in errors), errors
