"""Release identity ledger must agree with tag + package version."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_release_identity.py"
LEDGER = REPO / "contracts" / "RELEASE_IDENTITY_LEDGER.json"


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
