"""Locate the vendored bigraph-loom built bundle (_dist)."""
from pathlib import Path


def asset_dir() -> Path:
    return Path(__file__).resolve().parent / "loom" / "_dist"
