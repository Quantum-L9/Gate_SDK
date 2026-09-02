#!/usr/bin/env bash
# Run pip-audit with the repository's reviewed suppression list.
#
# pip-audit has no config-file support (see its README: suppressions are
# `--ignore-vuln ID` flags only), so three workflows would otherwise each carry
# their own copy of the list and drift apart. This script is the single source:
# ci.yml gate-5, nightly.yml, and release-publish.yml all call it and pass their
# own site-specific flags through ("$@" — e.g. --strict, --skip-editable).
#
# It fails closed in three ways:
#   - the suppression file must exist;
#   - its EXPIRES date must be present, well-formed, and in the future;
#   - pip-audit's own exit code is propagated unchanged.
set -euo pipefail

IGNORE_FILE="${PIP_AUDIT_IGNORE_FILE:-.github/pip-audit-ignore.txt}"

if [[ ! -f "$IGNORE_FILE" ]]; then
  echo "FAIL: suppression file not found: $IGNORE_FILE" >&2
  echo "      pip-audit suppressions are reviewed in-repo; refusing to audit" >&2
  echo "      without them rather than silently scanning with none." >&2
  exit 1
fi

# --- expiry: a suppression nobody revisits is a disabled scanner -------------
expires="$(sed -n 's/^#[[:space:]]*EXPIRES:[[:space:]]*\([0-9-]\{10\}\).*/\1/p' "$IGNORE_FILE" | head -1)"
if [[ -z "$expires" ]]; then
  echo "FAIL: $IGNORE_FILE has no '# EXPIRES: YYYY-MM-DD' line." >&2
  exit 1
fi
if ! date -d "$expires" +%s >/dev/null 2>&1; then
  echo "FAIL: $IGNORE_FILE EXPIRES value is not a valid date: $expires" >&2
  exit 1
fi
if [[ "$(date -u +%Y-%m-%d)" > "$expires" ]]; then
  echo "FAIL: pip-audit suppressions expired on $expires." >&2
  echo "      Re-review $IGNORE_FILE: drop entries whose upstream constraint is" >&2
  echo "      gone, and extend EXPIRES only for those that still hold." >&2
  exit 1
fi

# --- build the flags --------------------------------------------------------
args=()
ids=()
while IFS= read -r line; do
  line="${line%%#*}"                       # strip trailing comments
  line="${line#"${line%%[![:space:]]*}"}"  # ltrim
  line="${line%"${line##*[![:space:]]}"}"  # rtrim
  [[ -z "$line" ]] && continue
  ids+=("$line")
  args+=(--ignore-vuln "$line")
done <"$IGNORE_FILE"

if [[ ${#ids[@]} -eq 0 ]]; then
  echo "pip-audit: no suppressions active (list empty)"
else
  # Print the waiver into the CI log: a suppressed finding a reader cannot see
  # is the failure mode this whole arrangement exists to avoid.
  echo "pip-audit: ${#ids[@]} reviewed suppression(s), expiring $expires:"
  printf '  %s\n' "${ids[@]}"
  echo "  rationale: $IGNORE_FILE"
fi

echo "+ pip-audit $* ${args[*]}"
exec pip-audit "$@" "${args[@]}"
