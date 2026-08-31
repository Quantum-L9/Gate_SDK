"""
The whole application-side Gate integration.

This is the complete outbound path for a business application: build a domain
payload, decide what one logical operation is, and make one call. Everything
below the call — packet construction, Gate destination, deadline translation,
transport idempotency, signing, HTTP, response validation, failure typing — is
owned by Gate_SDK and must not be reproduced here.

Run: python examples/application_client/app_client.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from constellation_node_sdk import (
    GateClient,
    GateClientConfig,
    GateClientError,
    GateConnectionError,
    GateHTTPError,
    GateTimeoutError,
)


def build_client() -> GateClient:
    """
    One place where transport configuration lives.

    ``timeout_seconds`` is the default operation budget. It reaches both the
    packet header and the socket, so there is nothing to keep in step by hand.
    """
    return GateClient(
        GateClientConfig(
            gate_url=os.getenv("GATE_URL", "http://gate:8000"),
            local_node=os.getenv("L9_NODE_NAME", "erp"),
            timeout_seconds=30.0,
        )
    )


def build_enrichment_payload(org_id: str) -> dict[str, Any]:
    """
    The application owns its domain payload entirely.

    Gate_SDK transports this dictionary and never interprets, renames, or
    supplements a single key in it.
    """
    return {
        "entity": {"id": org_id, "domain": "example.test"},
        "requested_fields": ["website", "employee_count"],
    }


def logical_operation_id(run_id: str) -> str:
    """
    The application decides what constitutes one logical business operation.

    Gate_SDK places this into the transport header and never substitutes a
    payload hash for it: only the application knows that two structurally
    identical payloads are two different runs, or that one retried run is still
    the same operation.
    """
    return f"erp:enrichment:{run_id}"


async def enrich_organization(client: GateClient, *, org_id: str, run_id: str) -> dict[str, Any]:
    """
    The entire integration: one call, business inputs only.

    Note what is absent — no destination (Gate resolves ownership from the
    action), no packet, no signing call, no HTTP, no ``httpx`` import.
    """
    response = await client.execute(
        action="converge",
        payload=build_enrichment_payload(org_id),
        tenant="tenant-a",
        idempotency_key=logical_operation_id(run_id),
        timeout_ms=25_000,
        correlation_id=run_id,
    )
    return dict(response.payload)


async def main() -> None:
    client = build_client()
    try:
        result = await enrich_organization(client, org_id="org-4711", run_id="run-4711")
    except GateTimeoutError as exc:
        # Gate may or may not have executed. A replay is safe only under the
        # stable idempotency key above; that decision belongs to the caller.
        print(f"timed out after {exc.timeout_seconds}s")
    except GateConnectionError:
        # Gate was never reached, so nothing ran. Retryable.
        print("gate unreachable")
    except GateHTTPError as exc:
        # The status is a structured attribute; a 4xx will not improve on retry.
        verdict = "retryable" if exc.is_server_error else "permanent"
        print(f"gate rejected the call: HTTP {exc.status_code} ({verdict})")
    except GateClientError as exc:
        # One base class catches every remaining transport failure, so no
        # failure escapes untyped and none needs its message parsed.
        print(f"transport failure: {type(exc).__name__}: {exc}")
    else:
        print(f"converge returned: {result}")


if __name__ == "__main__":
    asyncio.run(main())
