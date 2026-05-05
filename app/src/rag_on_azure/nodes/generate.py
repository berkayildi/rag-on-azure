"""``generate`` — produces the grounded answer with validated citations.

See ``docs/design/rag-on-azure.md`` §3.2 + §7.1 (citation hallucination
mitigation).

Citation contract (audit-grade):

  - The LLM returns an ``Answer`` with ``cited_chunk_ids``.
  - Every cited ID MUST exist in ``state.retrieved_chunks``. If not,
    the node retries once with a stricter prompt that lists the valid
    IDs explicitly.
  - A second contract violation raises ``CitationContractError``. The
    API layer maps that to HTTP 502 — the upstream LLM didn't honour
    the contract; we refuse to return a hallucinated answer.

Hard cap of two LLM calls per ``/query``. No third attempt.

Empty-retrieval short-circuit (Phase D defence-in-depth):

  - If ``state.retrieved_chunks`` is empty, the node returns
    ``EMPTY_RETRIEVAL_ANSWER`` directly without calling the LLM.
    Discovered during Phase D live testing: gpt-4o ignored the
    "say so explicitly" prompt instruction and substituted Consumer
    Duty prose from training data when given empty context, even
    though no chunks were retrieved (citations were correctly empty,
    so the §5.3 retrieval boundary held — but the generation step
    was producing content the user shouldn't see).
  - This short-circuit mirrors the retrieval boundary's
    type-system + audit-test pattern at the generation boundary:
    by construction, no path exists where empty retrieval produces
    LLM-generated content.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from rag_on_azure.api.schemas import Citation
from rag_on_azure.clients.llm import LLMClient
from rag_on_azure.models import Chunk, Message
from rag_on_azure.state import GraphState

log = logging.getLogger(__name__)


class CitationContractError(RuntimeError):
    """LLM cited chunk IDs not present in the retrieved set after one
    strict-prompt retry. The API layer maps this to HTTP 502.
    """


class Answer(BaseModel):
    text: str
    cited_chunk_ids: list[str]


EMPTY_RETRIEVAL_ANSWER = "No relevant context found for this tenant."


_BASE_PROMPT = (
    "You answer questions about UK regulatory documents using ONLY the "
    "provided chunks. Populate cited_chunk_ids with the IDs of the "
    "chunks you used. If the chunks do not support an answer, say so "
    "explicitly and return an empty cited_chunk_ids list."
)


def _format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(
        f"[{c.id}] ({c.source} — {c.section_path})\n{c.chunk_text}" for c in chunks
    )


def _strict_retry_prompt(valid_ids: list[str]) -> str:
    return (
        _BASE_PROMPT + "\n\nYour previous answer cited chunk IDs not in the retrieved "
        f"set. You MUST cite ONLY IDs from this exact list: {', '.join(valid_ids)}. "
        "If the answer requires a chunk not in this list, say the available "
        "chunks do not support an answer and return an empty list."
    )


def _all_ids_valid(cited: list[str], valid: set[str]) -> bool:
    return all(cid in valid for cid in cited)


def make_generate_node(
    llm: LLMClient,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def generate(state: GraphState) -> dict[str, Any]:
        chunks = state.retrieved_chunks
        if not chunks:
            log.info(
                "generate: short-circuiting on empty retrieval "
                "(tenant=%s, question=%r)",
                state.tenant_id,
                state.question[:50],
            )
            return {"answer": EMPTY_RETRIEVAL_ANSWER, "citations": []}

        valid_ids = {c.id for c in chunks}
        chunks_by_id = {c.id: c for c in chunks}
        user_message = (
            f"Question: {state.question}\n\nChunks:\n{_format_chunks(chunks)}"
        )

        answer: Answer = await llm.complete(
            messages=[
                Message(role="system", content=_BASE_PROMPT),
                Message(role="user", content=user_message),
            ],
            schema=Answer,
        )

        if not _all_ids_valid(answer.cited_chunk_ids, valid_ids):
            log.warning(
                "generate: first attempt cited unknown chunk IDs %s; retrying",
                [cid for cid in answer.cited_chunk_ids if cid not in valid_ids],
            )
            answer = await llm.complete(
                messages=[
                    Message(
                        role="system",
                        content=_strict_retry_prompt(sorted(valid_ids)),
                    ),
                    Message(role="user", content=user_message),
                ],
                schema=Answer,
            )
            if not _all_ids_valid(answer.cited_chunk_ids, valid_ids):
                raise CitationContractError(
                    "LLM cited unknown chunk IDs after one retry; "
                    "refusing to return a hallucinated answer."
                )

        citations = [
            Citation(
                chunk_id=cid,
                source=chunks_by_id[cid].source,
                section_path=chunks_by_id[cid].section_path,
                score=chunks_by_id[cid].score,
            )
            for cid in answer.cited_chunk_ids
        ]
        return {"answer": answer.text, "citations": citations}

    return generate
