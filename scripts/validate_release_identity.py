#!/usr/bin/env python3
"""Validate the Gate_SDK release-identity ledger against git + pyproject.

Gate_SDK is the release-identity authority for the Constellation set. The
consumer compatibility contract is the *moving major tag* ``v{major}``, not a
commit sha:

    package version 1.1.0
        -> immutable release tag  v1.1.0
        -> moving channel         v1      <- what Gate / CEG / EIE declare

A resolved commit sha is legitimate as generated-lock resolution, artifact
provenance, or audit evidence. It is never the consumer compatibility
contract, so this validator derives every expectation from the package version
and refuses a ledger that reintroduces consumer sha policy.

Modes
-----
default        Structural checks plus local tag resolution. A genuinely shallow
               clone may skip object-level checks and says so.
--verify-tag   Networked agreement. Resolves the release tag and the
               compatibility channel from the canonical remote and fails closed
               when either cannot be resolved.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path

LEDGER_SCHEMA = "l9.gate_sdk.release_identity_ledger.v2"
CANONICAL_REMOTE = "https://github.com/Quantum-L9/Gate_SDK.git"
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

# Keys whose presence means the retired v1 consumer-sha policy is back. These
# are kept apart from the merely-stale v1 fields below because an error message
# that misnames the violation teaches the wrong correction.
CONSUMER_SHA_KEYS = ("consumer_pin", "release_commit_sha")
# v1 fields that carry no consumer policy but no longer have a v2 meaning.
RETIRED_V1_KEYS = ("release_tag_object",)


def _git(repo: Path, *args: str) -> str:
    # Call real git by absolute path. Controller worktrees often put a git
    # guard ahead of PATH that requires L9_REAL_GIT and denies some forms.
    git_bin = Path("/usr/bin/git")
    completed = subprocess.run(
        [str(git_bin), *args],
        check=False,
        text=True,
        capture_output=True,
        cwd=str(repo),
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _is_shallow(repo: Path) -> bool:
    """True when this clone is a shallow git repository."""
    try:
        return _git(repo, "rev-parse", "--is-shallow-repository") == "true"
    except RuntimeError:
        return False


def _resolve_local(repo: Path, ref: str) -> str | None:
    """Resolve *ref* to a commit object in this clone, peeling annotated tags."""
    for candidate in (f"{ref}^{{commit}}", ref):
        try:
            return _git(repo, "rev-parse", "--verify", candidate)
        except RuntimeError:
            continue
    return None


def _safe_remote(remote: str) -> str:
    """Reject a remote that git would parse as an option.

    Passing argv as a list and never invoking a shell stops *command*
    injection, but not *argument* injection: ``git ls-remote
    --upload-pack=<cmd> <repo>`` runs ``<cmd>``, so a --remote value beginning
    with ``-`` is an execution vector on its own (SonarCloud
    pythonsecurity:S8705). Callers pass --remote, so validate it here and use
    --end-of-options below rather than trusting either alone.
    """
    if not remote or remote.startswith("-"):
        msg = f"refusing remote {remote!r}: a remote must not begin with '-'"
        raise ValueError(msg)
    return remote


def _resolve_remote(remote: str, ref: str) -> str | None:
    """Resolve ``refs/tags/<ref>`` at *remote*, preferring the peeled object."""
    try:
        output = _git(
            Path.cwd(),
            "ls-remote",
            "--tags",
            "--end-of-options",
            _safe_remote(remote),
            f"refs/tags/{ref}",
        )
    except RuntimeError:
        return None
    peeled: str | None = None
    direct: str | None = None
    for line in output.splitlines():
        sha, _, name = line.partition("\t")
        if name == f"refs/tags/{ref}^{{}}":
            peeled = sha.strip()
        elif name == f"refs/tags/{ref}":
            direct = sha.strip()
    return peeled or direct


def derive_expectations(package_version: str) -> tuple[str, str]:
    """Return (exact release tag, moving compatibility channel)."""
    match = VERSION_RE.match(package_version)
    if match is None:
        msg = f"package_version {package_version!r} is not MAJOR.MINOR.PATCH"
        raise ValueError(msg)
    return f"v{package_version}", f"v{match.group(1)}"


def check_ledger_policy(ledger: dict[str, object]) -> list[str]:
    """Structural ledger policy — no git, no filesystem, no network."""
    errors: list[str] = []

    if ledger.get("schema") != LEDGER_SCHEMA:
        errors.append(f"schema must be {LEDGER_SCHEMA}, got {ledger.get('schema')!r}")

    for key in CONSUMER_SHA_KEYS:
        if key in ledger:
            errors.append(
                f"{key!r} is retired consumer-sha policy; the consumer contract is "
                "the moving major channel"
            )

    for key in RETIRED_V1_KEYS:
        if key in ledger:
            errors.append(f"{key!r} is a schema v1 field with no meaning under {LEDGER_SCHEMA}")

    contract = ledger.get("consumer_contract")
    if not isinstance(contract, dict):
        errors.append("consumer_contract object is missing")
        return errors

    if contract.get("mode") != "moving_major_tag":
        errors.append(
            f"consumer_contract.mode must be 'moving_major_tag', got {contract.get('mode')!r}"
        )

    channel = ledger.get("compatibility_channel")
    if contract.get("required_ref") != channel:
        errors.append(
            f"consumer_contract.required_ref {contract.get('required_ref')!r} "
            f"must equal compatibility_channel {channel!r}"
        )

    forbidden = contract.get("forbid_ref_kinds")
    if not isinstance(forbidden, list) or not {"branch", "commit_sha", "fork"} <= set(forbidden):
        errors.append("consumer_contract.forbid_ref_kinds must forbid branch, commit_sha, and fork")

    # A sha anywhere inside the consumer contract is consumer-sha policy by
    # another name, whatever the field is called.
    if SHA_RE.search(json.dumps(contract)):
        errors.append("consumer_contract must not contain a 40-character commit sha")

    return errors


def check_channel_agreement(release_sha: str | None, channel_sha: str | None) -> list[str]:
    """The exact release tag and the moving channel must name one object."""
    if release_sha is None or channel_sha is None:
        return []
    if release_sha != channel_sha:
        return [
            f"release tag resolves to {release_sha} but compatibility channel "
            f"resolves to {channel_sha}; the channel must point at the approved release"
        ]
    return []


def validate(
    repo: Path,
    ledger_path: Path,
    *,
    verify_tag: bool = False,
    remote: str = CANONICAL_REMOTE,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    ledger = json.loads(ledger_path.read_text())
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    project = pyproject["project"]

    errors.extend(check_ledger_policy(ledger))

    if project.get("name") != ledger.get("distribution_name"):
        errors.append(
            f"distribution_name mismatch: pyproject={project.get('name')} "
            f"ledger={ledger.get('distribution_name')}"
        )

    package_version = ledger.get("package_version")
    if project.get("version") != package_version:
        errors.append(
            f"package_version mismatch at HEAD tree: pyproject={project.get('version')} "
            f"ledger={package_version}"
        )

    if not isinstance(package_version, str):
        errors.append("package_version must be a string")
        return errors, notes

    try:
        expected_tag, expected_channel = derive_expectations(package_version)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, notes

    release_tag = ledger.get("release_tag")
    channel = ledger.get("compatibility_channel")
    if release_tag != expected_tag:
        errors.append(f"release_tag must be {expected_tag!r}, got {release_tag!r}")
    if channel != expected_channel:
        errors.append(f"compatibility_channel must be {expected_channel!r}, got {channel!r}")

    if verify_tag:
        # Additive, not instead-of. --verify-tag used to return here, which
        # meant release.yml — the one workflow that runs only this mode —
        # never reached the "package version at the release tag" check below.
        # The networked proof is extra evidence, never a replacement for the
        # structural ones, and it matches how the consumer validators layer
        # their two modes.
        try:
            remote_release = _resolve_remote(remote, expected_tag)
            remote_channel = _resolve_remote(remote, expected_channel)
        except ValueError as exc:
            # Fails closed like any other unresolvable tag, but says which of
            # the two reasons it was.
            errors.append(f"--verify-tag: {exc}")
            return errors, notes
        # Required networked mode fails closed: an unresolvable tag is not a
        # warning, it is the absence of the proof this mode exists to produce.
        if remote_release is None:
            errors.append(f"--verify-tag: {expected_tag} unresolvable at {remote}")
        if remote_channel is None:
            errors.append(f"--verify-tag: {expected_channel} unresolvable at {remote}")
        disagreement = check_channel_agreement(remote_release, remote_channel)
        errors.extend(disagreement)
        if remote_release is not None and remote_channel is not None and not disagreement:
            notes.append(f"NETWORK: {expected_tag} == {expected_channel} == {remote_channel}")

    release_sha = _resolve_local(repo, expected_tag)
    channel_sha = _resolve_local(repo, expected_channel)

    if release_sha is None or channel_sha is None:
        missing = [
            name
            for name, sha in ((expected_tag, release_sha), (expected_channel, channel_sha))
            if sha is None
        ]
        if _is_shallow(repo):
            notes.append(
                f"INFO: skipped tag object checks (shallow clone); unresolved: {', '.join(missing)}"
            )
        else:
            errors.append(
                f"{', '.join(missing)} absent from a non-shallow clone — "
                "release identity cannot be judged without the tag objects"
            )
    else:
        errors.extend(check_channel_agreement(release_sha, channel_sha))

    # Package version at the tagged release must match the ledger when that
    # object is present. Shallow CI clones omit it; note rather than FAIL.
    if release_sha is not None:
        try:
            tagged = _git(repo, "show", f"{release_sha}:pyproject.toml")
            tagged_version = tomllib.loads(tagged)["project"]["version"]
            if tagged_version != package_version:
                errors.append(
                    f"package_version at {expected_tag} ({release_sha}) is "
                    f"{tagged_version}, ledger expects {package_version}"
                )
        except RuntimeError as exc:
            errors.append(f"unable to read pyproject at {expected_tag}: {exc}")

    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("contracts/RELEASE_IDENTITY_LEDGER.json"),
    )
    parser.add_argument(
        "--verify-tag",
        action="store_true",
        help="resolve the release tag and channel from the canonical remote; fails closed",
    )
    parser.add_argument("--remote", default=CANONICAL_REMOTE)
    args = parser.parse_args(argv)
    ledger_path = args.ledger if args.ledger.is_absolute() else args.repo / args.ledger
    errors, notes = validate(args.repo, ledger_path, verify_tag=args.verify_tag, remote=args.remote)
    for note in notes:
        print(note)
    if errors:
        print("FAIL: release identity disagreement")
        for err in errors:
            print(f"- {err}")
        return 1
    mode = "networked" if args.verify_tag else "local"
    print(f"PASS: release identity agrees ({ledger_path}, {mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
