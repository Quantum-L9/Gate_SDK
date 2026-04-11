from __future__ import annotations

import pytest

from constellation_node_sdk.transport.provenance import RoutingProvenance


def test_routing_provenance_accepts_node_origin() -> None:
    provenance = RoutingProvenance(
        origin_kind="node",
        requested_action="enrich",
        resolved_by_gate=False,
        original_source_node="orchestrator",
    )

    assert provenance.origin_kind == "node"
    assert provenance.requested_action == "enrich"
    assert provenance.original_source_node == "orchestrator"


def test_routing_provenance_accepts_client_origin() -> None:
    provenance = RoutingProvenance(
        origin_kind="client",
        requested_action="score",
        resolved_by_gate=True,
        original_source_node="client",
    )

    assert provenance.origin_kind == "client"
    assert provenance.resolved_by_gate is True


def test_routing_provenance_rejects_invalid_origin_kind() -> None:
    with pytest.raises(ValueError):
        RoutingProvenance(
            origin_kind="invalid",
            requested_action="enrich",
            resolved_by_gate=False,
            original_source_node="x",
        )


def test_routing_provenance_rejects_empty_action() -> None:
    with pytest.raises(ValueError):
        RoutingProvenance(
            origin_kind="node",
            requested_action="   ",
            resolved_by_gate=False,
            original_source_node="orchestrator",
        )
