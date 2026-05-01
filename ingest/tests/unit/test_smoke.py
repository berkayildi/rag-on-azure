"""Smoke tests proving the package installs and submodules import cleanly."""

from __future__ import annotations


def test_package_imports() -> None:
    import ingest

    assert ingest.__version__ == "0.0.0"


def test_submodules_import() -> None:
    from ingest import chunk, clients, fetch, index, schema  # noqa: F401


def test_cli_help_exits_cleanly() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "ingest", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "fetch" in result.stdout
    assert "chunk" in result.stdout
    assert "index" in result.stdout
    assert "all" in result.stdout
