"""FastAPI app construction with lifespan-managed Azure clients + graph.

See ``docs/design/rag-on-azure.md`` §3 + §8.

The lifespan pattern (modern FastAPI; the deprecated startup/shutdown
events are not used) constructs the canonical ``AzureOpenAIClient``
and ``TenantAwareSearchClient`` exactly once at process start and
closes them on shutdown. The compiled graph is built once from those
clients and attached to ``app.state.graph`` so the route handler can
pull it via dependency injection (``api.routes.get_graph``).

Each client owns its own ``DefaultAzureCredential`` — sharing one
between two clients works at runtime but doubles the close-on-shutdown
contract and is unnecessary given the credential is cheap to mint.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from rag_on_azure.api.routes import router
from rag_on_azure.clients.llm import AzureOpenAIClient
from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.graph import build_graph
from rag_on_azure.key_vault import KeyVaultClient
from rag_on_azure.settings import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    if settings.enable_dev_auth:
        log.warning(
            "ENABLE_DEV_AUTH=true — JWT signature verification is OFF. "
            "Local dev only; never set this in production."
        )
    else:
        log.info(
            "JWT signature verification ENABLED (RS256, key fetched from Key Vault)"
        )

    llm = AzureOpenAIClient(
        endpoint=settings.azure_openai_endpoint,
        embedding_deployment=settings.azure_openai_embedding_deployment,
        chat_deployment=settings.azure_openai_chat_deployment,
    )
    search = TenantAwareSearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index_name,
    )
    key_vault = KeyVaultClient(vault_uri=settings.key_vault_uri)
    # Fail-fast at boot if Key Vault is unreachable: the verifier (commit 2)
    # cannot do its job without it. Container Apps will restart the replica
    # and surface the failure on the readiness probe instead of letting a
    # half-broken instance accept traffic.
    await key_vault.ping()

    graph = build_graph(llm, search)

    app.state.llm = llm
    app.state.search = search
    app.state.key_vault = key_vault
    app.state.graph = graph

    try:
        yield
    finally:
        await llm.close()
        await search.close()
        await key_vault.close()


def create_app() -> FastAPI:
    app = FastAPI(title="rag-on-azure", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
