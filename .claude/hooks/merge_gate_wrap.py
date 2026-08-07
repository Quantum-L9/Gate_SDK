#!/usr/bin/env python3
"""Thin Claude PreToolUse wrapper → ops/autonomy/merge_gate.py (§2.1)."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

GATE = Path.home() / ".cursor-governance" / "ops" / "autonomy" / "merge_gate.py"


def main() -> int:
    if not GATE.is_file():
        # Fail-open: do not brick sessions if governance clone is absent.
        print("merge_gate_wrap: ops merge_gate missing; skip", file=sys.stderr)
        return 0
    sys.argv = [str(GATE)]
    runpy.run_path(str(GATE), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
