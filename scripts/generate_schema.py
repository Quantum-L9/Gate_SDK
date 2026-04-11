from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "contracts" / "transport-packet.schema.json"

    if not schema_path.exists():
        print(f"schema file does not exist: {schema_path}")
        return 1

    parsed = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    print(f"normalized schema: {schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
