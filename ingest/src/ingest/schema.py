"""Definition of the ``corpus`` Azure AI Search index.

The schema lives in Python, not Bicep, because index shape is application data —
its lifecycle ties to the app, not to the cloud resource. See ``README.md``
(architectural note) and ``docs/design/rag-on-azure.md`` §2.2.

Implemented in Phase E of Day 3.
"""

from __future__ import annotations

from typing import Any


def build_index() -> Any:
    raise NotImplementedError(
        "ingest.schema.build_index is not yet implemented (Phase E)"
    )
