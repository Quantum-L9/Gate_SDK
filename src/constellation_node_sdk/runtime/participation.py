"""
Node participation: how a birthed node becomes, and stays, an active Gate participant.

Gate's registry is authoritative and, in the reference deployment, in memory: a
Gate restart forgets every node. A node that registers once at startup and then
reports itself ready is therefore silently unroutable after the first Gate
restart. Nodes used to close that gap themselves (a startup registration, a
re-registration loop, a registration-aware readiness probe) — the same
infrastructure written once per node. This module is that infrastructure, owned
by the SDK.

The SDK asks; Gate decides. Every attempt goes through :func:`register_node`
(the one registration HTTP implementation), and a node is ``active`` only after
Gate accepted it. Nothing here grants a node anything.

States::

    disabled       registration is switched off, or there is no Gate URL
    not_attempted  enabled, but no attempt has completed yet
    registering    the first attempt is in flight
    active         Gate accepted the most recent attempt
    degraded       the most recent attempt failed (rejected, unreachable, or
                   the registration could not be built)

``ready`` is true for ``active`` and ``disabled`` only. A re-registration of an
``active`` node keeps it ``active`` until the attempt resolves, so readiness does
not flap on every interval.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from constellation_node_sdk.gate.config import _env_bool
from constellation_node_sdk.gate.registration import (
    NodeRegistration,
    build_node_registration,
    load_node_spec,
    register_node,
)

_logger = logging.getLogger(__name__)

DEFAULT_REREGISTRATION_INTERVAL_SECONDS = 300.0
_MAX_ERROR_CHARS = 300


class ParticipationState(StrEnum):
    DISABLED = "disabled"
    NOT_ATTEMPTED = "not_attempted"
    REGISTERING = "registering"
    ACTIVE = "active"
    DEGRADED = "degraded"


_READY_STATES = frozenset({ParticipationState.ACTIVE, ParticipationState.DISABLED})


class ParticipationStatus(BaseModel):
    """A point-in-time view of a node's Gate participation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ParticipationState
    node_name: str | None = None
    attempts: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    reregistration_interval_seconds: float = Field(default=0.0, ge=0.0)

    @property
    def ready(self) -> bool:
        return self.state in _READY_STATES


class NodeParticipation:
    """
    Own a node's registration with Gate for the node's whole lifetime.

    ``start()`` makes one bounded attempt and, when ``reregistration_interval_seconds``
    is positive, keeps re-registering in the background so the node recovers on
    its own after Gate forgets it. ``stop()`` cancels the loop. ``on_change`` is
    called with the new status whenever the status changes, which is how the
    node runtime binds readiness to participation.
    """

    def __init__(
        self,
        *,
        gate_url: str | None,
        registration: NodeRegistration | None = None,
        spec_path: str | None = None,
        admin_token: str | None = None,
        retries: int = 3,
        overwrite: bool = True,
        enabled: bool = True,
        reregistration_interval_seconds: float = DEFAULT_REREGISTRATION_INTERVAL_SECONDS,
        on_change: Callable[[ParticipationStatus], None] | None = None,
    ) -> None:
        if retries < 1:
            raise ValueError("retries must be >= 1")
        if reregistration_interval_seconds < 0:
            raise ValueError("reregistration_interval_seconds must be >= 0")
        normalized_url = (gate_url or "").strip().rstrip("/")
        if registration is None and spec_path is None and enabled and normalized_url:
            raise ValueError("NodeParticipation needs a registration or a spec_path")
        self._gate_url = normalized_url
        self._registration = registration
        self._spec_path = spec_path
        self._admin_token = admin_token
        self._retries = retries
        self._overwrite = overwrite
        self._interval = float(reregistration_interval_seconds)
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._status = ParticipationStatus(
            state=(
                ParticipationState.NOT_ATTEMPTED
                if enabled and normalized_url
                else ParticipationState.DISABLED
            ),
            node_name=registration.node_name if registration is not None else None,
            reregistration_interval_seconds=self._interval,
        )

    @classmethod
    def from_env(
        cls,
        *,
        gate_url: str | None = None,
        registration: NodeRegistration | None = None,
        on_change: Callable[[ParticipationStatus], None] | None = None,
    ) -> NodeParticipation:
        """
        Build from the ``GATE_*`` registration environment.

        ``gate_url`` overrides ``GATE_URL`` (the node runtime passes its own
        resolved URL). ``registration`` overrides the ``GATE_NODE_SPEC_PATH``
        spec file for nodes whose identity comes from application settings.
        ``GATE_REGISTRATION_ENABLED=false`` yields a ``disabled`` participation
        without requiring ``GATE_URL``.
        """
        enabled = _env_bool("GATE_REGISTRATION_ENABLED", True)
        resolved_url = gate_url if gate_url is not None else os.getenv("GATE_URL", "")
        return cls(
            gate_url=resolved_url,
            registration=registration,
            spec_path=(
                None
                if registration is not None
                else os.getenv("GATE_NODE_SPEC_PATH", "engine/spec.yaml")
            ),
            admin_token=os.getenv("GATE_ADMIN_TOKEN") or None,
            retries=int(os.getenv("GATE_REGISTER_RETRIES", "3")),
            overwrite=_env_bool("GATE_REGISTER_OVERWRITE", True),
            enabled=enabled,
            reregistration_interval_seconds=float(
                os.getenv(
                    "GATE_REREGISTRATION_INTERVAL_SECONDS",
                    str(DEFAULT_REREGISTRATION_INTERVAL_SECONDS),
                )
            ),
            on_change=on_change,
        )

    @property
    def status(self) -> ParticipationStatus:
        return self._status

    @property
    def ready(self) -> bool:
        return self._status.ready

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _set(self, status: ParticipationStatus) -> None:
        changed = status != self._status
        self._status = status
        if changed and self._on_change is not None:
            self._on_change(status)

    def _resolve_registration(self) -> NodeRegistration:
        if self._registration is not None:
            return self._registration
        if self._spec_path is None:
            raise ValueError("no registration and no spec_path configured")
        return build_node_registration(load_node_spec(self._spec_path))

    async def register_once(self) -> ParticipationStatus:
        """Make one bounded registration attempt and return the resulting status."""
        current = self._status
        if current.state is ParticipationState.DISABLED:
            return current
        if current.state is not ParticipationState.ACTIVE:
            self._set(current.model_copy(update={"state": ParticipationState.REGISTERING}))

        attempted_at = datetime.now(tz=UTC)
        node_name = current.node_name
        error: str | None = None
        try:
            registration = self._resolve_registration()
            node_name = registration.node_name
            accepted = await register_node(
                gate_url=self._gate_url,
                registration=registration,
                admin_token=self._admin_token,
                retries=self._retries,
                overwrite=self._overwrite,
            )
            if not accepted:
                error = "Gate rejected the registration or was unreachable after retries"
        except (FileNotFoundError, ValueError) as exc:
            error = f"registration could not be built: {exc}"
        except Exception as exc:  # noqa: BLE001 - participation must never crash the node
            error = f"registration attempt failed: {type(exc).__name__}: {exc}"

        base = self._status
        if error is None:
            status = base.model_copy(
                update={
                    "state": ParticipationState.ACTIVE,
                    "node_name": node_name,
                    "attempts": base.attempts + 1,
                    "consecutive_failures": 0,
                    "last_attempt_at": attempted_at,
                    "last_success_at": attempted_at,
                    "last_error": None,
                }
            )
            if base.state is not ParticipationState.ACTIVE:
                _logger.info("gate_participation_active", extra={"node_name": node_name})
        else:
            status = base.model_copy(
                update={
                    "state": ParticipationState.DEGRADED,
                    "node_name": node_name,
                    "attempts": base.attempts + 1,
                    "consecutive_failures": base.consecutive_failures + 1,
                    "last_attempt_at": attempted_at,
                    "last_error": error[:_MAX_ERROR_CHARS],
                }
            )
            _logger.warning(
                "gate_participation_degraded",
                extra={
                    "node_name": node_name,
                    "consecutive_failures": status.consecutive_failures,
                    "error": status.last_error,
                },
            )
        self._set(status)
        return status

    async def _reregistration_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.register_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the loop alive; register_once records failures
                _logger.exception("gate_participation_loop_error")

    async def start(self) -> ParticipationStatus:
        """Register once, then keep the registration alive in the background."""
        if self._status.state is ParticipationState.DISABLED:
            return self._status
        status = await self.register_once()
        if self._interval > 0 and not self.running:
            self._task = asyncio.create_task(
                self._reregistration_loop(), name="gate-participation-reregistration"
            )
        return status

    async def stop(self) -> None:
        """Cancel the re-registration loop. Gate has no deregistration call."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
