"""Gate-only capability discovery client with bounded TTL + ETag cache."""

from __future__ import annotations

import time

import httpx

from .capabilities import CapabilityDescriptor, CapabilityListResponse
from .config import GateClientConfig
from .errors import GateClientError, GateResponseError


class CapabilityCacheEntry[T]:
    __slots__ = ("etag", "expires_at", "payload")

    def __init__(self, *, payload: T, etag: str, expires_at: float) -> None:
        self.payload: T = payload
        self.etag = etag
        self.expires_at = expires_at


class GateCapabilityClient:
    """Discover sanitized Gate capabilities without contacting peer nodes."""

    def __init__(
        self,
        config: GateClientConfig,
        *,
        admin_token: str | None = None,
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be > 0")
        self._config = config
        self._admin_token = admin_token.strip() if admin_token else None
        self._cache_ttl_seconds = cache_ttl_seconds
        self._list_cache: CapabilityCacheEntry[CapabilityListResponse] | None = None
        self._action_cache: dict[str, CapabilityCacheEntry[CapabilityDescriptor]] = {}

    @property
    def gate_url(self) -> str:
        return self._config.gate_url

    def invalidate(self) -> None:
        self._list_cache = None
        self._action_cache.clear()

    async def list_capabilities(
        self, *, include_protected: bool = False, force_refresh: bool = False
    ) -> CapabilityListResponse:
        now = time.monotonic()
        if not force_refresh and self._list_cache is not None and self._list_cache.expires_at > now:
            return self._list_cache.payload

        headers = self._headers()
        if self._list_cache is not None and not force_refresh:
            headers["If-None-Match"] = self._list_cache.etag

        params = {"include_protected": "true"} if include_protected else None
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            response = await client.get(
                f"{self._config.gate_url}/v1/capabilities",
                headers=headers,
                params=params,
            )

        if response.status_code == 304 and self._list_cache is not None:
            self._list_cache.expires_at = now + self._cache_ttl_seconds
            return self._list_cache.payload

        if response.status_code >= 400:
            raise GateResponseError(f"Gate capabilities list failed: HTTP {response.status_code}")

        body = response.json()
        if not isinstance(body, dict):
            raise GateClientError("Gate capabilities list must be a JSON object")

        try:
            parsed = CapabilityListResponse.model_validate(body)
        except Exception as exc:  # noqa: BLE001
            raise GateClientError(
                f"Gate capability list failed closed during parse: {exc}"
            ) from exc

        etag = response.headers.get("etag") or parsed.etag
        self._list_cache = CapabilityCacheEntry[CapabilityListResponse](
            payload=parsed,
            etag=etag,
            expires_at=now + self._cache_ttl_seconds,
        )
        return parsed

    async def get_capability(
        self, action: str, *, force_refresh: bool = False
    ) -> CapabilityDescriptor:
        normalized = action.strip().lower()
        if not normalized:
            raise ValueError("action must not be empty")

        now = time.monotonic()
        cached = self._action_cache.get(normalized)
        if not force_refresh and cached is not None and cached.expires_at > now:
            return cached.payload

        headers = self._headers()
        if cached is not None and not force_refresh:
            headers["If-None-Match"] = cached.etag

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            response = await client.get(
                f"{self._config.gate_url}/v1/capabilities/{normalized}",
                headers=headers,
            )

        if response.status_code == 304 and cached is not None:
            cached.expires_at = now + self._cache_ttl_seconds
            return cached.payload

        if response.status_code >= 400:
            raise GateResponseError(
                f"Gate capability lookup failed for {normalized!r}: HTTP {response.status_code}"
            )

        body = response.json()
        if not isinstance(body, dict):
            raise GateClientError("Gate capability response must be a JSON object")

        try:
            parsed = CapabilityDescriptor.model_validate(body)
        except Exception as exc:  # noqa: BLE001
            raise GateClientError(
                f"Gate capability response failed closed during parse: {exc}"
            ) from exc

        etag = response.headers.get("etag") or f'W/"{normalized}"'
        self._action_cache[normalized] = CapabilityCacheEntry[CapabilityDescriptor](
            payload=parsed,
            etag=etag,
            expires_at=now + self._cache_ttl_seconds,
        )
        return parsed

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._admin_token is not None:
            headers["X-Admin-Token"] = self._admin_token
        return headers
