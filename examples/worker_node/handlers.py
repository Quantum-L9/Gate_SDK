from __future__ import annotations

from constellation_node_sdk import register_handler


@register_handler("score")
async def handle_score(_tenant: str, payload: dict) -> dict:
    entity_id = payload["entity_id"]
    return {
        "status": "completed",
        "entity_id": entity_id,
        "score": 91,
        "explanation": "example deterministic score",
    }
