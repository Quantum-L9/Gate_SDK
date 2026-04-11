from __future__ import annotations

from constellation_node_sdk.runtime.handlers import (
    clear_handlers,
    get_handler,
    register_handler,
    registered_actions,
)


def test_register_handler_and_resolve_exact_action() -> None:
    clear_handlers()

    @register_handler("score")
    def handle_score() -> dict:
        return {"status": "completed"}

    resolved = get_handler("score")
    assert resolved is not None
    assert resolved() == {"status": "completed"}
    assert "score" in registered_actions()


def test_get_handler_falls_back_to_wildcard() -> None:
    clear_handlers()

    @register_handler("*")
    def handle_any() -> dict:
        return {"status": "completed"}

    resolved = get_handler("unknown.action")
    assert resolved is not None
    assert resolved() == {"status": "completed"}
