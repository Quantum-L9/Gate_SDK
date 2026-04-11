from __future__ import annotations

import pytest

from constellation_node_sdk.transport.errors import TenantMutationError
from constellation_node_sdk.transport.tenant import (
    TenantContext,
    assert_tenant_immutable,
    ensure_tenant_context,
)


def test_ensure_tenant_context_from_string() -> None:
    tenant = ensure_tenant_context("tenant-a")

    assert isinstance(tenant, TenantContext)
    assert tenant.actor == "tenant-a"
    assert tenant.on_behalf_of == "tenant-a"
    assert tenant.originator == "tenant-a"
    assert tenant.org_id == "tenant-a"
    assert tenant.user_id is None


def test_ensure_tenant_context_from_dict() -> None:
    tenant = ensure_tenant_context(
        {
            "actor": "worker-a",
            "on_behalf_of": "tenant-a",
            "originator": "client-a",
            "org_id": "tenant-a",
            "user_id": "user-1",
        }
    )

    assert tenant.actor == "worker-a"
    assert tenant.on_behalf_of == "tenant-a"
    assert tenant.originator == "client-a"
    assert tenant.org_id == "tenant-a"
    assert tenant.user_id == "user-1"


def test_assert_tenant_immutable_accepts_same_tenant() -> None:
    tenant = ensure_tenant_context("tenant-a")
    assert_tenant_immutable(tenant, tenant)


def test_assert_tenant_immutable_rejects_mutation() -> None:
    parent = ensure_tenant_context("tenant-a")
    child = ensure_tenant_context("tenant-b")

    with pytest.raises(TenantMutationError):
        assert_tenant_immutable(parent, child)
