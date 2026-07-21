"""bigraph-loom — read-only React Flow viewer for process-bigraph composites.

This Python package is a thin wrapper around the built front-end bundle. The
JavaScript app (``src/``, built with Vite) compiles into ``bigraph_loom/_dist``,
which this package ships as package data. Host applications (e.g.
vivarium-dashboard) depend on ``bigraph-loom`` and serve the static bundle from
:func:`asset_dir`, rather than vendoring a copy of the build.

Dev loop: edit ``src/``, run ``npm run build`` (refreshes ``bigraph_loom/_dist``
in place); an editable install picks the new bundle up immediately.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["asset_dir", "index_html", "__version__"]

__version__ = "0.1.0"


def asset_dir() -> Path:
    """Absolute path to the built static bundle directory (``index.html`` + ``assets/``).

    Serve the files under this directory at whatever URL prefix the host uses
    (vivarium-dashboard mounts them at ``/loom-explore``). Works for both
    editable and wheel installs because ``_dist`` lives inside the package dir.
    """
    return Path(__file__).resolve().parent / "_dist"


def index_html() -> Path:
    """Absolute path to the bundle's ``index.html`` entry point."""
    return asset_dir() / "index.html"
