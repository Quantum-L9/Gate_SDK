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
SRC_ROOT = REPO_ROOT / "src" / "constellation_node_sdk"

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


# ---------------------------------------------------------------------------
# Gate-authority dispatch: the privileged surface must stay privileged
# ---------------------------------------------------------------------------
#
# Adding a Gate→worker transport is where an SDK grows a general peer client by
# accident. These guard the boundary structurally, so the regression is caught
# by the suite rather than by a reviewer noticing a new parameter.

GENERIC_PEER_SURFACE_NAMES = {
    "send_to_url",
    "send_to_peer",
    "post_packet",
    "execute_peer",
    "send_to_node",
    "dispatch_to_url",
}

PEER_ROUTING_PARAMETERS = {
    "peer_url",
    "destination_url",
    "worker_url",
    "url",
    "endpoint",
    "destination_node",
}


def _public_callables(module: object) -> dict[str, object]:
    return {
        name: getattr(module, name)
        for name in getattr(module, "__all__", [])
        if callable(getattr(module, name, None))
    }


def test_no_generic_peer_surface_exists_anywhere_public() -> None:
    """
    No public API may offer "post this packet to this URL".

    That shape is the whole risk: it would let any node reach any node, which is
    what Gate-only egress exists to prevent.
    """
    import constellation_node_sdk as sdk
    import constellation_node_sdk.gate as gate_package
    from constellation_node_sdk import gate_authority

    for module in (sdk, gate_package, gate_authority):
        exported = set(getattr(module, "__all__", []))
        offending = exported & GENERIC_PEER_SURFACE_NAMES
        assert not offending, f"{module.__name__} exports {sorted(offending)}"


def test_the_dispatch_surface_is_absent_from_application_namespaces() -> None:
    """An application never encounters the Gate-only surface by importing normally."""
    import constellation_node_sdk as sdk
    import constellation_node_sdk.gate as gate_package

    privileged = {"GateDispatchTransport", "GateDispatchTransportConfig"}
    for module in (sdk, gate_package):
        assert not (privileged & set(getattr(module, "__all__", [])))
        for name in privileged:
            assert not hasattr(module, name), f"{module.__name__} leaks {name}"


def test_gate_client_takes_no_routing_parameter_on_any_public_method() -> None:
    """Re-asserted after the dispatch surface landed: the application client is unchanged."""
    for name in dir(GateClient):
        if name.startswith("_"):
            continue
        member = getattr(GateClient, name)
        if not callable(member):
            continue
        parameters = set(inspect.signature(member).parameters)
        assert not (parameters & PEER_ROUTING_PARAMETERS), name


def test_the_dispatch_surface_requires_a_gate_authored_packet() -> None:
    """
    The authority check is mechanical and reachable, not documentary.

    A guard that only read the docstring would pass a version that had lost the
    check, so this asserts the validator exists and is wired into the send path.
    """
    import inspect as _inspect

    from constellation_node_sdk.gate_authority.dispatch import GateDispatchTransport

    assert hasattr(GateDispatchTransport, "_assert_gate_authored")
    send_source = _inspect.getsource(GateDispatchTransport.send_gate_authored_packet)
    assert "_assert_gate_authored" in send_source

    # It reuses the worker's own ingress law rather than restating it.
    authority_source = _inspect.getsource(GateDispatchTransport._assert_gate_authored)
    assert "validate_execute_ingress_packet" in authority_source


def test_the_dispatch_surface_does_not_route() -> None:
    """
    Gate selects the worker. The SDK must not learn how.

    Checked against executable code rather than raw text: this module's prose
    legitimately discusses Gate's registry in order to say the SDK does not own
    it, and a substring scan would flag that explanation as the violation.
    """
    tree = ast.parse((SRC_ROOT / "gate_authority" / "dispatch.py").read_text(encoding="utf-8"))

    identifiers: set[str] = set()
    imported_from: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_from.add(node.module)
        elif isinstance(node, ast.Import):
            imported_from.update(alias.name for alias in node.names)

    routing_constructs = {
        "NodeRegistry",
        "RouteResolver",
        "resolve",
        "registry",
        "select_worker",
        "choose_node",
    }
    offending = identifiers & routing_constructs
    assert not offending, f"dispatch.py performs routing via {sorted(offending)}"

    # Nor may it reach into Gate for that knowledge.
    assert not any(module.startswith("constellation_gate") for module in imported_from), (
        "the SDK must not import Constellation.Gate"
    )


def test_the_dispatch_surface_owns_the_execution_endpoint() -> None:
    """The worker execution path is fixed by the SDK, never supplied by the caller."""
    from constellation_node_sdk.gate_authority.dispatch import (
        _WORKER_EXECUTE_PATH,
        GateDispatchTransport,
    )

    assert _WORKER_EXECUTE_PATH == "/v1/execute"
    parameters = set(inspect.signature(GateDispatchTransport.send_gate_authored_packet).parameters)
    assert "worker_base_url" in parameters
    # A full endpoint parameter would make the path a registry value.
    assert not (parameters & {"url", "endpoint", "worker_url", "execute_url"})


def test_the_dispatch_surface_has_no_retry_or_timeout_knob() -> None:
    """One attempt, and one deadline that comes from the packet."""
    from constellation_node_sdk.gate_authority import GateDispatchTransportConfig
    from constellation_node_sdk.gate_authority.dispatch import GateDispatchTransport

    fields = set(GateDispatchTransportConfig.model_fields)
    assert not {f for f in fields if "retr" in f.lower() or "timeout" in f.lower()}

    parameters = set(inspect.signature(GateDispatchTransport.send_gate_authored_packet).parameters)
    assert not (parameters & {"timeout", "timeout_ms", "timeout_seconds", "retries", "deadline"})


def test_one_canonical_packet_http_implementation() -> None:
    """
    Both sides of Gate route their POST through one implementation.

    Two copies drift, and the one that drifts is whichever is exercised less.
    The check is that each caller *uses* the shared helper and that the helper
    is defined exactly once — not that neither file mentions httpx, since both
    legitimately do (a health GET on one side, a managed client on the other).
    """
    callers = {
        "gate/client.py": SRC_ROOT / "gate" / "client.py",
        "gate_authority/dispatch.py": SRC_ROOT / "gate_authority" / "dispatch.py",
    }
    for who, path in callers.items():
        assert "post_packet_json" in path.read_text(encoding="utf-8"), (
            f"{who} does not route its POST through the shared machinery"
        )

    definitions = [
        path
        for path in SRC_ROOT.rglob("*.py")
        if "async def post_packet_json" in path.read_text(encoding="utf-8")
    ]
    assert len(definitions) == 1, f"canonical POST defined in {len(definitions)} places"
    assert definitions[0].name == "_packet_http.py"
