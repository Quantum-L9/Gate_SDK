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

Publication is derived, never declared. Whether a release exists is a fact
about the canonical remote, so the remote is asked: the exact release tag
present means published, absent means prepared but not published, and a remote
that does not answer is a failure rather than either verdict. The ledger used
to record that state instead, which made it a hand-flipped field: forget the
follow-up pull request and a published release stayed unverified; flip it early
and the validator asserted a release that did not exist.

Modes
-----
default        Structural checks, local tag resolution, and the remote probe
               that derives publication. A genuinely shallow clone may skip
               object-level checks and says so.
--verify-tag   Networked agreement. Resolves the release tag and the
               compatibility channel from the canonical remote and fails closed
               when either cannot be resolved. Steady-state proof: run it after
               the channel has been promoted.
--release-gate TAG
               Pre-publish gate for the tag push that started a release. TAG
               must be the ledger's release tag, it must resolve at the
               canonical remote, and the checked-out HEAD must be exactly that
               object — a later commit that kept the same version and ledger
               is not the release. The channel may not agree yet: promotion
               moves it after the gates, so it must be absent (first release of
               a major), already on the release, or an ancestor of it. No
               shallow-clone exception applies.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Literal

LEDGER_SCHEMA = "l9.gate_sdk.release_identity_ledger.v2"
CANONICAL_REMOTE = "https://github.com/Quantum-L9/Gate_SDK.git"
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-rc\d+)?$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

# Every key the ledger may carry. Publication is deliberately absent from this
# set: it is a fact about the remote, and a field restating it is a manual step
# that nothing can verify. Unknown keys are refused rather than ignored,
# because a field nobody reads is a statement nobody checks.
LEDGER_KEYS = frozenset(
    {
        "agreement_rules",
        "artifacts",
        "compatibility_channel",
        "consumer_contract",
        "delivery_branch",
        "delivery_policy",
        "distribution_name",
        "github_release_url",
        "import_name",
        "observed_at_build",
        "package_version",
        "protocol_version",
        "provenance",
        "python_requires",
        "release_authority",
        "release_tag",
        "schema",
        "task_id",
    }
)
# Keys whose presence means the retired v1 consumer-sha policy is back. These
# are kept apart from the merely-stale v1 fields below because an error message
# that misnames the violation teaches the wrong correction.
CONSUMER_SHA_KEYS = ("consumer_pin", "release_commit_sha")
# v1 fields that carry no consumer policy but no longer have a v2 meaning.
RETIRED_V1_KEYS = ("release_tag_object",)

# What the remote says about one release tag. ``present`` is published,
# ``absent`` is not published yet, and ``unreachable`` is the question going
# unanswered — which is not a third kind of "not published".
TagState = Literal["present", "absent", "unreachable"]


def _git_process(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git and keep the exit status, which some questions are answered by.

    Call real git by absolute path. Controller worktrees often put a git
    guard ahead of PATH that requires L9_REAL_GIT and denies some forms.
    """
    return subprocess.run(
        [str(Path("/usr/bin/git")), *args],
        check=False,
        text=True,
        capture_output=True,
        cwd=str(repo),
    )


def _git(repo: Path, *args: str) -> str:
    """Run git and return stdout, raising when it fails."""
    completed = _git_process(repo, *args)
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
    """Return a remote from a fixed set — never the caller's string.

    Passing argv as a list and never invoking a shell stops *command*
    injection, but not *argument* injection: ``git ls-remote
    --upload-pack=<cmd> <repo>`` runs ``<cmd>``, so a remote beginning with
    ``-`` is an execution vector on its own (SonarCloud
    pythonsecurity:S8705).

    The canonical remote is the contract, so it is matched by equality and the
    module constant is returned: what reaches git is provably not built from
    the argument. Anything else is a local fixture path used by the tests,
    which must be an existing git repository directory and must not look like
    an option. The CLI deliberately exposes no --remote flag — an operator
    pointing this check at a non-canonical repository is exactly the thing the
    release-identity contract exists to prevent.
    """
    if remote == CANONICAL_REMOTE:
        return CANONICAL_REMOTE
    if not remote or remote.startswith("-"):
        msg = f"refusing remote {remote!r}: a remote must not begin with '-'"
        raise ValueError(msg)
    candidate = Path(remote)
    if not candidate.is_dir():
        msg = f"refusing remote {remote!r}: not the canonical remote and not a local repository"
        raise ValueError(msg)
    return str(candidate.resolve(strict=True))


def _parse_tag_lines(output: str, ref: str) -> tuple[str | None, str | None]:
    """Return ``(direct, peeled)`` for ``refs/tags/<ref>`` from ls-remote output.

    ``direct`` is the sha the remote publishes for the ref — the tag object
    itself for an annotated tag — and ``peeled`` is the commit it resolves to,
    which the remote reports on the ``refs/tags/<ref>^{}`` line.
    """
    peeled: str | None = None
    direct: str | None = None
    for line in output.splitlines():
        sha, _, name = line.partition("\t")
        if name == f"refs/tags/{ref}^{{}}":
            peeled = sha.strip()
        elif name == f"refs/tags/{ref}":
            direct = sha.strip()
    return direct, peeled


def _resolve_remote(remote: str, ref: str) -> str | None:
    """Resolve ``refs/tags/<ref>`` at *remote*, preferring the peeled object.

    The pattern is deliberately wider than the ref: ``git ls-remote`` with an
    exact ``refs/tags/<ref>`` pattern does not emit the ``^{}`` peel line, so
    the exact form reports an annotated tag's own object and the commit it
    names would never be seen. ``_parse_tag_lines`` matches the exact names, so
    sibling tags the wider pattern also matches are ignored.
    """
    try:
        output = _git(
            Path.cwd(),
            "ls-remote",
            "--tags",
            "--end-of-options",
            _safe_remote(remote),
            f"refs/tags/{ref}*",
        )
    except RuntimeError:
        return None
    direct, peeled = _parse_tag_lines(output, ref)
    return peeled or direct


def _remote_tag_state(remote: str, tag: str) -> TagState:
    """Ask *remote* whether ``refs/tags/<tag>`` exists.

    Presence only. The exact pattern makes git omit the ``^{}`` peel line, so
    this cannot answer which object the tag names — ``_resolve_remote`` does
    that with a wider pattern.

    ``--exit-code`` is what makes the answer three-valued. 0 is the remote
    reporting the ref, 2 is the remote reporting it has no such ref, and every
    other status is the remote not answering at all. Folding the last into the
    second would turn an unreachable remote into "not published", a verdict
    about the release that no evidence supports.
    """
    completed = _git_process(
        Path.cwd(),
        "ls-remote",
        "--exit-code",
        "--tags",
        "--end-of-options",
        _safe_remote(remote),
        f"refs/tags/{tag}",
    )
    if completed.returncode == 0:
        return "present"
    if completed.returncode == 2:
        return "absent"
    return "unreachable"


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

    # Fail closed on anything else. The retired keys above are named separately
    # so their error teaches the right correction; every other unknown key is
    # refused here rather than read past, because a field the validator ignores
    # is a statement no one checks.
    retired = {*CONSUMER_SHA_KEYS, *RETIRED_V1_KEYS}
    for key in sorted(set(ledger) - LEDGER_KEYS - retired):
        errors.append(
            f"{key!r} is not a ledger field; unknown keys are refused rather than ignored"
        )

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


def check_manifest_agreement(project: dict[str, object], ledger: dict[str, object]) -> list[str]:
    """pyproject and the ledger must describe the same distribution at HEAD."""
    errors: list[str] = []
    if project.get("name") != ledger.get("distribution_name"):
        errors.append(
            f"distribution_name mismatch: pyproject={project.get('name')} "
            f"ledger={ledger.get('distribution_name')}"
        )
    if project.get("version") != ledger.get("package_version"):
        errors.append(
            f"package_version mismatch at HEAD tree: pyproject={project.get('version')} "
            f"ledger={ledger.get('package_version')}"
        )
    return errors


def check_declared_refs(ledger: dict[str, object], expected_tag: str, channel: str) -> list[str]:
    """The ledger's own refs must be the ones the package version derives."""
    errors: list[str] = []
    if ledger.get("release_tag") != expected_tag:
        errors.append(f"release_tag must be {expected_tag!r}, got {ledger.get('release_tag')!r}")
    if ledger.get("compatibility_channel") != channel:
        errors.append(
            f"compatibility_channel must be {channel!r}, "
            f"got {ledger.get('compatibility_channel')!r}"
        )
    return errors


def check_publication(remote: str, expected_tag: str) -> tuple[TagState, list[str]]:
    """Derive whether *expected_tag* is published from the canonical remote.

    ``present`` is published: the exact tag exists where consumers resolve it.
    ``absent`` is prepared but not published, which is the ordinary state of a
    release-preparation pull request and of every release before its tag push.
    ``unreachable`` is a hard error, never a third kind of "not published": the
    question went unanswered, so no regime may be chosen on the strength of it.

    A refused remote is reported the same way and returned rather than raised,
    because it also means no probe ran: the remaining checks still run, and the
    run still fails closed.
    """
    try:
        state = _remote_tag_state(remote, expected_tag)
    except ValueError as exc:
        return "unreachable", [f"publication of {expected_tag} is unknown: {exc}"]
    if state == "unreachable":
        return state, [
            f"publication of {expected_tag} is unknown: {remote} did not answer the tag "
            "probe (git ls-remote exited other than 0 for present or 2 for absent); "
            "an unanswered probe is neither a published nor an unpublished release"
        ]
    return state, []


def check_remote_identity(
    remote: str, expected_tag: str, channel: str
) -> tuple[list[str], list[str]]:
    """Networked agreement between the release tag and the moving channel.

    Fails closed: an unresolvable tag is not a warning, it is the absence of
    the proof this mode exists to produce.

    A refused remote propagates as ``ValueError`` rather than becoming an entry
    in the returned list, because the two are different failures: an
    unresolvable tag is a verdict about the release, a refused remote means no
    check ran at all and the caller must stop.
    """
    remote_release = _resolve_remote(remote, expected_tag)
    remote_channel = _resolve_remote(remote, channel)

    errors: list[str] = []
    if remote_release is None:
        errors.append(f"--verify-tag: {expected_tag} unresolvable at {remote}")
    if remote_channel is None:
        errors.append(f"--verify-tag: {channel} unresolvable at {remote}")

    disagreement = check_channel_agreement(remote_release, remote_channel)
    errors.extend(disagreement)
    if remote_release is not None and remote_channel is not None and not disagreement:
        return errors, [f"NETWORK: {expected_tag} == {channel} == {remote_channel}"]
    return errors, []


def check_head_binding(repo: Path, release_sha: str | None, expected_tag: str) -> list[str]:
    """The revision being validated and built must be the release object itself.

    Remote tag agreement and manifest agreement are both satisfied by a later
    commit that kept the same version and ledger, so neither proves what is
    about to be published. Only equality with the tag object does.
    """
    if release_sha is None:
        return []  # the unresolvable tag is already reported by the caller
    head = _resolve_local(repo, "HEAD")
    if head is None:
        return ["--release-gate: HEAD is unresolvable; nothing to bind to the release"]
    if head != release_sha:
        return [
            f"--release-gate: HEAD {head} is not {expected_tag} ({release_sha}); "
            "a release must build the tagged revision, not a later one"
        ]
    return []


def check_channel_promotable(
    repo: Path, release_sha: str | None, channel_sha: str | None, channel: str
) -> list[str]:
    """Before promotion the channel may lag the release, but only behind it.

    Absent is the first release of a major. Equal is a re-run. Anything else
    must be an ancestor, so promotion only ever moves consumers forward along
    the history that was released.
    """
    if release_sha is None or channel_sha is None or release_sha == channel_sha:
        return []
    try:
        _git(repo, "merge-base", "--is-ancestor", channel_sha, release_sha)
    except RuntimeError:
        return [
            f"--release-gate: {channel} ({channel_sha}) is not an ancestor of the "
            f"release ({release_sha}); promotion must move the channel forward"
        ]
    return []


def check_local_tag_objects(
    repo: Path,
    expected_tag: str,
    channel: str,
    package_version: str,
    *,
    release_gate: bool = False,
    publication: TagState = "present",
) -> tuple[list[str], list[str]]:
    """Resolve both refs in this clone and judge what they point at.

    Under ``release_gate`` the channel is judged by the networked promotion
    check instead, and the release tag must be present: the shallow-clone
    exception is for PR validation, never for a publish.

    Outside the gate an unresolved ref is judged by *publication*, which the
    caller derived from the remote. A release that is not published yet has no
    tag object to bind to, so its absence is not a disagreement; a published
    one whose object is missing from a complete clone is. ``unreachable`` means
    publication is unknown, so the judgement is withheld rather than guessed —
    the caller already fails the run on it.
    """
    errors: list[str] = []
    notes: list[str] = []

    release_sha = _resolve_local(repo, expected_tag)
    channel_sha = _resolve_local(repo, channel)
    unresolved = release_sha is None or channel_sha is None
    missing = [
        name for name, sha in ((expected_tag, release_sha), (channel, channel_sha)) if sha is None
    ]

    if release_gate:
        if release_sha is None:
            errors.append(
                f"--release-gate: {expected_tag} absent from this clone; "
                "check out the tag with full history"
            )
    elif unresolved and _is_shallow(repo):
        notes.append(
            f"INFO: skipped tag object checks (shallow clone); unresolved: {', '.join(missing)}"
        )
    elif publication == "absent":
        # Prepared but not published: the tag cannot exist before the merge that
        # publishes it, and the channel is promoted only afterwards, so there is
        # nothing here to disagree with. --release-gate still requires the tag on
        # the remote and HEAD == that object, so the publish path stays closed.
        notes.append(
            f"INFO: {expected_tag} is not published yet (absent from the remote); "
            "tag-object and channel agreement are deferred to the release gate"
        )
    elif publication == "unreachable":
        notes.append(
            f"INFO: publication of {expected_tag} is unknown (the remote did not answer); "
            "tag-object and channel agreement withheld"
        )
    elif unresolved:
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


def check_release_gate(
    repo: Path, remote: str, requested_tag: str, expected_tag: str, channel: str
) -> tuple[list[str], list[str]]:
    """Pre-publish networked gate for the tag push that started a release."""
    if requested_tag != expected_tag:
        return [
            f"--release-gate: requested tag {requested_tag!r} is not the ledger "
            f"release tag {expected_tag!r}"
        ], []
    remote_release = _resolve_remote(remote, expected_tag)
    remote_channel = _resolve_remote(remote, channel)
    if remote_release is None:
        return [f"--release-gate: {expected_tag} unresolvable at {remote}"], []
    errors = check_head_binding(repo, remote_release, expected_tag)
    errors.extend(check_channel_promotable(repo, remote_release, remote_channel, channel))
    if errors:
        return errors, []
    state = "absent" if remote_channel is None else remote_channel
    return [], [f"RELEASE-GATE: HEAD == {expected_tag} == {remote_release}; {channel} {state}"]


def validate(
    repo: Path,
    ledger_path: Path,
    *,
    verify_tag: bool = False,
    release_gate: str | None = None,
    remote: str = CANONICAL_REMOTE,
) -> tuple[list[str], list[str]]:
    """Run every release-identity check and return (errors, notes).

    Each check is a named function above that owns one question. This stays an
    orchestrator so the order — structural, then publication, then local and
    networked — is readable in one screen and a new check has an obvious place
    to go.
    """
    errors: list[str] = []
    notes: list[str] = []
    ledger = json.loads(ledger_path.read_text())
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    project = pyproject["project"]

    errors.extend(check_ledger_policy(ledger))
    errors.extend(check_manifest_agreement(project, ledger))

    package_version = ledger.get("package_version")
    if not isinstance(package_version, str):
        errors.append("package_version must be a string")
        return errors, notes

    try:
        expected_tag, expected_channel = derive_expectations(package_version)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, notes

    errors.extend(check_declared_refs(ledger, expected_tag, expected_channel))

    # Publication is asked of the remote once, here: it is the fact that decides
    # whether an unresolved local tag is a prepared release or a disagreement,
    # and every mode below depends on that distinction.
    publication, publication_errors = check_publication(remote, expected_tag)
    errors.extend(publication_errors)

    if verify_tag and release_gate is not None:
        errors.append("--verify-tag and --release-gate are separate modes; pick one")
        return errors, notes

    if release_gate is not None:
        try:
            gate_errors, gate_notes = check_release_gate(
                repo, remote, release_gate, expected_tag, expected_channel
            )
        except ValueError as exc:
            errors.append(f"--release-gate: {exc}")
            return errors, notes
        errors.extend(gate_errors)
        notes.extend(gate_notes)

    if verify_tag:
        # Additive, not instead-of. --verify-tag used to return here, which
        # meant the release workflow — which runs only networked modes —
        # never reached the local checks below. The networked proof is extra
        # evidence, never a replacement for the structural ones, and it
        # matches how the consumer validators layer their two modes.
        try:
            remote_errors, remote_notes = check_remote_identity(
                remote, expected_tag, expected_channel
            )
        except ValueError as exc:
            # A refused remote means no networked check ran; say which of the
            # two reasons it was and stop rather than reporting local results
            # as if the requested proof had been attempted.
            errors.append(f"--verify-tag: {exc}")
            return errors, notes
        errors.extend(remote_errors)
        notes.extend(remote_notes)

    local_errors, local_notes = check_local_tag_objects(
        repo,
        expected_tag,
        expected_channel,
        package_version,
        release_gate=release_gate is not None,
        publication=publication,
    )
    errors.extend(local_errors)
    notes.extend(local_notes)

    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("contracts/RELEASE_IDENTITY_LEDGER.json"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-tag",
        action="store_true",
        help="resolve the release tag and channel from the canonical remote; fails closed",
    )
    mode.add_argument(
        "--release-gate",
        metavar="TAG",
        help="pre-publish gate: TAG is the ledger release tag and HEAD is its object",
    )
    args = parser.parse_args(argv)
    if args.release_gate is not None and not TAG_RE.match(args.release_gate):
        parser.error(f"--release-gate {args.release_gate!r} is not a vX.Y.Z release tag")
    ledger_path = args.ledger if args.ledger.is_absolute() else args.repo / args.ledger
    # No --remote flag: the canonical remote is the contract, not an option.
    errors, notes = validate(
        args.repo, ledger_path, verify_tag=args.verify_tag, release_gate=args.release_gate
    )
    for note in notes:
        print(note)
    if errors:
        print("FAIL: release identity disagreement")
        for err in errors:
            print(f"- {err}")
        return 1
    if args.release_gate is not None:
        label = "release-gate"
    else:
        # The default mode is no longer local-only: publication is derived from
        # the remote, so calling it "local" would misdescribe what ran.
        label = "networked" if args.verify_tag else "default"
    print(f"PASS: release identity agrees ({ledger_path}, {label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
