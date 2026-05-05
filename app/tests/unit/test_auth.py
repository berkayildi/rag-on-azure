"""Unit tests for the get_current_tenant FastAPI dependency.

Day 5 dev-mode contract:

- Bearer JWT decoded without signature verification (``verify_signature=False``)
- ``tenant_id`` claim required → 403 if absent
- Missing / malformed Authorization header → 401

Day 6 swaps the decoder for one that fetches a Key Vault key and
verifies the signature; the contract these tests pin (status codes,
returned ``TenantContext`` shape) does not change.
"""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from fastapi import HTTPException

from rag_on_azure.auth import get_current_tenant

# Dummy HMAC key used only to mint test tokens. The dependency under
# test never sees it — it decodes with verify_signature=False. Padded
# to 32 bytes purely to silence PyJWT's InsecureKeyLengthWarning.
_DEV_KEY = "dev-only-not-a-secret-padded-32b"


def _mint(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _DEV_KEY, algorithm="HS256")


# ---------------------------------------------------------------------------
# 401 — credential missing or malformed
# ---------------------------------------------------------------------------


async def test_missing_authorization_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    "header",
    [
        "Token abc.def.ghi",  # wrong scheme
        "Bearer",  # missing token
        "Bearer abc def",  # too many parts
        "abc.def.ghi",  # no scheme
    ],
)
async def test_malformed_authorization_header_raises_401(header: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=header)
    assert exc_info.value.status_code == 401


async def test_undecodable_token_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization="Bearer not-a-real-jwt")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 403 — credential decoded but lacks required claim
# ---------------------------------------------------------------------------


async def test_missing_tenant_id_claim_raises_403() -> None:
    token = _mint({"sub": "user-1"})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 403


async def test_empty_tenant_id_claim_raises_403() -> None:
    token = _mint({"sub": "user-1", "tenant_id": ""})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 403


async def test_non_string_tenant_id_claim_raises_403() -> None:
    token = _mint({"sub": "user-1", "tenant_id": 12345})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Success — TenantContext returned
# ---------------------------------------------------------------------------


async def test_returns_tenant_context_with_admin_false_when_claim_absent() -> None:
    token = _mint({"sub": "user-1", "tenant_id": "demo"})
    ctx = await get_current_tenant(authorization=f"Bearer {token}")
    assert ctx.tenant_id == "demo"
    assert ctx.is_admin is False


async def test_returns_tenant_context_with_admin_true_when_claim_present() -> None:
    token = _mint({"sub": "user-1", "tenant_id": "demo", "tenant_admin": True})
    ctx = await get_current_tenant(authorization=f"Bearer {token}")
    assert ctx.tenant_id == "demo"
    assert ctx.is_admin is True


async def test_admin_claim_false_yields_non_admin() -> None:
    token = _mint({"sub": "user-1", "tenant_id": "demo", "tenant_admin": False})
    ctx = await get_current_tenant(authorization=f"Bearer {token}")
    assert ctx.is_admin is False
