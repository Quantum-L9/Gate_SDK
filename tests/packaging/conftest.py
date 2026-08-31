"""
Harness for proving the built distribution behaves like the source checkout.

Every test in this package runs against a wheel that was built from this
repository and installed into an isolated directory — never against
``src/``. The risk being caught is the one a repo-root ``pytest`` run cannot
see: a package that imports cleanly from the checkout but is broken once
distributed, because a module was omitted from the wheel, an export was
dropped, ``py.typed`` went missing, or a runtime dependency was never
declared.

The child interpreter is deliberately hostile to the checkout: it strips the
repository ``src`` directory from ``sys.path`` and drops any editable-install
finder, then asserts that the package it imported really does live under the
install directory. A harness that silently fell back to the source tree
would prove nothing, so that assertion is made in the child on every call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_SRC = REPO_ROOT / "src"
DISTRIBUTION_NAME = "constellation_node_sdk"

_RESULT_SENTINEL = "__INSTALLED_SDK_RESULT__"

_PRELUDE = f'''
import json, pathlib, sys

_INSTALL_DIR = pathlib.Path(sys.argv[1]).resolve()
_REPO_SRC = pathlib.Path(sys.argv[2]).resolve()

# An editable install of this same project would resolve the package back to
# the checkout through a .pth entry or a meta-path finder. Remove both.
sys.meta_path = [
    finder
    for finder in sys.meta_path
    if "__editable__" not in getattr(type(finder), "__module__", "")
    and "__editable__" not in getattr(finder, "__name__", "")
]
sys.path = [
    entry
    for entry in sys.path
    if entry and pathlib.Path(entry).resolve() != _REPO_SRC
]
sys.path.insert(0, str(_INSTALL_DIR))
for _name in [n for n in sys.modules if n == "{DISTRIBUTION_NAME}" or n.startswith("{DISTRIBUTION_NAME}.")]:
    del sys.modules[_name]

import {DISTRIBUTION_NAME} as _sdk

_resolved = pathlib.Path(_sdk.__file__).resolve()
if _INSTALL_DIR not in _resolved.parents:
    raise AssertionError(
        "harness fault: the source checkout shadowed the installed package "
        f"({{_resolved}} is not under {{_INSTALL_DIR}})"
    )

def emit(value):
    print("{_RESULT_SENTINEL}" + json.dumps(value))
'''


@dataclass(frozen=True)
class InstalledSdk:
    """A wheel built from this repository and installed in isolation."""

    wheel_path: Path
    install_dir: Path
    build_command: tuple[str, ...]
    work_dir: Path

    def run(self, code: str) -> dict[str, object]:
        """
        Execute ``code`` against the installed package and return what it emitted.

        ``code`` runs after the prelude, so ``emit(...)`` is available and the
        package has already been imported from the install directory.
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.install_dir)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONHOME", None)

        completed = subprocess.run(
            [sys.executable, "-c", _PRELUDE + code, str(self.install_dir), str(REPO_SRC)],
            capture_output=True,
            text=True,
            cwd=self.work_dir,
            env=env,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "installed-package subprocess failed\n"
                f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
            )

        emitted = [
            line[len(_RESULT_SENTINEL) :]
            for line in completed.stdout.splitlines()
            if line.startswith(_RESULT_SENTINEL)
        ]
        if not emitted:
            raise AssertionError(
                f"installed-package subprocess emitted no result\n--- stdout ---\n{completed.stdout}"
            )
        result = json.loads(emitted[-1])
        assert isinstance(result, dict)
        return result


_BUILD_EXCLUDES = (
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
)


def _export_repository(destination: Path) -> Path:
    """
    Copy the project into a clean directory and build from there.

    Building in place would leave ``build/`` and ``*.egg-info`` behind in the
    working tree, and — worse for this track — could produce a wheel from a
    stale artifact rather than from the declared package contents. Exporting
    first makes the build answer only to ``pyproject.toml`` and ``src/``.
    """
    shutil.copytree(
        REPO_ROOT,
        destination,
        ignore=shutil.ignore_patterns(*_BUILD_EXCLUDES, "*.egg-info", "*.pyc"),
    )
    return destination


def _importable(module: str) -> bool:
    return (
        subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _build_wheel(source_dir: Path, output_dir: Path) -> tuple[Path, tuple[str, ...]]:
    """
    Build a wheel with the repository's canonical build mechanism.

    ``python -m build`` is what CI gate-7 runs, so it is preferred. Where the
    ``build`` frontend is absent, pip's PEP 517 wheel builder drives the same
    ``setuptools.build_meta`` backend declared in ``pyproject.toml``. PEP 517
    isolation is skipped when setuptools is already importable, so the suite
    does not need network access to prove a packaging property; where it is
    not, the isolated build provisions the backend itself.
    """
    setuptools_available = _importable("setuptools")

    if _importable("build"):
        build_args = ["--wheel"]
        if setuptools_available:
            build_args.append("--no-isolation")
        command = (
            sys.executable,
            "-m",
            "build",
            *build_args,
            "--outdir",
            str(output_dir),
            str(source_dir),
        )
    else:
        pip_args = ["--no-deps"]
        if setuptools_available:
            pip_args.append("--no-build-isolation")
        command = (
            sys.executable,
            "-m",
            "pip",
            "wheel",
            *pip_args,
            "--wheel-dir",
            str(output_dir),
            str(source_dir),
        )

    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    if completed.returncode != 0:
        raise AssertionError(
            "wheel build failed\n"
            f"--- command ---\n{' '.join(command)}\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )

    wheels = sorted(output_dir.glob(f"{DISTRIBUTION_NAME}-*.whl"))
    if len(wheels) != 1:
        raise AssertionError(f"expected exactly one built wheel, found {[w.name for w in wheels]}")
    return wheels[0], command


@pytest.fixture(scope="session")
def installed_sdk(tmp_path_factory: pytest.TempPathFactory) -> InstalledSdk:
    """Build the wheel once per session and install it into a clean directory."""
    base = tmp_path_factory.mktemp("installed-sdk")
    dist_dir = base / "dist"
    install_dir = base / "site"
    work_dir = base / "cwd"
    dist_dir.mkdir()
    install_dir.mkdir()
    work_dir.mkdir()

    export_dir = _export_repository(base / "export")
    wheel_path, build_command = _build_wheel(export_dir, dist_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-compile",
            "--target",
            str(install_dir),
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "clean install of the built wheel failed\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )

    return InstalledSdk(
        wheel_path=wheel_path,
        install_dir=install_dir,
        build_command=build_command,
        work_dir=work_dir,
    )
