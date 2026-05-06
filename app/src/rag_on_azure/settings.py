"""Application settings, sourced from environment variables.

See ``docs/design/rag-on-azure.md`` §2.4 (Container App env vars) and
§3 (component layout). The deployed stack populates these from Bicep
outputs into the Container App; local dev populates them via ``.env``
(gitignored).

``enable_dev_auth`` is the Day 5 kill-switch that gates the
unsigned-JWT decode path in ``auth.py``. Default is False — even with
the dev-mode auth code in place, an operator must explicitly set
``ENABLE_DEV_AUTH=true`` to enable the dev path. Defence in depth.
Day 6 inverts the meaning: signature verification becomes the default
path; the flag goes away.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    azure_openai_endpoint: str
    azure_openai_embedding_deployment: str
    azure_openai_chat_deployment: str
    azure_search_endpoint: str
    azure_search_index_name: str = "corpus"
    key_vault_uri: str
    log_level: str = "INFO"
    enable_dev_auth: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
