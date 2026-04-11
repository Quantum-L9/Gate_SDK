from __future__ import annotations

import pytest

from constellation_node_sdk.gate.registration import (
    build_registration_payload,
    load_node_spec,
)


def test_build_registration_payload_from_valid_spec() -> None:
    spec = {
        "node": {
            "id": "score",
            "actions": ["score"],
            "internal_url": "http://score:8000",
            "priority_class": "P1",
            "max_concurrent": 25,
            "health_endpoint": "/v1/health",
            "timeout_ms": 15000,
            "version": "1.2.3",
            "type": "worker",
        }
    }

    payload = build_registration_payload(spec)

    assert "score" in payload
    node = payload["score"]

    assert node["internal_url"] == "http://score:8000"
    assert node["supported_actions"] == ["score"]
    assert node["priority_class"] == "P1"
    assert node["max_concurrent"] == 25
    assert node["timeout_ms"] == 15000
    assert node["metadata"]["version"] == "1.2.3"


def test_build_registration_payload_requires_node_id() -> None:
    with pytest.raises(ValueError):
        build_registration_payload({"node": {"actions": ["score"]}})


def test_load_node_spec_reads_yaml(tmp_path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "node:\n"
        "  id: enrich\n"
        "  actions:\n"
        "    - enrich\n",
        encoding="utf-8",
    )

    spec = load_node_spec(str(spec_path))
    assert spec["node"]["id"] == "enrich"
