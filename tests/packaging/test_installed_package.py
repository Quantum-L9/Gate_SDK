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
