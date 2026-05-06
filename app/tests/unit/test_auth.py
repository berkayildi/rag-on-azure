"""Unit tests for the get_current_tenant FastAPI dependency.

The dependency has two modes, gated by ``ENABLE_DEV_AUTH``:

- ``true`` — Bearer JWT decoded without signature verification. Tests
  in the "dev mode" section pin this contract; ``conftest.py`` defaults
  ``ENABLE_DEV_AUTH=true`` so they run unmodified.
- ``false`` — RS256 signature verification against a public PEM
  fetched from Key Vault. Tests in the "verified mode" section flip
  the env via ``monkeypatch``, supply a ``FakeKeyVaultClient`` with a
  matching public PEM, and mint tokens with the matching private key.

Status code mapping is identical across modes: 401 for bad credentials,
403 for missing ``tenant_id`` claim, 503 only in verified mode when the
Key Vault fetch itself fails.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from fastapi import HTTPException

from rag_on_azure.api.schemas import TenantContext
from rag_on_azure.auth import get_current_admin, get_current_tenant
from rag_on_azure.settings import get_settings

from .conftest import FakeKeyVaultClient

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


# ---------------------------------------------------------------------------
# Verified-mode contract (ENABLE_DEV_AUTH=false)
# ---------------------------------------------------------------------------


def _enable_verified_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip ENABLE_DEV_AUTH=false for one test and bust the settings cache.

    The autouse cache-reset fixture clears around the test, but the env
    change happens *inside* the test body, so we must clear again after
    setenv to make the new value visible to ``get_settings``.
    """
    monkeypatch.setenv("ENABLE_DEV_AUTH", "false")
    get_settings.cache_clear()


def _mint_rs256(claims: dict[str, Any], private_pem: str) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256")


async def test_signed_token_verifies_with_keyvault_key(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    _enable_verified_mode(monkeypatch)
    private_pem, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)
    now = int(time.time())
    token = _mint_rs256(
        {"sub": "u", "tenant_id": "demo", "iat": now, "exp": now + 60},
        private_pem,
    )

    ctx = await get_current_tenant(authorization=f"Bearer {token}", key_vault=fake_kv)
    assert ctx.tenant_id == "demo"
    assert ctx.is_admin is False
    assert fake_kv.get_signing_key_calls == 1


async def test_signed_token_with_wrong_key_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[str, str],
    rsa_keypair_other: tuple[str, str],
) -> None:
    _enable_verified_mode(monkeypatch)
    _, public_pem_kv = rsa_keypair  # what KV holds
    private_pem_attacker, _ = rsa_keypair_other  # attacker's signing key
    fake_kv = FakeKeyVaultClient(signing_key=public_pem_kv)
    now = int(time.time())
    token = _mint_rs256(
        {"sub": "u", "tenant_id": "demo", "iat": now, "exp": now + 60},
        private_pem_attacker,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}", key_vault=fake_kv)
    assert exc_info.value.status_code == 401


async def test_expired_token_returns_401(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    _enable_verified_mode(monkeypatch)
    private_pem, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)
    now = int(time.time())
    token = _mint_rs256(
        {"sub": "u", "tenant_id": "demo", "iat": now - 7200, "exp": now - 60},
        private_pem,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}", key_vault=fake_kv)
    assert exc_info.value.status_code == 401


async def test_token_missing_required_claim_returns_401(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """PyJWT's ``require=[...]`` rejects a token missing ``iat`` (or any
    other required claim) with ``MissingRequiredClaimError``, which is
    a ``PyJWTError`` and maps to 401."""
    _enable_verified_mode(monkeypatch)
    private_pem, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)
    now = int(time.time())
    token = _mint_rs256(
        {"sub": "u", "tenant_id": "demo", "exp": now + 60},  # no iat
        private_pem,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}", key_vault=fake_kv)
    assert exc_info.value.status_code == 401


async def test_dev_mode_unsigned_token_rejected_when_verified_mode_active(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """Security gate: an alg=none token minted via the dev-mode path must
    not verify against a real public PEM. RS256 enforcement rejects it."""
    _enable_verified_mode(monkeypatch)
    _, public_pem = rsa_keypair
    fake_kv = FakeKeyVaultClient(signing_key=public_pem)
    now = int(time.time())
    unsigned_token = jwt.encode(
        {"sub": "u", "tenant_id": "demo", "iat": now, "exp": now + 60},
        key="",
        algorithm="none",
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(
            authorization=f"Bearer {unsigned_token}", key_vault=fake_kv
        )
    assert exc_info.value.status_code == 401


async def test_keyvault_unreachable_returns_503_not_401(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[str, str]
) -> None:
    """Per design D3: KV fetch failure during verification is "verifier
    degraded" (503), not "bad token" (401). The token here is well-formed
    but the verifier cannot fetch the key to check it."""
    _enable_verified_mode(monkeypatch)
    private_pem, _ = rsa_keypair
    fake_kv = FakeKeyVaultClient(get_signing_key_raises=ConnectionError("kv down"))
    now = int(time.time())
    token = _mint_rs256(
        {"sub": "u", "tenant_id": "demo", "iat": now, "exp": now + 60},
        private_pem,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_tenant(authorization=f"Bearer {token}", key_vault=fake_kv)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "verifier degraded"


async def test_dev_mode_unsigned_token_works_when_enable_dev_auth_true(
    rsa_keypair: tuple[str, str],
) -> None:
    """Backward compat: dev-mode untouched. ENABLE_DEV_AUTH=true (the
    default in conftest) accepts an unsigned token without consulting
    Key Vault — the existing curl-driven testing flow keeps working."""
    now = int(time.time())
    unsigned_token = jwt.encode(
        {"sub": "u", "tenant_id": "demo", "iat": now, "exp": now + 60},
        key="",
        algorithm="none",
    )
    fake_kv = FakeKeyVaultClient(
        get_signing_key_raises=AssertionError("KV must not be consulted in dev mode")
    )

    ctx = await get_current_tenant(
        authorization=f"Bearer {unsigned_token}", key_vault=fake_kv
    )
    assert ctx.tenant_id == "demo"
    assert fake_kv.get_signing_key_calls == 0


# ---------------------------------------------------------------------------
# Admin gate (composes on top of get_current_tenant)
# ---------------------------------------------------------------------------


async def test_admin_dep_passes_when_admin_claim_true() -> None:
    ctx = TenantContext(tenant_id="demo", is_admin=True)
    result = await get_current_admin(ctx=ctx)
    assert result is ctx


async def test_admin_dep_403_when_admin_claim_false() -> None:
    ctx = TenantContext(tenant_id="demo", is_admin=False)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(ctx=ctx)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "admin claim required"
