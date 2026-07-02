"""Launch the typed FastAPI seam so you can browse the Swagger UI.

    python -m vivarium_dashboard.api --workspace /path/to/workspace

Then open the printed URLs:
    /docs           Swagger UI (interactive, try-it-out)
    /redoc          ReDoc (reference-style)
    /openapi.json   the raw OpenAPI schema

This serves ONLY the ported, typed routes (see ``app.py``); the legacy stdlib
server still serves the full dashboard. It exists so the generated endpoints are
easy to view and exercise as the strangler-fig migration proceeds.
"""

from __future__ import annotations

import argparse
import os

from vivarium_dashboard.api.app import WORKSPACE_ENV


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m vivarium_dashboard.api",
        description="Serve the typed dashboard API and its Swagger UI (/docs).",
    )
    parser.add_argument(
        "--workspace", "-w", default=None,
        help="Workspace root to serve (default: $%s or the current dir)." % WORKSPACE_ENV,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    parser.add_argument("--port", "-p", type=int, default=8001, help="Bind port (default 8001).")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes.")
    args = parser.parse_args()

    if args.workspace:
        os.environ[WORKSPACE_ENV] = args.workspace

    import uvicorn

    base = f"http://{args.host}:{args.port}"
    print(f"vivarium-workbench typed API — workspace: {os.environ.get(WORKSPACE_ENV, '.')}")
    print(f"  Swagger UI : {base}/docs")
    print(f"  ReDoc      : {base}/redoc")
    print(f"  OpenAPI    : {base}/openapi.json")
    # Import string (not the app object) so --reload works.
    uvicorn.run("vivarium_dashboard.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
