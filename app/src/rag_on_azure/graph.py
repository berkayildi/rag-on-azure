"""LangGraph workflow construction — linear understand → retrieve → generate.

See ``docs/design/rag-on-azure.md`` §3.2.

``build_graph`` is called once at app startup with the canonical
``LLMClient`` + ``TenantAwareSearchClient`` injected; the FastAPI
handler in Phase C constructs a ``GraphState`` (with ``tenant_id``
sourced from the JWT via ``get_current_tenant``) and ``ainvoke``s the
compiled graph. The graph itself is auth-agnostic — it never sees
the request, the JWT, or the auth dependency.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from rag_on_azure.clients.llm import LLMClient
from rag_on_azure.clients.search import TenantAwareSearchClient
from rag_on_azure.nodes.generate import make_generate_node
from rag_on_azure.nodes.retrieve import make_retrieve_node
from rag_on_azure.nodes.understand import make_understand_node
from rag_on_azure.state import GraphState

# langgraph 0.6 types add_node against an internal `_Node[NodeInputT]`
# union covering several Pregel-shaped variants. mypy --strict can't
# bridge our plain async `Callable[[GraphState], Awaitable[dict[str, Any]]]`
# against that union, even though the runtime contract matches. The
# narrow type: ignore on each add_node call is the lowest-cost
# acknowledgment of that gap; the same pattern shows up in langgraph's
# own examples. CompiledStateGraph is parameterised with Any-quads for
# the same reason — its 4 type parameters (state, config, input,
# output) are private generics that don't add value here.


def build_graph(
    llm: LLMClient,
    search: TenantAwareSearchClient,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    workflow = StateGraph(GraphState)
    workflow.add_node("understand", make_understand_node(llm))  # type: ignore[call-overload]
    workflow.add_node("retrieve", make_retrieve_node(llm, search))  # type: ignore[call-overload]
    workflow.add_node("generate", make_generate_node(llm))  # type: ignore[call-overload]
    workflow.add_edge(START, "understand")
    workflow.add_edge("understand", "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    return workflow.compile()
