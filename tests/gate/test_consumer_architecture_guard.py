"""
Consumer-facing architecture drift guard (ADR-SDK-013).

The failure mode this guards is not a bug, it is a slow regression: examples and
docs that teach the packet-native API as the normal integration, until every new
consumer has hand-rolled its own transport adapter again.

The guard is against the *consumer-facing* surface. The SDK's own internals
obviously construct packets, sign, and speak HTTP — that is its job.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from constellation_node_sdk.gate.client import GateClient

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"

# The mechanics a closed abstraction means an application never performs itself.
CONSUMER_FORBIDDEN_CALLS = {
    "create_transport_packet",
    "sign_transport_packet",
    "validate_transport_packet",
    "compute_payload_hash",
    "compute_transport_hash",
}


def _application_example_files() -> list[Path]:
    """
    Example files that stand in for an application calling Gate.

    Node *runtime* examples (worker/orchestrator apps) are node-side: they
    receive packets and are legitimately packet-native. The guard targets the
    outbound application path.
    """
    return sorted(EXAMPLES.rglob("*_client.py")) + sorted(EXAMPLES.rglob("app_client*.py"))


def test_execute_is_reachable_from_the_package_root() -> None:
    """A consumer finds the application surface without spelunking submodules."""
    import constellation_node_sdk as sdk

    assert "GateClient" in sdk.__all__
    assert callable(sdk.GateClient.execute)


def test_execute_takes_business_inputs_only() -> None:
    """
    The application API's parameters are business concerns, not transport mechanics.

    A new transport parameter appearing here is the drift: it means the SDK has
    started asking the application to coordinate something the SDK owns.
    """
    parameters = set(inspect.signature(GateClient.execute).parameters) - {"self"}
    assert parameters == {
        "action",
        "payload",
        "tenant",
        "idempotency_key",
        "timeout_ms",
        "correlation_id",
        "trace_id",
        "classification",
        "compliance_tags",
        "retention_days",
        "priority",
    }


def test_execute_accepts_no_packet_argument() -> None:
    """If the application still has to build a packet, nothing was closed."""
    signature = inspect.signature(GateClient.execute)
    for name, parameter in signature.parameters.items():
        annotation = str(parameter.annotation)
        assert "TransportPacket" not in annotation, name


def test_the_client_exposes_no_peer_or_url_entry_point() -> None:
    """
    Gate-only egress: no public method takes a destination the caller chose.

    ``gate_url`` is read-only configuration, not a per-call argument.
    """
    for name in dir(GateClient):
        if name.startswith("_"):
            continue
        member = getattr(GateClient, name)
        if not callable(member):
            continue
        parameters = set(inspect.signature(member).parameters)
        assert not (parameters & {"url", "peer_url", "worker_url", "destination_node", "endpoint"})


def test_the_transport_seam_is_keyword_only_and_optional() -> None:
    """
    The httpx seam exists for tests and must never look like a routing argument.

    Keyword-only and defaulted means no consumer reaches it by accident, and it
    carries no URL, so it cannot become a peer-dispatch backdoor.
    """
    parameter = inspect.signature(GateClient.__init__).parameters["transport"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


@pytest.mark.parametrize("example", _application_example_files(), ids=lambda p: p.name)
def test_application_examples_do_not_teach_transport_mechanics(example: Path) -> None:
    """
    An example that hand-builds a packet teaches the next consumer to do the same.

    Examples are how an integration gets written; this is where a shadow SDK
    starts.
    """
    tree = ast.parse(example.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & CONSUMER_FORBIDDEN_CALLS), (
        f"{example.name} performs transport mechanics an application should not: "
        f"{sorted(called & CONSUMER_FORBIDDEN_CALLS)}"
    )


def test_at_least_one_application_example_exists() -> None:
    """
    The guard above is vacuous with nothing to check.

    A closed abstraction has to be demonstrated somewhere a consumer will look.
    """
    assert _application_example_files(), "no application client example to guard"
