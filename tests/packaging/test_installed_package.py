"""
Track A — the built distribution must behave like the source checkout.

A repo-root ``pytest`` run imports ``src/``. Every consuming repository
imports a wheel. These tests close that gap: the artifact is built with the
project's own build configuration, installed into an empty directory, and
exercised there — public imports, packet creation, the serialization
boundary, handler dispatch, and response construction — with the checkout
explicitly removed from the child interpreter's import path.
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any, Protocol

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_SRC = REPO_ROOT / "src"
DISTRIBUTION_NAME = "constellation_node_sdk"


class InstalledSdk(Protocol):
    """The wheel-under-test, as provided by the ``installed_sdk`` fixture.

    Declared structurally rather than imported: ``tests/`` is not a package,
    so a sibling import of ``conftest`` would not resolve, and making
    ``tests/packaging`` one would shadow the third-party ``packaging``
    distribution.
    """

    wheel_path: Path
    install_dir: Path

    def run(self, code: str) -> dict[str, Any]: ...


_MATRIX = json.loads(
    (REPO_ROOT / "tests" / "contracts" / "release_set_api_matrix.json").read_text(encoding="utf-8")
)
_CONSUMERS: dict[str, Any] = _MATRIX["consumers"]

# Third-party top-level modules the packaged code imports. Anything here that
# is not covered by the wheel's own Requires-Dist is an installation that
# succeeds and then fails at first import inside a consumer.
_STDLIB_AND_SELF_EXEMPT = {DISTRIBUTION_NAME}


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Build + install
# ---------------------------------------------------------------------------


def test_wheel_builds_and_matches_the_project_metadata(installed_sdk: InstalledSdk) -> None:
    """The artifact under test is this project's wheel, at this project's version."""
    project = _pyproject()["project"]
    expected_version = project["version"]

    assert installed_sdk.wheel_path.is_file()
    assert installed_sdk.wheel_path.name.startswith(f"{DISTRIBUTION_NAME}-{expected_version}-")
    assert installed_sdk.wheel_path.suffix == ".whl"


def test_clean_install_places_the_package_in_an_empty_directory(
    installed_sdk: InstalledSdk,
) -> None:
    """A no-dependency install into a fresh directory produces an importable package."""
    package_root = installed_sdk.install_dir / DISTRIBUTION_NAME
    assert package_root.is_dir()
    assert (package_root / "__init__.py").is_file()

    dist_info = sorted(installed_sdk.install_dir.glob(f"{DISTRIBUTION_NAME}-*.dist-info"))
    assert len(dist_info) == 1, f"expected one dist-info, found {[d.name for d in dist_info]}"


def test_installed_package_is_not_the_source_checkout(installed_sdk: InstalledSdk) -> None:
    """
    The harness proves what it claims to prove.

    Without this the whole track could pass while quietly importing ``src/``,
    which is exactly the failure mode being tested for.
    """
    result = installed_sdk.run(
        """
emit({"file": str(pathlib.Path(_sdk.__file__).resolve())})
"""
    )
    resolved = Path(str(result["file"]))
    assert installed_sdk.install_dir in resolved.parents
    assert REPO_SRC not in resolved.parents


# ---------------------------------------------------------------------------
# Public API surface, driven by the release-set matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("consumer", sorted(_CONSUMERS))
def test_installed_wheel_exposes_the_api_a_consumer_imports(
    consumer: str, installed_sdk: InstalledSdk
) -> None:
    """
    Every import a release-set repository performs works from the wheel.

    The expectations come from ``release_set_api_matrix.json``, so the source
    checkout and the distribution are held to one list rather than two.
    """
    expected = _CONSUMERS[consumer]["imports"]
    result = installed_sdk.run(
        f"""
import importlib
expected = {expected!r}
missing = []
for entry in expected:
    module = importlib.import_module(entry["module"])
    for symbol in entry["symbols"]:
        if not hasattr(module, symbol):
            missing.append(entry["module"] + "." + symbol)
emit({{"missing": missing, "checked": sum(len(e["symbols"]) for e in expected)}})
"""
    )
    assert result["missing"] == [], (
        f"{consumer} imports these from the installed wheel, which does not export them: "
        f"{result['missing']}"
    )
    assert result["checked"] > 0


# ---------------------------------------------------------------------------
# Behavior from the installed wheel
# ---------------------------------------------------------------------------


def test_installed_wheel_creates_a_canonical_packet(installed_sdk: InstalledSdk) -> None:
    """Packet creation from the distribution mints the same canonical shape."""
    result = installed_sdk.run(
        """
from constellation_node_sdk import create_transport_packet

packet = create_transport_packet(
    action="converge",
    payload={"entity": {"id": "acme-1"}, "objective": "enrich"},
    tenant="test-org",
    source_node="odoo",
    destination_node="gate",
    reply_to="odoo",
    idempotency_key="idem-installed-001",
    correlation_id="corr-installed-001",
    timeout_ms=45_000,
)
emit({
    "action": packet.header.action,
    "payload": packet.payload,
    "idempotency_key": packet.header.idempotency_key,
    "correlation_id": packet.header.correlation_id,
    "timeout_ms": packet.header.timeout_ms,
    "causation_id": packet.header.causation_id,
    "generation": packet.lineage.generation,
    "root_is_self": str(packet.lineage.root_id) == str(packet.header.packet_id),
    "payload_hash_len": len(packet.security.payload_hash),
})
"""
    )
    assert result["action"] == "converge"
    assert result["payload"] == {"entity": {"id": "acme-1"}, "objective": "enrich"}
    assert result["idempotency_key"] == "idem-installed-001"
    assert result["correlation_id"] == "corr-installed-001"
    assert result["timeout_ms"] == 45_000
    assert result["causation_id"] is None
    assert result["generation"] == 0
    assert result["root_is_self"] is True
    assert result["payload_hash_len"] == 64


def test_installed_wheel_round_trips_a_packet_across_the_wire(
    installed_sdk: InstalledSdk,
) -> None:
    """Serialize and parse from the distribution reconstructs an identical packet."""
    result = installed_sdk.run(
        """
from constellation_node_sdk import TransportPacket, create_transport_packet

original = create_transport_packet(
    action="converge",
    payload={"entity": {"id": "acme-1", "website": None}, "max_variations": 5},
    tenant="test-org",
    source_node="odoo",
    destination_node="gate",
    reply_to="odoo",
    idempotency_key="idem-installed-002",
    timeout_ms=30_000,
)
wire = json.loads(json.dumps(original.model_dump_json_dict()))
parsed = TransportPacket.model_validate(wire)

emit({
    "payload_identical": parsed.payload == original.payload,
    "packet_id_identical": str(parsed.header.packet_id) == str(original.header.packet_id),
    "idempotency_key": parsed.header.idempotency_key,
    "timeout_ms": parsed.header.timeout_ms,
    "transport_hash_identical": parsed.security.transport_hash == original.security.transport_hash,
    "null_field_preserved": "website" in parsed.payload["entity"],
})
"""
    )
    assert result["payload_identical"] is True
    assert result["packet_id_identical"] is True
    assert result["idempotency_key"] == "idem-installed-002"
    assert result["timeout_ms"] == 30_000
    assert result["transport_hash_identical"] is True
    assert result["null_field_preserved"] is True


def test_installed_wheel_dispatches_to_a_registered_handler(installed_sdk: InstalledSdk) -> None:
    """
    Handler registration, action dispatch, and response construction all work
    from the distribution — the three runtime APIs EIE depends on.
    """
    result = installed_sdk.run(
        """
import asyncio
from constellation_node_sdk import create_transport_packet, execute_transport_packet
from constellation_node_sdk.runtime.handlers import clear_handlers, register_handler

seen = {}

async def converge(org_id, payload):
    seen["org_id"] = org_id
    seen["payload"] = payload
    return {"state": "completed", "fields": {"website": "https://example.com"}}

clear_handlers()
register_handler("converge", converge)

request = create_transport_packet(
    action="converge",
    payload={"entity": {"id": "acme-1"}, "objective": "enrich"},
    tenant="test-org",
    source_node="gate",
    destination_node="worker",
    reply_to="gate",
    idempotency_key="idem-installed-003",
    timeout_ms=5_000,
)
response = asyncio.run(
    execute_transport_packet(request, node_name="worker", dev_mode=True)
)

emit({
    "handler_org_id": seen.get("org_id"),
    "handler_payload": seen.get("payload"),
    "response_payload": response.payload,
    "response_packet_type": response.header.packet_type,
    "response_action": response.header.action,
    "correlation_preserved": response.header.correlation_id == request.header.correlation_id,
    "causation_is_request": str(response.header.causation_id) != str(request.header.packet_id),
    "idempotency_key": response.header.idempotency_key,
    "destination": response.address.destination_node,
})
"""
    )
    assert result["handler_org_id"] == "test-org"
    assert result["handler_payload"] == {"entity": {"id": "acme-1"}, "objective": "enrich"}
    assert result["response_payload"] == {
        "state": "completed",
        "fields": {"website": "https://example.com"},
    }
    assert result["response_packet_type"] == "response"
    assert result["response_action"] == "converge"
    assert result["correlation_preserved"] is True
    assert result["idempotency_key"] == "idem-installed-003"
    assert result["destination"] == "gate"


def test_installed_wheel_builds_a_node_app(installed_sdk: InstalledSdk) -> None:
    """EIE creates its FastAPI application through the SDK; prove that from the wheel."""
    result = installed_sdk.run(
        """
from constellation_node_sdk import NodeRuntimeConfig, create_node_app

config = NodeRuntimeConfig(
    environment="local",
    node_name="worker",
    service_name="worker-service",
    service_version="0.0.1",
    dev_mode=True,
)
app = create_node_app(config=config, auto_register_with_gate=False)
routes = sorted(getattr(route, "path", "") for route in app.routes)
emit({"routes": routes, "title": app.title})
"""
    )
    assert "/v1/execute" in result["routes"]
    assert "/v1/health" in result["routes"]
    assert result["title"] == "worker-service"


# ---------------------------------------------------------------------------
# Packaging completeness
# ---------------------------------------------------------------------------


def test_wheel_ships_every_module_in_the_source_tree(installed_sdk: InstalledSdk) -> None:
    """
    A module present in ``src/`` but absent from the wheel is invisible until
    a consumer imports it. Compare the two sets directly.
    """
    source_modules = {
        str(path.relative_to(REPO_SRC)).replace("\\", "/")
        for path in (REPO_SRC / DISTRIBUTION_NAME).rglob("*.py")
    }
    with zipfile.ZipFile(installed_sdk.wheel_path) as archive:
        packaged_modules = {
            name
            for name in archive.namelist()
            if name.startswith(f"{DISTRIBUTION_NAME}/") and name.endswith(".py")
        }

    assert source_modules, "no source modules discovered — the comparison would be vacuous"
    assert source_modules == packaged_modules, (
        "wheel contents diverge from the source tree; "
        f"missing from wheel: {sorted(source_modules - packaged_modules)}; "
        f"unexpected in wheel: {sorted(packaged_modules - source_modules)}"
    )


def test_wheel_ships_the_typing_marker(installed_sdk: InstalledSdk) -> None:
    """``py.typed`` must survive packaging or consumers lose SDK type information."""
    assert (REPO_SRC / DISTRIBUTION_NAME / "py.typed").is_file()
    with zipfile.ZipFile(installed_sdk.wheel_path) as archive:
        assert f"{DISTRIBUTION_NAME}/py.typed" in archive.namelist()


def test_wheel_metadata_declares_every_third_party_import(installed_sdk: InstalledSdk) -> None:
    """
    Every third-party module the packaged code imports is covered by a
    ``Requires-Dist`` entry.

    A gap here installs cleanly and then raises ``ModuleNotFoundError`` at
    first use inside a consumer, which is the packaging failure this track
    exists to catch.
    """
    result = installed_sdk.run(
        f"""
import ast, pathlib, sys

package_root = _INSTALL_DIR / "{DISTRIBUTION_NAME}"
imported = set()
for module_path in package_root.rglob("*.py"):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

third_party = sorted(name for name in imported if name not in sys.stdlib_module_names)
emit({{"third_party": third_party}})
"""
    )
    third_party = {str(name) for name in result["third_party"]} - _STDLIB_AND_SELF_EXEMPT

    # Distribution names differ from import names; map the ones this project uses.
    import_to_distribution = {
        "fastapi": "fastapi",
        "starlette": "fastapi",
        "pydantic": "pydantic",
        "httpx": "httpx",
        "cryptography": "cryptography",
        "prometheus_client": "prometheus-client",
        "pythonjsonlogger": "python-json-logger",
        "yaml": "pyyaml",
        "uvicorn": "uvicorn",
    }

    declared = {
        requirement.split(">=")[0].split("[")[0].split("==")[0].strip().lower()
        for requirement in _pyproject()["project"]["dependencies"]
    }

    unmapped = sorted(name for name in third_party if name not in import_to_distribution)
    assert not unmapped, (
        "the packaged code imports third-party modules this test does not know how to map to a "
        f"distribution: {unmapped}. Add the mapping and confirm the dependency is declared."
    )

    undeclared = sorted(
        name for name in third_party if import_to_distribution[name].lower() not in declared
    )
    assert not undeclared, (
        f"packaged code imports {undeclared} but pyproject declares no matching dependency"
    )


# ---------------------------------------------------------------------------
# Transport closure from the installed wheel
# ---------------------------------------------------------------------------
#
# The matrix above transcribes what consumers call *today*. The closure surface
# is what lets them stop hand-rolling transport, so it needs its own proof from
# the distribution: an export that only exists in the checkout is a capability
# no consumer can actually adopt.


def test_installed_wheel_exposes_the_application_closure_surface(
    installed_sdk: InstalledSdk,
) -> None:
    """The closure API a consumer adopts must be importable from the wheel."""
    result = installed_sdk.run(
        """
import constellation_node_sdk as sdk

expected = [
    "GateClient",
    "GateClientConfig",
    "GateClientError",
    "GateConfigurationError",
    "GateConnectionError",
    "GateHTTPError",
    "GatePolicyError",
    "GateResponseError",
    "GateSecurityError",
    "GateTimeoutError",
    "NodeRegistration",
    "register_node",
]
emit({
    "missing": [name for name in expected if not hasattr(sdk, name)],
    "unexported": [name for name in expected if name not in sdk.__all__],
    "has_execute": hasattr(sdk.GateClient, "execute"),
})
"""
    )
    assert result["missing"] == []
    assert result["unexported"] == []
    assert result["has_execute"] is True


def test_installed_wheel_execute_builds_a_gate_bound_packet(
    installed_sdk: InstalledSdk,
) -> None:
    """
    ``execute()`` from the distribution produces the same Gate-bound packet.

    Driven through a stub transport so the assertion is about the packet the
    wheel actually puts on the wire, not about reaching a network.
    """
    result = installed_sdk.run(
        """
import asyncio, json
import httpx
from constellation_node_sdk import GateClient, GateClientConfig
from constellation_node_sdk.transport.packet import TransportPacket


class Stub(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request):
        request.read()
        self.requests.append(request)
        sent = TransportPacket.model_validate(json.loads(request.content.decode("utf-8")))
        response = sent.derive(
            packet_type="response",
            source_node="gate",
            destination_node=sent.address.reply_to,
            reply_to="gate",
            payload={"state": "completed"},
        )
        return httpx.Response(200, json=response.model_dump_json_dict())


async def main():
    stub = Stub()
    client = GateClient(
        GateClientConfig(gate_url="http://gate:8000", local_node="odoo", timeout_seconds=30.0),
        transport=stub,
    )
    response = await client.execute(
        action="converge",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        idempotency_key="odoo:enrichment:run-1",
        timeout_ms=7000,
    )
    sent = json.loads(stub.requests[0].content.decode("utf-8"))
    emit({
        "state": response.payload["state"],
        "destination": sent["address"]["destination_node"],
        "source": sent["address"]["source_node"],
        "idempotency_key": sent["header"]["idempotency_key"],
        "packet_timeout_ms": sent["header"]["timeout_ms"],
        "applied_read_timeout": stub.requests[0].extensions["timeout"]["read"],
        "attempts": len(stub.requests),
    })


asyncio.run(main())
"""
    )
    assert result["state"] == "completed"
    assert result["destination"] == "gate"
    assert result["source"] == "odoo"
    assert result["idempotency_key"] == "odoo:enrichment:run-1"
    # One deadline: the caller's budget reached both the header and the socket.
    assert result["packet_timeout_ms"] == 7000
    assert result["applied_read_timeout"] == 7.0
    # No hidden retry survived packaging.
    assert result["attempts"] == 1


def test_installed_wheel_raises_typed_transport_failures(
    installed_sdk: InstalledSdk,
) -> None:
    """
    Failure classification works from the distribution without importing httpx.

    A taxonomy that only holds in the checkout leaves consumers back on string
    matching, which is the defect it exists to remove.
    """
    result = installed_sdk.run(
        """
import asyncio
import httpx
from constellation_node_sdk import (
    GateClient,
    GateClientConfig,
    GateClientError,
    GateConnectionError,
    GateHTTPError,
    GateTimeoutError,
)


def stub_transport(responder):
    class Stub(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            request.read()
            return responder()

    return Stub()


async def main():
    cases = {
        "timeout": lambda: (_ for _ in ()).throw(httpx.ConnectTimeout("")),
        "connection": lambda: (_ for _ in ()).throw(httpx.ConnectError("")),
        "server": lambda: httpx.Response(503, json={}),
        "client": lambda: httpx.Response(403, json={}),
        "noncanonical": lambda: httpx.Response(200, json={"state": "x"}),
    }
    observed = {}
    blank_reasons = []
    for label, responder in cases.items():
        client = GateClient(
            GateClientConfig(gate_url="http://gate:8000", local_node="odoo"),
            transport=stub_transport(responder),
        )
        try:
            await client.execute(action="converge", payload={}, tenant="tenant-a")
            observed[label] = "no-error"
        except GateClientError as exc:
            if isinstance(exc, GateTimeoutError):
                observed[label] = "timeout"
            elif isinstance(exc, GateConnectionError):
                observed[label] = "connection"
            elif isinstance(exc, GateHTTPError):
                observed[label] = "server" if exc.is_server_error else "client"
            else:
                observed[label] = "other"
            if not str(exc).strip():
                blank_reasons.append(label)
    emit({"observed": observed, "blank_reasons": blank_reasons})


asyncio.run(main())
"""
    )
    assert result["observed"] == {
        "timeout": "timeout",
        "connection": "connection",
        "server": "server",
        "client": "client",
        "noncanonical": "other",
    }
    # httpx timeout exceptions stringify to "" — no typed failure may inherit that.
    assert result["blank_reasons"] == []


def test_installed_wheel_renders_a_registration_with_owner(
    installed_sdk: InstalledSdk,
) -> None:
    """
    Registration metadata a node needs is expressible from the distribution.

    ``metadata.owner`` is what Gate reads to resolve a canonical action's owner,
    and its absence is what drove a node to write its own admin HTTP client.
    """
    result = installed_sdk.run(
        """
from constellation_node_sdk import NodeRegistration

body = NodeRegistration(
    node_name="enrichment-engine",
    internal_url="http://enrichment-engine:8000",
    supported_actions=("converge", "graph-inference-result"),
    health_endpoint="/api/v1/health",
    version="2.3.0",
    node_type="enrichment",
    owner="eie",
).to_payload()["enrichment-engine"]

emit({"body": body, "keys": sorted(body)})
"""
    )
    body = result["body"]
    assert body["metadata"]["owner"] == "eie"
    assert body["health_endpoint"] == "/api/v1/health"
    assert body["supported_actions"] == ["converge", "graph-inference-result"]
    # Gate's registration schema forbids extra keys.
    assert result["keys"] == [
        "health_endpoint",
        "internal_url",
        "max_concurrent",
        "metadata",
        "priority_class",
        "supported_actions",
        "timeout_ms",
    ]


# ---------------------------------------------------------------------------
# Gate-authority worker transport from the installed wheel
# ---------------------------------------------------------------------------
#
# Constellation.Gate consumes this from a published artifact, not from a
# checkout. A capability that only exists in src/ is one Gate cannot adopt.


def test_installed_wheel_ships_the_gate_authority_namespace(
    installed_sdk: InstalledSdk,
) -> None:
    """The privileged surface ships, and stays out of the application namespaces."""
    result = installed_sdk.run(
        """
import constellation_node_sdk as sdk
import constellation_node_sdk.gate as gate_package
from constellation_node_sdk import gate_authority

expected = [
    "GateDispatchTransport",
    "GateDispatchTransportConfig",
    "GateDispatchError",
    "GateDispatchAuthorityError",
    "GateDispatchConfigurationError",
    "GateDispatchSecurityError",
    "WorkerConnectionError",
    "WorkerHTTPError",
    "WorkerResponseError",
    "WorkerTimeoutError",
]
privileged = {"GateDispatchTransport", "GateDispatchTransportConfig"}
emit({
    "missing": [n for n in expected if not hasattr(gate_authority, n)],
    "unexported": [n for n in expected if n not in gate_authority.__all__],
    "leaked_to_root": sorted(privileged & set(sdk.__all__)),
    "leaked_to_gate": sorted(privileged & set(gate_package.__all__)),
    "has_send": hasattr(gate_authority.GateDispatchTransport, "send_gate_authored_packet"),
})
"""
    )
    assert result["missing"] == []
    assert result["unexported"] == []
    assert result["leaked_to_root"] == []
    assert result["leaked_to_gate"] == []
    assert result["has_send"] is True


def test_installed_wheel_dispatches_to_a_real_worker(installed_sdk: InstalledSdk) -> None:
    """
    The full Gate→worker rail from the distribution, through the real runtime.

    Proves the deadline closure survives packaging: the remaining budget Gate
    derived reaches the packet header, the socket, and the worker's handler as
    one number.
    """
    result = installed_sdk.run(
        """
import asyncio, inspect, json
import httpx
from constellation_node_sdk.gate_authority import (
    GateDispatchTransport,
    GateDispatchTransportConfig,
)
from constellation_node_sdk.runtime.execution import execute_transport_packet
from constellation_node_sdk.runtime.handlers import register_handler, clear_handlers
from constellation_node_sdk.transport.hop_trace import make_dispatch_hop, make_ingress_hop
from constellation_node_sdk.transport.packet import TransportPacket, create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance

WORKER = "enrichment-engine"


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    async def handle_async_request(self, request):
        request.read()
        self.requests.append(request)
        result = self.responder(request, len(self.requests))
        return await result if inspect.isawaitable(result) else result


async def worker(request, _attempt):
    packet = TransportPacket.model_validate(json.loads(request.content.decode()))
    response = await execute_transport_packet(packet, node_name=WORKER, dev_mode=True)
    return httpx.Response(200, json=response.model_dump_json_dict())


async def main():
    clear_handlers()

    @register_handler("converge")
    async def handle(_org_id, payload):
        return {"state": "completed", "run_id": payload["run_id"]}

    root = create_transport_packet(
        action="converge",
        payload={"entity_id": "42", "run_id": "run-1"},
        tenant="tenant-a",
        source_node="odoo",
        destination_node="gate",
        reply_to="odoo",
        timeout_ms=30000,
        idempotency_key="odoo:enrichment:run-1",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    observed = root.with_hop(
        make_ingress_hop(packet=root, node="gate", action="converge", status="validated")
    )
    base = observed.derive(
        packet_type=observed.header.packet_type,
        action="converge",
        source_node="gate",
        destination_node=WORKER,
        reply_to="gate",
        payload=dict(observed.payload),
        timeout_ms=2000,
        provenance=RoutingProvenance(
            origin_kind="gate",
            requested_action="converge",
            resolved_by_gate=True,
            route_kind="external_ingress",
            original_source_node="odoo",
        ),
    )
    dispatch_packet = base.with_hop(
        make_dispatch_hop(
            packet=base, node="gate", action="converge",
            target_node=WORKER, status="delegated",
        )
    )

    budgets = []
    import constellation_node_sdk.runtime.execution as execution

    original_wait_for = execution.asyncio.wait_for

    async def spy(awaitable, timeout=None):
        budgets.append(timeout)
        return await original_wait_for(awaitable, timeout)

    recorder = Recorder(worker)
    execution.asyncio.wait_for = spy
    try:
        transport = GateDispatchTransport(
            GateDispatchTransportConfig(local_gate_node="gate"), transport=recorder
        )
        response = await transport.send_gate_authored_packet(
            packet=dispatch_packet,
            target_node=WORKER,
            worker_base_url="http://enrichment-engine:8000",
        )
    finally:
        execution.asyncio.wait_for = original_wait_for

    sent = json.loads(recorder.requests[0].content.decode())
    emit({
        "state": response.payload["state"],
        "url": str(recorder.requests[0].url),
        "attempts": len(recorder.requests),
        "packet_timeout_ms": sent["header"]["timeout_ms"],
        "socket_read_timeout": recorder.requests[0].extensions["timeout"]["read"],
        "handler_budgets": budgets,
        "response_source": response.address.source_node,
        "response_destination": response.address.destination_node,
        "idempotency_preserved": response.header.idempotency_key
        == dispatch_packet.header.idempotency_key,
    })
    clear_handlers()


asyncio.run(main())
"""
    )
    assert result["state"] == "completed"
    assert result["url"] == "http://enrichment-engine:8000/v1/execute"
    assert result["attempts"] == 1
    # One downstream deadline, from the wheel.
    assert result["packet_timeout_ms"] == 2000
    assert result["socket_read_timeout"] == 2.0
    assert result["handler_budgets"] == [2.0]
    assert result["response_source"] == "enrichment-engine"
    assert result["response_destination"] == "gate"
    assert result["idempotency_preserved"] is True


def test_installed_wheel_refuses_a_non_gate_authored_dispatch(
    installed_sdk: InstalledSdk,
) -> None:
    """The peer-escape guard survives packaging: a worker URL alone is not enough."""
    result = installed_sdk.run(
        """
import asyncio
import httpx
from constellation_node_sdk.gate_authority import (
    GateDispatchAuthorityError,
    GateDispatchTransport,
    GateDispatchTransportConfig,
)
from constellation_node_sdk.transport.packet import create_transport_packet
from constellation_node_sdk.transport.provenance import RoutingProvenance


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request):
        request.read()
        self.requests.append(request)
        return httpx.Response(200, json={})


async def main():
    node_packet = create_transport_packet(
        action="converge",
        payload={"entity_id": "42"},
        tenant="tenant-a",
        source_node="odoo",
        destination_node="enrichment-engine",
        reply_to="odoo",
        provenance=RoutingProvenance(
            origin_kind="node",
            requested_action="converge",
            resolved_by_gate=False,
            original_source_node="odoo",
        ),
    )
    recorder = Recorder()
    transport = GateDispatchTransport(
        GateDispatchTransportConfig(local_gate_node="gate"), transport=recorder
    )
    refused = False
    try:
        await transport.send_gate_authored_packet(
            packet=node_packet,
            target_node="enrichment-engine",
            worker_base_url="http://enrichment-engine:8000",
        )
    except GateDispatchAuthorityError:
        refused = True
    emit({"refused": refused, "requests": len(recorder.requests)})


asyncio.run(main())
"""
    )
    assert result["refused"] is True
    assert result["requests"] == 0
