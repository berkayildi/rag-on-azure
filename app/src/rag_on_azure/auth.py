"""FastAPI auth dependency — extracts ``TenantContext`` from a Bearer JWT.

See ``docs/design/rag-on-azure.md`` §3.5.

Two decode modes, gated by the ``ENABLE_DEV_AUTH`` setting:

- ``ENABLE_DEV_AUTH=true`` (local dev convenience) — JWT is decoded
  *without* signature verification so curl-driven testing works against
  unsigned tokens minted by ``scripts/mint-token.py``. The dev path is
  off by default; an operator must set the flag explicitly to engage it.
- ``ENABLE_DEV_AUTH=false`` (production) — JWT signature is verified
  with RS256 against the public PEM fetched from Key Vault (cached for
  five minutes inside ``KeyVaultClient``). PyJWT's ``require`` option
  enforces presence of ``sub`` / ``tenant_id`` / ``exp`` / ``iat``;
  ``exp`` validation is built-in.

The wire contract — ``Authorization: Bearer <jwt>`` with ``tenant_id``
and optional ``tenant_admin`` claims — is identical across both modes.

Status code mapping:

  - 401 (Unauthorized) — credential is missing, malformed, or fails
    signature verification (wrong key, expired, missing required claim)
  - 403 (Forbidden) — credential decoded but lacks a valid
    ``tenant_id`` claim, or (via ``get_current_admin``) lacks the
    ``tenant_admin`` claim required for admin-only routes
  - 503 (Service Unavailable) — verifier itself is degraded (Key Vault
    fetch failed). Differentiates "your token is bad" from "we cannot
    currently check your token"; lets clients retry vs. re-auth

``get_current_admin`` composes on top of ``get_current_tenant`` for
admin-only routes (e.g. ``POST /ingest``): same auth surface, with an
extra check that ``ctx.is_admin`` is True.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from rag_on_azure.api.schemas import TenantContext
from rag_on_azure.settings import get_settings

log = logging.getLogger(__name__)

JWT_ALGORITHM = "RS256"
REQUIRED_CLAIMS = ["sub", "tenant_id", "exp", "iat"]


class _KeyProvider(Protocol):
    """Structural contract for the auth-side Key Vault client.

    Satisfied by ``KeyVaultClient`` in production and by
    ``FakeKeyVaultClient`` in unit tests. Lives here (not in
    ``key_vault.py``) so ``auth.py`` does not depend on the concrete
    Azure-SDK-backed class for type-checking.
    """

    async def get_signing_key(self) -> str: ...


def resolve_key_vault(request: Request) -> _KeyProvider | None:
    """FastAPI dep — pulls the KV client off ``app.state``.

    Co-located with the auth module rather than imported from
    ``api.routes`` to avoid a circular import (``api.routes`` imports
    ``get_current_tenant`` from this module). Public so integration
    tests can override it via ``app.dependency_overrides`` — same
    convention as ``get_llm`` / ``get_search`` / ``get_key_vault`` in
    ``api.routes``.

    Returns ``None`` if ``app.state`` has no ``key_vault`` attribute.
    Production lifespan always sets it; dev-mode tests construct the
    app without entering the lifespan and don't need it (the unsigned
    decode path never reads it). Verified-mode requests against a
    misconfigured app fall through to the explicit "verifier degraded"
    503 in ``get_current_tenant`` rather than hitting an opaque
    ``AttributeError`` during dep resolution.
    """
    return getattr(request.app.state, "key_vault", None)


def _decode_unverified(token: str) -> dict[str, Any]:
    """Decode without signature verification — dev mode only."""
    return jwt.decode(token, options={"verify_signature": False})


async def _decode_verified(token: str, key_vault: _KeyProvider) -> dict[str, Any]:
    """Decode with RS256 signature verification against the KV-fetched key.

    Wraps the KV fetch in a try/except so a degraded vault yields HTTP
    503 ("verifier degraded") rather than 401 ("bad token"). PyJWT
    errors propagate to the caller, which maps them to 401.
    """
    try:
        signing_key = await key_vault.get_signing_key()
    except Exception as exc:
        log.error("Key Vault fetch failed during JWT verification: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="verifier degraded",
        ) from exc
    return jwt.decode(
        token,
        key=signing_key,
        algorithms=[JWT_ALGORITHM],
        options={"require": REQUIRED_CLAIMS},
    )


async def get_current_tenant(
    authorization: Annotated[str | None, Header()] = None,
    key_vault: Annotated[_KeyProvider | None, Depends(resolve_key_vault)] = None,
) -> TenantContext:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    settings = get_settings()
    try:
        if settings.enable_dev_auth:
            claims = _decode_unverified(token)
        else:
            if key_vault is None:
                # Production resolves this via Depends; only a unit test
                # forgetting to inject a fake reaches this branch. Treat
                # as verifier-misconfigured rather than as a bad token.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="verifier degraded",
                )
            claims = await _decode_verified(token, key_vault)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tenant_id = claims.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="JWT missing required tenant_id claim",
        )

    is_admin = bool(claims.get("tenant_admin", False))
    return TenantContext(tenant_id=tenant_id, is_admin=is_admin)


async def get_current_admin(
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
) -> TenantContext:
    """Admin-only auth gate composed on top of ``get_current_tenant``.

    Same auth surface (Bearer JWT, signature verification or unsigned
    decode per ``ENABLE_DEV_AUTH``); requires ``tenant_admin=True`` in
    the decoded claims. Used by ``POST /ingest`` and any future
    admin-only routes.
    """
    if not ctx.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin claim required",
        )
    return ctx
