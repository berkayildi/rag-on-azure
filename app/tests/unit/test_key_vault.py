"""Unit tests for ``KeyVaultClient`` — focused on the PEM-shape check
in ``ping()``. Cache TTL behavior, close idempotence, and credential
construction are exercised implicitly by the integration tests; this
module pins the validation contract that prevents the silent-failure
mode where a corrupted secret value passes naive existence checks."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rag_on_azure.key_vault import (
    SIGNING_KEY_SECRET_NAME,
    InvalidSigningKeyError,
    KeyVaultClient,
)


@dataclass
class _FakeSecret:
    value: str


class _FakeSecretClient:
    """Minimal stand-in for ``azure.keyvault.secrets.aio.SecretClient`` —
    enough surface for ``KeyVaultClient.ping()``: returns a fixed
    secret value on every ``get_secret`` call, no-op close. Records
    calls so the test can assert which secret name was fetched."""

    def __init__(self, value: str) -> None:
        self._value = value
        self.get_secret_calls: list[str] = []

    async def get_secret(self, name: str) -> _FakeSecret:
        self.get_secret_calls.append(name)
        return _FakeSecret(value=self._value)

    async def close(self) -> None:
        pass


async def test_ping_raises_invalid_signing_key_on_malformed_pem() -> None:
    """A corrupted secret value must fail ``ping()`` with
    ``InvalidSigningKeyError`` so the lifespan refuses to boot. Without
    this check, /readyz would report green (the secret is fetchable)
    while every authenticated request 401s with PyJWT's
    ``InvalidKeyError`` on the unparseable key."""
    inner = _FakeSecretClient(value="not a real PEM")
    kv = KeyVaultClient(inner=inner)  # type: ignore[arg-type]

    with pytest.raises(InvalidSigningKeyError):
        await kv.ping()

    assert inner.get_secret_calls == [SIGNING_KEY_SECRET_NAME]


async def test_ping_accepts_valid_public_pem(rsa_keypair: tuple[str, str]) -> None:
    """Sanity check on the validator: a well-formed RSA public PEM (the
    production shape stored in Key Vault) passes ``ping()`` cleanly.
    Pairs with the negative case above to bound the validation contract."""
    _, public_pem = rsa_keypair
    inner = _FakeSecretClient(value=public_pem)
    kv = KeyVaultClient(inner=inner)  # type: ignore[arg-type]

    await kv.ping()  # would raise InvalidSigningKeyError if validation failed

    assert inner.get_secret_calls == [SIGNING_KEY_SECRET_NAME]
