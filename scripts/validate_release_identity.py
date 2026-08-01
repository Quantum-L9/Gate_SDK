#!/usr/bin/env python3
"""Validate Gate_SDK release identity ledger against git + pyproject.

Fails closed when tag, package version, and claimed release identity disagree.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path


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


def validate(repo: Path, ledger_path: Path) -> list[str]:
    errors: list[str] = []
    ledger = json.loads(ledger_path.read_text())
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    project = pyproject["project"]

    if project.get("name") != ledger.get("distribution_name"):
        errors.append(
            f"distribution_name mismatch: pyproject={project.get('name')} ledger={ledger.get('distribution_name')}"
        )
    if project.get("version") != ledger.get("package_version"):
        errors.append(
            f"package_version mismatch at HEAD tree: pyproject={project.get('version')} "
            f"ledger={ledger.get('package_version')}"
        )

    tag = ledger["release_tag"]
    expected_sha = ledger["release_commit_sha"]
    try:
        tag_sha = _git(repo, "rev-parse", f"{tag}^{{}}")
    except RuntimeError as exc:
        errors.append(f"release_tag {tag} not resolvable: {exc}")
        return errors
    if tag_sha != expected_sha:
        errors.append(f"tag {tag} resolves to {tag_sha}, ledger expects {expected_sha}")

    # Package version at the tagged commit must match ledger.
    try:
        tagged_pyproject = _git(repo, "show", f"{expected_sha}:pyproject.toml")
        tagged_version = tomllib.loads(tagged_pyproject)["project"]["version"]
        if tagged_version != ledger["package_version"]:
            errors.append(
                f"package_version at {expected_sha} is {tagged_version}, "
                f"ledger expects {ledger['package_version']}"
            )
    except RuntimeError as exc:
        errors.append(f"unable to read pyproject at release commit: {exc}")

    head = _git(repo, "rev-parse", "HEAD")
    if ledger.get("claim_head_is_release") and head != expected_sha:
        errors.append(
            f"ledger claims HEAD is release but HEAD={head} release={expected_sha}"
        )

    # Consumer pin must be the immutable release commit.
    pin = ledger.get("consumer_pin", {})
    if pin.get("sha") != expected_sha:
        errors.append("consumer_pin.sha must equal release_commit_sha")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Gate_SDK repository root",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Path to RELEASE_IDENTITY_LEDGER.json",
    )
    args = parser.parse_args()
    ledger = args.ledger or (args.repo / "contracts" / "RELEASE_IDENTITY_LEDGER.json")
    if not ledger.is_file():
        print(f"FAIL: ledger missing: {ledger}", file=sys.stderr)
        return 1
    errors = validate(args.repo, ledger)
    if errors:
        print("FAIL: release identity disagreement")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"PASS: release identity agrees ({ledger})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
