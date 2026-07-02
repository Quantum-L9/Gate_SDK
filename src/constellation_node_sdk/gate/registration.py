from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import GateRegistrationConfig, get_gate_registration_config_from_env

_DEFAULT_RETRY_BASE_SECONDS = 1.0


def load_node_spec(spec_path: str) -> dict[str, Any]:
    """
    Load node spec.yaml and require a top-level mapping.
    """
    path = Path(spec_path)
    if not path.exists():
        raise FileNotFoundError(f"node spec not found: {path.resolve()}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"node spec must be a YAML mapping: {path}")
    return raw


def build_registration_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """
    Convert spec.yaml into Gate admin registration payload.

    Required:
    - node.id
    - node.actions
    """
    node = spec.get("node", {})
    if not isinstance(node, dict) or not node:
        raise ValueError("spec.yaml missing required node block")

    node_id = str(node.get("id", "")).strip().lower()
    if not node_id:
        raise ValueError("spec.yaml node.id is required")

    actions_raw = node.get("actions", [])
    if not isinstance(actions_raw, list) or not actions_raw:
        raise ValueError(f"spec.yaml node.actions must not be empty (node: {node_id})")

    actions = [str(action).strip().lower() for action in actions_raw if str(action).strip()]
    if not actions:
        raise ValueError(
            f"spec.yaml node.actions must contain at least one non-blank action (node: {node_id})"
        )

    internal_url = str(node.get("internal_url", f"http://{node_id}:8000")).strip().rstrip("/")
    if not internal_url:
        raise ValueError("spec.yaml node.internal_url must not be blank")

    return {
        node_id: {
            "internal_url": internal_url,
            "supported_actions": actions,
            "priority_class": str(node.get("priority_class", "P2")).strip() or "P2",
            "max_concurrent": int(node.get("max_concurrent", 50)),
            "health_endpoint": str(node.get("health_endpoint", "/v1/health")).strip()
            or "/v1/health",
            "timeout_ms": int(node.get("timeout_ms", 30000)),
            "metadata": {
                "version": str(node.get("version", "1.0.0")).strip() or "1.0.0",
                "type": str(node.get("type", "custom")).strip() or "custom",
                "generated_by": "constellation-node-sdk",
            },
        }
    }


async def register_with_gate(
    *,
    gate_url: str,
    admin_token: str | None = None,
    spec_path: str,
    retries: int = 3,
    overwrite: bool = True,
) -> bool:
    """
    Register the current node with Gate via POST /v1/admin/register.

    Returns True on success, False on rejection or after retry exhaustion.
    Registration failure is intentionally non-fatal for node startup.
    """
    try:
        spec = load_node_spec(spec_path)
        payload = build_registration_payload(spec)
    except (FileNotFoundError, ValueError):
        return False

    _node_id = next(iter(payload))  # noqa: F841 — used for future logging
    url = f"{gate_url.rstrip('/')}/v1/admin/register"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    params = {"overwrite": "true" if overwrite else "false"}

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    params=params,
                )

            if response.status_code == 200:
                return True

            if response.status_code in {400, 401, 403, 409, 422}:
                return False

        except httpx.TransportError:
            pass

        if attempt < retries:
            backoff = _DEFAULT_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

    return False


async def register_from_env() -> bool:
    """
    Convenience wrapper for Gate registration using environment-derived config.
    """
    config: GateRegistrationConfig = get_gate_registration_config_from_env()
    if not config.registration_enabled:
        return False

    return await register_with_gate(
        gate_url=config.gate_url,
        admin_token=config.admin_token,
        spec_path=config.spec_path,
        retries=config.retries,
        overwrite=config.overwrite,
    )
