#!/usr/bin/env python3
"""Mint a dev JWT for local API testing.

Local dev only. Day 6 hardens ``app/src/rag_on_azure/auth.py`` to fetch
a Key Vault key and verify signatures; this script will continue to
mint tokens for local testing only — it is never the production
issuer. The algorithm is deliberately ``"none"`` (unsigned) on Day 5
so the dev path is honest about what verification it skips.

Example:

    TOKEN=$(python scripts/mint-token.py --tenant-id demo)
    curl -H "Authorization: Bearer $TOKEN" \\
         -d '{"question":"What does CASS 7 require?"}' \\
         http://localhost:8000/query
"""

from __future__ import annotations

import argparse
import sys
import time

import jwt


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mint a dev JWT for local API testing."
    )
    parser.add_argument(
        "--tenant-id",
        required=True,
        help="Tenant identifier (becomes the JWT tenant_id claim).",
    )
    parser.add_argument(
        "--tenant-admin",
        action="store_true",
        help="Mint a token with tenant_admin=True (default: False).",
    )
    parser.add_argument(
        "--user",
        default="user-local",
        help="Value for the JWT sub claim (default: user-local).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Token lifetime in hours (default: 24).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": args.user,
        "tenant_id": args.tenant_id,
        "iat": now,
        "exp": now + args.hours * 3600,
        "tenant_admin": args.tenant_admin,
    }
    token = jwt.encode(claims, key="", algorithm="none")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
