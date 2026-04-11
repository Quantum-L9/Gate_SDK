from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    contracts_dir = repo_root / "contracts"
    examples_dir = repo_root / "examples" / "packets"

    required_contracts = [
        contracts_dir / "transport-packet.schema.json",
        contracts_dir / "TRANSPORT_PACKET_SPEC.md",
        contracts_dir / "NODE_REGISTRATION_SPEC.md",
        contracts_dir / "ROUTING_POLICY_SPEC.md",
    ]
    required_examples = [
        examples_dir / "simple_request.json",
        examples_dir / "orchestrated_request.json",
        examples_dir / "replay_request.json",
    ]

    missing = [path for path in (*required_contracts, *required_examples) if not path.exists()]
    if missing:
        for path in missing:
            print(f"missing: {path}")
        return 1

    schema_path = contracts_dir / "transport-packet.schema.json"
    try:
        json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"invalid schema JSON: {exc}")
        return 1

    for example_path in required_examples:
        try:
            json.loads(example_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"invalid example JSON in {example_path.name}: {exc}")
            return 1

    print("contracts validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
