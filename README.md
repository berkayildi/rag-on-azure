# rag-on-azure

A reference implementation of a production-grade RAG (Retrieval-Augmented Generation) application on Microsoft Azure: Bicep IaC, FastAPI + LangGraph, multi-tenant via JWT-driven filters, CI quality-gated by `mcp-llm-eval`. Intended to be forked or used as architectural reference for teams building RAG on the Azure stack.

> **Status: under construction.** The repository is currently being scaffolded — directory structure, tooling, and CI plumbing are landing first; deployable Bicep and application code follow in subsequent commits.

## Source of truth

The canonical architecture, scope, naming, and security model live in [`docs/design/rag-on-azure.md`](docs/design/rag-on-azure.md). That document is the single source of truth for every decision in this repo.

## Licence

Released under the [MIT Licence](LICENSE).
