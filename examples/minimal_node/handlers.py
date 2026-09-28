from __future__ import annotations

from typing import Any

from constellation_node_sdk import register_handler


@register_handler("sdk-echo")
async def handle_echo(_tenant: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "completed", "echo": payload}
