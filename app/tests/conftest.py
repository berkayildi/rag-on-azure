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
