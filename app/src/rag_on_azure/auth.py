"""FastAPI auth dependency — extracts ``TenantContext`` from a Bearer JWT.

See ``docs/design/rag-on-azure.md`` §3.5.

Day 5 ships the dependency in dev mode: the JWT is decoded *without*
signature verification so local curl-driven testing works against
tokens minted by ``scripts/mint-token.py`` before Key Vault wiring
lands. Day 6 hardens this in a single, small diff:

  - fetch the verifying key from Key Vault (cached 5 min)
  - flip ``verify_signature=True`` and add ``algorithms=["RS256"]``
  - require ``sub`` / ``exp`` / ``iat`` claims via PyJWT's ``require``

The wire contract — ``Authorization: Bearer <jwt>`` with ``tenant_id``
and optional ``tenant_admin`` claims — is stable across the Day 5 →
Day 6 flip. No request schema changes; no caller changes.

Status code mapping:

  - 401 (Unauthorized) — credential is missing or malformed
    (no header, wrong scheme, undecodable JWT)
  - 403 (Forbidden) — credential decoded but lacks the required
    ``tenant_id`` claim
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException, status

from rag_on_azure.api.schemas import TenantContext

log = logging.getLogger(__name__)

# Emitted at module import (i.e. once per process) so the warning shows
# up in the uvicorn startup log. Day 6 deletes this line when signature
# verification flips on.
log.warning(
    "JWT signature verification disabled — DEV MODE ONLY "
    "(Day 6 enables verification via Key Vault)"
)


def _decode_unverified(token: str) -> dict[str, Any]:
    """Decode without signature verification. Day 5 only — see module docstring."""
    return jwt.decode(token, options={"verify_signature": False})


async def get_current_tenant(
    authorization: Annotated[str | None, Header()] = None,
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
    try:
        claims = _decode_unverified(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed JWT",
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
