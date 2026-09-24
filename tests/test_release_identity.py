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


def _verify(repo: Path, ledger: Path, remote: str) -> tuple[list[str], list[str]]:
    """Networked mode against a fixture remote, in-process.

    The CLI exposes no --remote flag on purpose: the canonical remote is the
    contract, and letting an operator point the check at another repository is
    the thing the contract exists to prevent. Tests reach the parameter
    directly instead, so no command-line argument ever reaches git.
    """
    return _load_validator().validate(repo, ledger, verify_tag=True, remote=remote)


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
    assert spec is not None, f"{SCRIPT} produced no import spec"
    assert spec.loader is not None, f"{SCRIPT} spec carries no loader"
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

    errors, _ = _verify(work, work / "contracts" / "RELEASE_IDENTITY_LEDGER.json", str(origin))
    assert any("unresolvable" in item for item in errors), errors


# -------------------------------------------------------------- networked


def test_verify_tag_passes_against_an_agreeing_remote(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    consumer = _seed_release_repo(tmp_path / "consumer")
    errors, notes = _verify(consumer, consumer / "contracts" / "L.json", str(origin))
    assert errors == [], errors
    assert any("NETWORK: v1.1.0 == v1 ==" in note for note in notes), notes


def test_verify_tag_still_runs_the_structural_checks(tmp_path: Path) -> None:
    """--verify-tag is additive. The release workflow runs networked modes, so if the
    networked check short-circuited the local ones, a release could ship with
    a tagged tree whose version disagreed with the ledger and nothing would say so.
    """
    origin = _seed_release_repo(tmp_path / "origin")
    consumer = _seed_release_repo(tmp_path / "consumer", tagged_version="1.0.0")
    errors, notes = _verify(consumer, consumer / "contracts" / "L.json", str(origin))
    # The networked half agreed; the structural half is what caught it.
    assert any("NETWORK: v1.1.0 == v1 ==" in note for note in notes), notes
    assert any("package_version at v1.1.0" in item for item in errors), errors


def test_verify_tag_fails_when_the_remote_channel_has_moved(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin", channel_advances=True)
    consumer = _seed_release_repo(tmp_path / "consumer")
    errors, _ = _verify(consumer, consumer / "contracts" / "L.json", str(origin))
    assert any("compatibility channel" in item for item in errors), errors


def test_verify_tag_refuses_a_remote_git_would_read_as_an_option(tmp_path: Path) -> None:
    """SonarCloud pythonsecurity:S8705.

    argv is a list and no shell is involved, which stops command injection but
    not argument injection: `git ls-remote --upload-pack=<cmd> <repo>` executes
    <cmd>, so a --remote beginning with `-` is an execution vector by itself.
    """
    consumer = _seed_release_repo(tmp_path / "consumer")
    errors, _ = _verify(
        consumer, consumer / "contracts" / "L.json", "--upload-pack=touch /tmp/pwned"
    )
    assert any("must not begin with" in item for item in errors), errors
    assert not Path("/tmp/pwned").exists()


def test_verify_tag_fails_closed_when_the_remote_cannot_be_resolved(tmp_path: Path) -> None:
    consumer = _seed_release_repo(tmp_path / "consumer")
    errors, _ = _verify(
        consumer, consumer / "contracts" / "L.json", str(tmp_path / "does-not-exist")
    )
    assert any("not a local repository" in item for item in errors), errors


# ------------------------------------------------------- release authority

WORKFLOWS = REPO / ".github" / "workflows"


def _workflow(name: str) -> dict[str, object]:
    import yaml

    loaded = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    # PyYAML reads the bare key `on` as boolean True.
    raw = workflow.get("on", workflow.get(True))
    assert isinstance(raw, dict)
    return raw


def test_release_publish_is_the_only_tag_triggered_workflow() -> None:
    """One release authority: a second tag-triggered workflow is a second owner."""
    tag_triggered = sorted(
        path.name
        for path in WORKFLOWS.glob("*.y*ml")
        if "tags" in (_triggers(_workflow(path.name)).get("push") or {})
    )
    assert tag_triggered == ["release-publish.yml"]


def test_release_publish_gates_identity_before_publication() -> None:
    workflow = _workflow("release-publish.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    gate_steps = " ".join(str(step.get("run", "")) for step in jobs["verify-tag"]["steps"])
    assert "validate_release_identity.py --release-gate" in gate_steps
    # Publication and release creation only on a tag push, never on dispatch.
    assert jobs["publish"]["if"] == "${{ github.event_name == 'push' }}"
    assert "github.event_name == 'push'" in jobs["gh-release"]["if"]
    assert "dry_run" not in json.dumps(_triggers(workflow))


def test_channel_promotion_follows_every_gate_and_is_reproved() -> None:
    jobs = _workflow("release-publish.yml")["jobs"]
    assert isinstance(jobs, dict)
    promote = jobs["promote-channel"]
    assert "gh-release" in promote["needs"]
    assert "github.event_name == 'push'" in promote["if"]
    runs = [str(step.get("run", "")) for step in promote["steps"]]
    gate = next(i for i, r in enumerate(runs) if "--release-gate" in r)
    move = next(i for i, r in enumerate(runs) if "refs/tags/${CHANNEL}" in r)
    proof = next(i for i, r in enumerate(runs) if "--verify-tag" in r)
    assert gate < move < proof


def test_ledger_names_the_release_authority() -> None:
    ledger = json.loads(LEDGER.read_text())
    assert ledger["release_authority"]["workflow"] == ".github/workflows/release-publish.yml"


# ------------------------------------------------------------ release gate


def _gate(repo: Path, remote: Path, tag: str = "v1.1.0") -> tuple[list[str], list[str]]:
    """Pre-publish release gate against a fixture remote, in-process."""
    return _load_validator().validate(
        repo, repo / "contracts" / "L.json", release_gate=tag, remote=str(remote)
    )


def _clone(origin: Path, dest: Path) -> Path:
    subprocess.run(
        [GIT, "clone", "-q", "--no-local", str(origin), str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


def test_release_gate_passes_when_head_is_the_release_object(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    work = _clone(origin, tmp_path / "work")
    errors, notes = _gate(work, origin)
    assert errors == [], errors
    assert any(note.startswith("RELEASE-GATE: HEAD == v1.1.0 ==") for note in notes), notes


def test_release_gate_fails_when_head_is_one_commit_past_the_tag(tmp_path: Path) -> None:
    """F-004 discriminator.

    A later commit that kept version 1.1.0 and the same ledger satisfies both
    remote tag agreement and manifest agreement. It is still not the release,
    so building it under the release's name must fail.
    """
    origin = _seed_release_repo(tmp_path / "origin")
    work = _clone(origin, tmp_path / "work")
    (work / "later.txt").write_text("same version, same ledger, different bytes\n")
    _git(work, "add", "later.txt")
    _git(work, "commit", "-qm", "commit after the release tag")

    # The steady-state networked check cannot tell the difference...
    verify_errors, _ = _verify(work, work / "contracts" / "L.json", str(origin))
    assert verify_errors == [], verify_errors
    # ...the release gate can.
    errors, _ = _gate(work, origin)
    assert any("is not v1.1.0" in item for item in errors), errors


def test_release_gate_requires_the_requested_tag_to_be_the_ledger_tag(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    work = _clone(origin, tmp_path / "work")
    errors, _ = _gate(work, origin, tag="v1.2.0")
    assert any("is not the ledger release tag" in item for item in errors), errors


def test_release_gate_accepts_a_first_release_with_no_channel_yet(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    _git(origin, "tag", "-d", "v1")
    work = _clone(origin, tmp_path / "work")
    errors, notes = _gate(work, origin)
    assert errors == [], errors
    assert any(note.endswith("v1 absent") for note in notes), notes


def test_release_gate_accepts_a_channel_still_on_the_previous_release(tmp_path: Path) -> None:
    """Promotion happens after the gates, so the channel legitimately lags."""
    origin = _seed_release_repo(tmp_path / "origin")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "the new release")
    _git(origin, "tag", "-f", "v1.1.0")
    work = _clone(origin, tmp_path / "work")
    errors, _ = _gate(work, origin)
    assert errors == [], errors


def test_release_gate_refuses_a_channel_off_the_release_history(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    # A root commit with no parent: history the release never descended from.
    empty_tree = subprocess.run(
        [GIT, "hash-object", "-t", "tree", "/dev/null"],
        cwd=origin,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    unrelated = subprocess.run(
        [GIT, "commit-tree", empty_tree, "-m", "unrelated history"],
        cwd=origin,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(origin, "tag", "-f", "v1", unrelated)
    work = _clone(origin, tmp_path / "work")
    errors, _ = _gate(work, origin)
    assert any("is not an ancestor of the release" in item for item in errors), errors


def test_release_gate_has_no_shallow_clone_exception(tmp_path: Path) -> None:
    origin = _seed_release_repo(tmp_path / "origin")
    (origin / "later.txt").write_text("depth\n")
    _git(origin, "add", "later.txt")
    _git(origin, "commit", "-qm", "second")
    work = tmp_path / "shallow"
    subprocess.run(
        [GIT, "clone", "-q", "--no-local", "--depth", "1", "--no-tags", str(origin), str(work)],
        check=True,
        capture_output=True,
    )
    errors, _ = _gate(work, origin)
    assert any("absent from this clone" in item for item in errors), errors


def test_release_gate_cli_rejects_a_malformed_tag() -> None:
    completed = _run("--repo", str(REPO), "--release-gate", "main")
    assert completed.returncode == 2
    assert "is not a vX.Y.Z release tag" in completed.stderr


def test_release_gate_and_verify_tag_are_exclusive_modes() -> None:
    completed = _run("--repo", str(REPO), "--verify-tag", "--release-gate", "v1.1.0")
    assert completed.returncode == 2
    assert "not allowed with" in completed.stderr


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
