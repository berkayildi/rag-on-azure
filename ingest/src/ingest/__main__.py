"""CLI entrypoint for the ingest pipeline.

Subcommands:
    fetch   download sources declared in corpus_manifest.yaml to ingest/.cache/
    chunk   split fetched markdown into chunks.jsonl
    index   create the corpus index, embed chunks, upload to Azure AI Search
    all     run fetch -> chunk -> index in sequence
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest", description="rag-on-azure corpus pipeline"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="DEBUG-level logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="download sources to ingest/.cache/")
    sub.add_parser("chunk", help="split fetched markdown into chunks.jsonl")
    sub.add_parser("index", help="embed and upload chunks to Azure AI Search")
    sub.add_parser("all", help="fetch -> chunk -> index")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Lazy imports: a single phase being unimplemented or failing to import its
    # heavy deps must not break `--help` or the other subcommands.
    if args.command == "fetch":
        from ingest import fetch

        fetch.run()
    elif args.command == "chunk":
        from ingest import chunk

        chunk.run()
    elif args.command == "index":
        from ingest import index

        index.run()
    elif args.command == "all":
        from ingest import chunk, fetch, index

        fetch.run()
        chunk.run()
        index.run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
