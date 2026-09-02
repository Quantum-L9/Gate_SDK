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


def test_release_identity_validator_passes_without_release_objects(tmp_path: Path) -> None:
    """Shallow clones omit the release tag and historical SHA — still PASS."""
    work = tmp_path / "shallow"
    work.mkdir()
    shutil.copy2(REPO / "pyproject.toml", work / "pyproject.toml")
    contracts = work / "contracts"
    contracts.mkdir()
    shutil.copy2(LEDGER, contracts / "RELEASE_IDENTITY_LEDGER.json")
    ledger = json.loads(LEDGER.read_text())
    assert ledger["release_commit_sha"] != "0" * 40

    subprocess.run([GIT, "init", "-q"], cwd=work, check=True, capture_output=True)
    subprocess.run([GIT, "config", "user.email", "ci@example.com"], cwd=work, check=True)
    subprocess.run([GIT, "config", "user.name", "ci"], cwd=work, check=True)
    subprocess.run([GIT, "add", "pyproject.toml", "contracts"], cwd=work, check=True)
    subprocess.run([GIT, "commit", "-qm", "shallow head"], cwd=work, check=True)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(work), "--ledger", str(contracts / "RELEASE_IDENTITY_LEDGER.json")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
