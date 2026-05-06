"""KeyVaultClient — fetches the JWT signing key, with TTL cache.

See ``docs/design/rag-on-azure.md`` §2.5 (KV resource) and §3.5 (auth flow).

The ``jwt-signing-key`` secret holds the **public** PEM of the RSA keypair
the issuer uses to sign tokens (RS256). The verifying app holds only the
public half — the private key never lands in this codebase or in the
deployed stack; it is rotated by the operator out-of-band. Local dev's
``scripts/mint-token.py`` is the one issuer-side simulation, and its
private keypair lives in ``scripts/dev-keys/`` (gitignored).

This module is purely additive in commit 1: lifespan constructs the
client and pings it at startup, and ``/readyz`` includes it as a check.
The auth-side flip from unsigned-decode to signature verification arrives
in commit 2 and is gated by ``ENABLE_DEV_AUTH=false``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets.aio import SecretClient

log = logging.getLogger(__name__)

# Spec §2.5: the single secret name written by Bicep. Hardcoded here
# rather than another Settings field — the name is fixed by the design
# spec, and a second env var would be ceremony without value.
SIGNING_KEY_SECRET_NAME = "jwt-signing-key"
DEFAULT_CACHE_TTL_S = 300.0


class _TimedCache:
    """Single-slot async cache with TTL + first-fetch coalescing.

    A single ``asyncio.Lock`` ensures that under a cold-start stampede
    (N concurrent first-callers) only one issues the underlying fetch;
    the rest await the same value. Double-checked locking around the
    expiry check keeps the hot path lock-free once the value is fresh.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._value: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, fetcher: Callable[[], Awaitable[str]]) -> str:
        now = time.monotonic()
        if self._value is not None and now < self._expires_at:
            return self._value
        async with self._lock:
            now = time.monotonic()
            if self._value is not None and now < self._expires_at:
                return self._value
            value = await fetcher()
            self._value = value
            self._expires_at = now + self._ttl
            return value

    def invalidate(self) -> None:
        self._value = None
        self._expires_at = 0.0


class KeyVaultClient:
    """Thin wrapper around ``SecretClient`` exposing signing-key fetch + ping.

    Construction takes ``vault_uri`` and an optional injected ``inner``
    ``SecretClient`` (test seam). Production uses ``DefaultAzureCredential``;
    no key path. The Container App's managed identity has the
    ``Key Vault Secrets User`` role per spec §2.4.
    """

    def __init__(
        self,
        *,
        vault_uri: str | None = None,
        credential: AsyncTokenCredential | None = None,
        inner: SecretClient | None = None,
        ttl_seconds: float = DEFAULT_CACHE_TTL_S,
    ) -> None:
        if inner is None:
            if vault_uri is None:
                raise ValueError("vault_uri is required when inner is not supplied")
            self._credential: AsyncTokenCredential | None = (
                credential or DefaultAzureCredential()
            )
            inner = SecretClient(vault_url=vault_uri, credential=self._credential)
        else:
            self._credential = None  # caller owns the credential

        self._inner = inner
        self._cache = _TimedCache(ttl_seconds=ttl_seconds)
        self._closed = False

    async def get_signing_key(self) -> str:
        """Return the public PEM of the JWT signing key, cached for TTL.

        Errors from the SDK call propagate; the caller (auth dependency in
        commit 2) maps cache-miss-plus-KV-down to HTTP 503 to differentiate
        "verifier degraded" from "your token is bad".
        """
        return await self._cache.get_or_fetch(self._fetch_secret_value)

    async def _fetch_secret_value(self) -> str:
        secret = await self._inner.get_secret(SIGNING_KEY_SECRET_NAME)
        return secret.value or ""

    async def ping(self) -> None:
        """Cheap readiness probe — credential + named secret both reachable.

        Bypasses the cache deliberately: a stale cached value must not mask
        a degraded vault when the readiness probe is the very thing meant
        to surface the degradation.
        """
        await self._inner.get_secret(SIGNING_KEY_SECRET_NAME)

    async def close(self) -> None:
        """Idempotent — safe to call twice."""
        if self._closed:
            return
        self._closed = True
        await self._inner.close()
        if self._credential is not None:
            await self._credential.close()

    async def __aenter__(self) -> KeyVaultClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
