"""Release identity is version-governed: the ledger must agree with the tags.

The consumer compatibility contract is the moving major channel ``v1``, not a
commit sha. These tests prove the validator derives the exact release tag and
the channel from the package version, requires them to name one object, and
rejects any ledger that reintroduces consumer-sha policy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_release_identity.py"
LEDGER = REPO / "contracts" / "RELEASE_IDENTITY_LEDGER.json"
GIT = "/usr/bin/git"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True)


# ---------------------------------------------------------------- structural


def test_release_identity_ledger_exists() -> None:
    assert LEDGER.is_file()
    assert SCRIPT.is_file()


def test_ledger_is_schema_v2_and_declares_a_moving_channel() -> None:
    ledger = json.loads(LEDGER.read_text())
    assert ledger["schema"] == "l9.gate_sdk.release_identity_ledger.v2"
    assert ledger["consumer_contract"]["mode"] == "moving_major_tag"
    assert ledger["consumer_contract"]["required_ref"] == ledger["compatibility_channel"]


def test_ledger_carries_no_consumer_sha_policy() -> None:
    """A resolved sha may be provenance; it must never be consumer policy."""
    ledger = json.loads(LEDGER.read_text())
    assert "consumer_pin" not in ledger
    assert "release_commit_sha" not in ledger
    assert ledger["provenance"]["authority"] == "none"


def test_exact_release_tag_derives_from_package_version() -> None:
    from_module = _load_validator()
    assert from_module.derive_expectations("1.1.0") == ("v1.1.0", "v1")
    assert from_module.derive_expectations("2.4.9") == ("v2.4.9", "v2")


def test_release_identity_validator_passes() -> None:
    completed = _run("--repo", str(REPO), "--ledger", str(LEDGER))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout


# ------------------------------------------------------------ falsification


def _load_validator():  # type: ignore[no-untyped-def]
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_release_identity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_channel_that_disagrees_with_package_major_fails(tmp_path: Path) -> None:
    """Probe 1 of the runbook: channel v2 while the package is 1.1.0."""
    work = _seed_release_repo(tmp_path / "wrong-channel", compatibility_channel="v2")
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert "compatibility_channel must be 'v1'" in completed.stdout


def test_channel_pointing_at_a_different_object_fails(tmp_path: Path) -> None:
    """Probe 2: v1 and v1.1.0 resolve to different objects."""
    work = _seed_release_repo(tmp_path / "split", channel_advances=True)
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert "compatibility channel" in completed.stdout


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"consumer_pin": {"sha": "a" * 40}}, "retired consumer-sha policy"),
        ({"release_commit_sha": "b" * 40}, "retired consumer-sha policy"),
        ({"schema": "l9.gate_sdk.release_identity_ledger.v1"}, "schema must be"),
        ({"release_tag_object": None}, "schema v1 field with no meaning"),
    ],
)
def test_reintroducing_consumer_sha_policy_fails(
    tmp_path: Path, mutation: dict[str, object], expected: str
) -> None:
    """Probe 3: the retired v1 policy must not pass under the v2 validator."""
    work = _seed_release_repo(tmp_path / "regressed", ledger_overrides=mutation)
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert expected in completed.stdout


def test_required_ref_must_be_the_channel_not_the_exact_tag(tmp_path: Path) -> None:
    """Consumers declare the channel; pinning them to v1.1.0 is not the contract."""
    work = _seed_release_repo(
        tmp_path / "exact-consumer",
        ledger_overrides={
            "consumer_contract": {
                "mode": "moving_major_tag",
                "required_ref": "v1.1.0",
                "forbid_ref_kinds": ["branch", "commit_sha", "fork"],
            }
        },
    )
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert "required_ref" in completed.stdout


def test_a_sha_inside_the_consumer_contract_fails(tmp_path: Path) -> None:
    work = _seed_release_repo(
        tmp_path / "sha-in-contract",
        ledger_overrides={
            "consumer_contract": {
                "mode": "moving_major_tag",
                "required_ref": "v1",
                "forbid_ref_kinds": ["branch", "commit_sha", "fork"],
                "expected_object": "c" * 40,
            }
        },
    )
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert "must not contain a 40-character commit sha" in completed.stdout


def test_package_version_at_the_release_tag_must_match_the_ledger(tmp_path: Path) -> None:
    work = _seed_release_repo(tmp_path / "version-drift", tagged_version="1.0.0")
    completed = _run("--repo", str(work), "--ledger", str(work / "contracts" / "L.json"))
    assert completed.returncode != 0, completed.stdout
    assert "package_version at v1.1.0" in completed.stdout


# ----------------------------------------------------------- clone topology


def _seed_head_only_repo(root: Path) -> Path:
    """Repo whose HEAD tree matches the ledger but carries no release tags."""
    root.mkdir(parents=True)
    shutil.copy2(REPO / "pyproject.toml", root / "pyproject.toml")
    contracts = root / "contracts"
    contracts.mkdir()
    shutil.copy2(LEDGER, contracts / "RELEASE_IDENTITY_LEDGER.json")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    _git(root, "add", "pyproject.toml", "contracts")
    _git(root, "commit", "-qm", "head without release tags")
    (root / "extra.txt").write_text("second commit so a depth-1 clone is shallow\n")
    _git(root, "add", "extra.txt")
    _git(root, "commit", "-qm", "second commit")
    return contracts / "RELEASE_IDENTITY_LEDGER.json"


def test_missing_tags_fail_on_a_non_shallow_clone(tmp_path: Path) -> None:
    """A complete clone must FAIL when the release tag and channel are absent."""
    work = tmp_path / "complete"
    ledger = _seed_head_only_repo(work)

    completed = _run("--repo", str(work), "--ledger", str(ledger))
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "FAIL" in completed.stdout
    assert "non-shallow" in completed.stdout


def test_shallow_clone_skips_object_checks_and_says_so(tmp_path: Path) -> None:
    """A real shallow clone may skip object checks — and must name the exception."""
    origin = tmp_path / "origin"
    _seed_head_only_repo(origin)
    work = tmp_path / "shallow"
    subprocess.run(
        [GIT, "clone", "--no-local", "--depth", "1", str(origin), str(work)],
        check=True,
        capture_output=True,
    )

    completed = _run(
        "--repo",
        str(work),
        "--ledger",
        str(work / "contracts" / "RELEASE_IDENTITY_LEDGER.json"),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "INFO: skipped tag object checks (shallow clone)" in completed.stdout
    assert "PASS" in completed.stdout


def test_shallow_exception_is_not_accepted_in_required_networked_mode(tmp_path: Path) -> None:
    """The shallow escape hatch must not survive into release acceptance."""
    origin = tmp_path / "origin"
    _seed_head_only_repo(origin)
    work = tmp_path / "shallow"
    subprocess.run(
        [GIT, "clone", "--no-local", "--depth", "1", str(origin), str(work)],
        check=True,
        capture_output=True,
    )

    completed = _run(
        "--repo",
        str(work),
        "--ledger",
        str(work / "contracts" / "RELEASE_IDENTITY_LEDGER.json"),
        "--verify-tag",
        "--remote",
        str(origin),
    )
    assert completed.returncode != 0, completed.stdout
    assert "unresolvable" in completed.stdout


# -------------------------------------------------------------- networked


def test_verify_tag_passes_against_an_agreeing_remote(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    consumer = _seed_release_repo(tmp_path / "consumer")
    completed = _run(
        "--repo",
        str(consumer),
        "--ledger",
        str(consumer / "contracts" / "L.json"),
        "--verify-tag",
        "--remote",
        str(origin),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NETWORK: v1.1.0 == v1 ==" in completed.stdout


def test_verify_tag_still_runs_the_structural_checks(tmp_path: Path) -> None:
    """--verify-tag is additive. release.yml runs only this mode, so if the
    networked check short-circuited the local ones, a release could ship with
    a tagged tree whose version disagreed with the ledger and nothing would say so.
    """
    origin = _seed_release_repo(tmp_path / "origin")
    consumer = _seed_release_repo(tmp_path / "consumer", tagged_version="1.0.0")
    completed = _run(
        "--repo",
        str(consumer),
        "--ledger",
        str(consumer / "contracts" / "L.json"),
        "--verify-tag",
        "--remote",
        str(origin),
    )
    assert completed.returncode != 0, completed.stdout
    # The networked half agreed; the structural half is what caught it.
    assert "NETWORK: v1.1.0 == v1 ==" in completed.stdout
    assert "package_version at v1.1.0" in completed.stdout


def test_verify_tag_fails_when_the_remote_channel_has_moved(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin", channel_advances=True)
    consumer = _seed_release_repo(tmp_path / "consumer")
    completed = _run(
        "--repo",
        str(consumer),
        "--ledger",
        str(consumer / "contracts" / "L.json"),
        "--verify-tag",
        "--remote",
        str(origin),
    )
    assert completed.returncode != 0, completed.stdout
    assert "compatibility channel" in completed.stdout


def test_verify_tag_fails_closed_when_the_remote_cannot_be_resolved(tmp_path: Path) -> None:
    consumer = _seed_release_repo(tmp_path / "consumer")
    completed = _run(
        "--repo",
        str(consumer),
        "--ledger",
        str(consumer / "contracts" / "L.json"),
        "--verify-tag",
        "--remote",
        str(tmp_path / "does-not-exist"),
    )
    assert completed.returncode != 0, completed.stdout
    assert "unresolvable" in completed.stdout


# ---------------------------------------------------------------- fixtures


def _seed_release_repo(
    root: Path,
    *,
    package_version: str = "1.1.0",
    tagged_version: str | None = None,
    compatibility_channel: str = "v1",
    channel_advances: bool = False,
    ledger_overrides: dict[str, object] | None = None,
) -> Path:
    """A repo carrying a v2 ledger, a release tag, and a compatibility channel.

    ``tagged_version`` writes a different version at the tagged commit than the
    ledger claims. ``channel_advances`` moves ``v1`` onto a later commit so the
    channel and the exact release tag name different objects.
    """
    contracts = root / "contracts"
    contracts.mkdir(parents=True)
    at_tag = tagged_version or package_version
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "constellation-node-sdk"\nversion = "{at_tag}"\n'
    )

    ledger: dict[str, object] = {
        "schema": "l9.gate_sdk.release_identity_ledger.v2",
        "distribution_name": "constellation-node-sdk",
        "import_name": "constellation_node_sdk",
        "package_version": package_version,
        "protocol_version": "1.0",
        "python_requires": ">=3.12",
        "release_tag": f"v{package_version}",
        "compatibility_channel": compatibility_channel,
        "consumer_contract": {
            "mode": "moving_major_tag",
            "required_ref": compatibility_channel,
            "forbid_ref_kinds": ["branch", "commit_sha", "fork"],
        },
    }
    ledger.update(ledger_overrides or {})
    (contracts / "L.json").write_text(json.dumps(ledger, indent=2))

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    _git(root, "add", "pyproject.toml", "contracts")
    _git(root, "commit", "-qm", f"release {package_version}")
    _git(root, "tag", f"v{package_version}")

    if tagged_version is not None:
        # HEAD must still match the ledger: only the tagged tree drifts.
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "constellation-node-sdk"\nversion = "{package_version}"\n'
        )
        _git(root, "add", "pyproject.toml")
        _git(root, "commit", "-qm", "head restores the ledger version")

    if channel_advances:
        (root / "later.txt").write_text("channel moved past the release\n")
        _git(root, "add", "later.txt")
        _git(root, "commit", "-qm", "commit after the release")

    _git(root, "tag", compatibility_channel)
    return root
