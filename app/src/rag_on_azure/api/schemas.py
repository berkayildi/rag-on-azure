"""Pydantic models for the FastAPI surface and auth boundary.

Wire contract for ``POST /query`` (design spec §3.4) plus the in-process
``TenantContext`` returned by the ``get_current_tenant`` auth dependency
(§3.5). ``TenantContext`` is never serialised to clients — it is the
typed handoff between ``auth.py`` and the route handler — but it lives
here so every Pydantic-shaped boundary type is in one place.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictFloat


class RagRequest(BaseModel):
    """``POST /query`` request body.

    ``tenant_id`` is intentionally absent: it flows from the JWT via
    ``get_current_tenant`` and is never accepted from the wire (design
    spec §3.5, §5.3 audit-grade invariant).
    """

    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class Citation(BaseModel):
    chunk_id: str
    source: str
    section_path: str
    # StrictFloat: a malformed upstream score can't silently land as a string.
    score: StrictFloat


class RagResponse(BaseModel):
    answer: str
    citations: list[Citation]
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantContext(BaseModel):
    """Typed handoff from the auth dependency into the route handler.

    Constructed only inside ``get_current_tenant`` from validated JWT
    claims. The ``tenant_id`` here is the sole source of truth used to
    construct ``GraphState.tenant_id`` downstream.
    """

    tenant_id: str
    is_admin: bool = False
