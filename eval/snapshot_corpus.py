"""Snapshot the live AI Search corpus index to a JSONL file in mcp-llm-eval's
expected corpus shape.

Used by the CI eval-gate job: dump the deployed dev index for one tenant, then
hand the JSONL to ``mcp-llm-eval evaluate-rag --corpus``. mcp-llm-eval's BM25
adapter retokenises content client-side, so we omit the embedding vector and
only export id / chunk_text / metadata.

mcp-llm-eval corpus row shape (per ``BM25Adapter`` in ``mcp_llm_eval.retrieval``):
    {"chunk_id": str, "content": str, "metadata": {...}}

Authentication uses ``DefaultAzureCredential``: ``az login`` locally, OIDC
federation in CI. The principal needs Search Index Data Reader on the search
service.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

_TENANT_ID_RE = re.compile(r"^[a-z0-9-]+$")
_SELECT_FIELDS = ["id", "tenant_id", "source", "section_path", "chunk_text"]


def _escape_odata(s: str) -> str:
    return s.replace("'", "''")


def snapshot(endpoint: str, index_name: str, tenant_id: str, output_path: Path) -> int:
    if not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError(
            f"tenant_id {tenant_id!r} must match ^[a-z0-9-]+$ "
            "(lowercase alphanumeric and hyphen only)"
        )

    credential = DefaultAzureCredential()
    client = SearchClient(
        endpoint=endpoint, index_name=index_name, credential=credential
    )

    odata_filter = f"tenant_id eq '{_escape_odata(tenant_id)}'"
    results = client.search(
        search_text="*",
        filter=odata_filter,
        select=_SELECT_FIELDS,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    seen_ids: set[str] = set()
    with output_path.open("w", encoding="utf-8") as f:
        for doc in results:
            doc_id = doc["id"]
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            row = {
                "chunk_id": doc_id,
                "content": doc["chunk_text"],
                "metadata": {
                    "source": doc.get("source", ""),
                    "section_path": doc.get("section_path", "") or "",
                    "tenant_id": doc["tenant_id"],
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="snapshot_corpus",
        description="Dump the AI Search corpus index for one tenant as mcp-llm-eval JSONL.",
    )
    parser.add_argument(
        "--endpoint", required=True, help="https://<service>.search.windows.net"
    )
    parser.add_argument(
        "--index", default="corpus", help="Index name (default: corpus)"
    )
    parser.add_argument(
        "--tenant", required=True, help="Tenant id to filter on (e.g. 'demo')"
    )
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path")
    args = parser.parse_args(argv)

    written = snapshot(args.endpoint, args.index, args.tenant, args.output)
    if written == 0:
        print(
            f"snapshot_corpus: 0 chunks for tenant={args.tenant!r} at {args.endpoint}",
            file=sys.stderr,
        )
        return 1
    print(f"snapshot_corpus: {written} chunks -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
