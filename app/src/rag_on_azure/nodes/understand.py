"""``understand`` — query rewriting node.

See ``docs/design/rag-on-azure.md`` §3.2.

Phase B implements query rewriting only. Filter extraction (the spec
mentions year/jurisdiction filters) is deferred until the corpus
index gains filterable fields beyond ``tenant_id`` and ``source``
(§2.2 — those are the only filterable fields today). Extracting
filters that have nowhere to land would be theatre. When the index
schema grows, ``QueryRewrite`` gains a ``filters`` field and the
prompt asks the LLM to populate it from a closed list of valid keys.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from rag_on_azure.clients.llm import LLMClient
from rag_on_azure.models import Message
from rag_on_azure.state import GraphState

SYSTEM_PROMPT = (
    "You rewrite user questions to maximise retrieval recall against a "
    "corpus of UK regulatory documents (FCA, HMRC, FATF). Expand "
    "acronyms (CASS → Client Assets Sourcebook), normalise terminology, "
    "and strip filler. Do NOT answer the question — return only the "
    "rewritten query."
)


class QueryRewrite(BaseModel):
    rewritten_query: str


def make_understand_node(
    llm: LLMClient,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def understand(state: GraphState) -> dict[str, Any]:
        rewrite: QueryRewrite = await llm.complete(
            messages=[
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=state.question),
            ],
            schema=QueryRewrite,
        )
        return {"rewritten_query": rewrite.rewritten_query}

    return understand
