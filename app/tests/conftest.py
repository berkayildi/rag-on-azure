"""Top-level test conftest.

Sets the env vars that ``rag_on_azure.settings.Settings`` requires
*before* any test imports the source package. The module-load timing
matters: ``Settings`` is read on first call to ``get_settings()``,
which any test exercising auth or the API will trigger transitively.

These are stable test placeholders, not real endpoints. Real values
flow through ``.env`` for local dev or Container App env vars in
production. ``setdefault`` lets a CI run override (e.g. by exporting
real test endpoints) without this file fighting the export.

Also installs an autouse fixture that clears the ``get_settings``
LRU cache between tests so a test that monkey-patches ``ENABLE_DEV_AUTH``
(or any other field) doesn't leak its override into the next test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "test-embedding")
os.environ.setdefault("AZURE_OPENAI_CHAT_DEPLOYMENT", "test-chat")
os.environ.setdefault("AZURE_SEARCH_ENDPOINT", "https://test.search.windows.net")
os.environ.setdefault("AZURE_SEARCH_INDEX_NAME", "corpus")
os.environ.setdefault("KEY_VAULT_URI", "https://test.vault.azure.net/")
os.environ.setdefault("ENABLE_DEV_AUTH", "true")


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    from rag_on_azure.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _generate_rsa_pem_pair() -> tuple[str, str]:
    """Generate a fresh RSA-2048 keypair and return (private_pem, public_pem)
    as PEM-encoded strings — what mint-token.py reads from disk and what
    Key Vault holds in the ``jwt-signing-key`` secret."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture(scope="session")
def rsa_keypair() -> tuple[str, str]:
    """Session-scoped: 2048-bit RSA generation costs ~50ms; one keypair
    serves every signature-verification test (unit + integration)."""
    return _generate_rsa_pem_pair()


@pytest.fixture(scope="session")
def rsa_keypair_other() -> tuple[str, str]:
    """A second, distinct keypair for wrong-key signature-mismatch tests."""
    return _generate_rsa_pem_pair()
