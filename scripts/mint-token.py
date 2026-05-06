#!/usr/bin/env python3
"""Mint a dev JWT for local API testing.

Two modes:

- Default (no ``--signing-key-path``): emit an unsigned token (alg=none).
  Only valid against an environment running with ``ENABLE_DEV_AUTH=true``.
  A warning is printed to stderr; stdout still gets the token so shell
  ``$()`` capture works as before.
- ``--signing-key-path <path>``: read a PEM-encoded RSA private key from
  the given path and sign the token with RS256. Required for any
  environment running ``ENABLE_DEV_AUTH=false`` (i.e. the production
  signature-verification path). The matching public PEM must be the
  current value of the ``jwt-signing-key`` Key Vault secret.

This script is local-dev tooling only — never the production token
issuer. See AGENTS.md "Local dev tools" for the one-time keypair
generation + ``az keyvault secret set`` setup.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

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
    parser.add_argument(
        "--signing-key-path",
        type=Path,
        default=None,
        help=(
            "Path to a PEM-encoded RSA private key. When supplied, the "
            "token is signed with RS256. When absent, the token is "
            "unsigned (alg=none) — only valid against ENABLE_DEV_AUTH=true."
        ),
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

    if args.signing_key_path is not None:
        private_pem = args.signing_key_path.read_text()
        token = jwt.encode(claims, private_pem, algorithm="RS256")
    else:
        print(
            "WARNING: minting unsigned token (alg=none) — only valid against "
            "ENABLE_DEV_AUTH=true environments. Pass --signing-key-path to "
            "produce a signed RS256 token for verified-mode testing.",
            file=sys.stderr,
        )
        token = jwt.encode(claims, key="", algorithm="none")

    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
