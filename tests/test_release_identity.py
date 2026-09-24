"""Release identity ledger must agree with tag + package version."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_release_identity.py"
LEDGER = REPO / "contracts" / "RELEASE_IDENTITY_LEDGER.json"
GIT = "/usr/bin/git"


def test_release_identity_ledger_exists() -> None:
    assert LEDGER.is_file()
    assert SCRIPT.is_file()


def test_release_identity_validator_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO), "--ledger", str(LEDGER)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout


def _seed_head_only_repo(root: Path) -> Path:
    """Repo whose HEAD tree matches the ledger but lacks the release objects."""
    root.mkdir()
    shutil.copy2(REPO / "pyproject.toml", root / "pyproject.toml")
    contracts = root / "contracts"
    contracts.mkdir()
    shutil.copy2(LEDGER, contracts / "RELEASE_IDENTITY_LEDGER.json")
    ledger = json.loads(LEDGER.read_text())
    assert ledger["release_commit_sha"] != "0" * 40
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run([GIT, "config", "user.email", "ci@example.com"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.name", "ci"], cwd=root, check=True)
    subprocess.run([GIT, "add", "pyproject.toml", "contracts"], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "head without release objects"], cwd=root, check=True)
    (root / "extra.txt").write_text("second commit so a depth-1 clone is shallow\n")
    subprocess.run([GIT, "add", "extra.txt"], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "second commit"], cwd=root, check=True)
    return contracts / "RELEASE_IDENTITY_LEDGER.json"


def test_release_identity_validator_fails_without_release_objects_on_non_shallow(
    tmp_path: Path,
) -> None:
    """A complete clone must FAIL when the tag and release commit are absent."""
    work = tmp_path / "complete"
    ledger = _seed_head_only_repo(work)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(work), "--ledger", str(ledger)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "FAIL" in completed.stdout
    assert "non-shallow" in completed.stdout


def test_release_identity_validator_passes_on_shallow_clone(tmp_path: Path) -> None:
    """A real shallow clone may skip object checks and must say so."""
    origin = tmp_path / "origin"
    _seed_head_only_repo(origin)
    work = tmp_path / "shallow"
    subprocess.run(
        [GIT, "clone", "--no-local", "--depth", "1", str(origin), str(work)],
        check=True,
        capture_output=True,
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(work),
            "--ledger",
            str(work / "contracts" / "RELEASE_IDENTITY_LEDGER.json"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "INFO: skipped tag and release-commit object checks (shallow clone)" in completed.stdout
    assert "PASS" in completed.stdout
