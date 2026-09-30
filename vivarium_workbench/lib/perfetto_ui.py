"""The Perfetto trace viewer the workbench opens a remote run's trace in.

Perfetto's UI (https://perfetto.dev, Apache-2.0, Google) is a static web app that
runs entirely in the browser: a trace handed to it with ``postMessage`` is parsed
by its in-page WebAssembly trace processor and never leaves the machine. The
workbench can reach it two ways, chosen by ``VIVARIUM_WORKBENCH_PERFETTO_UI``:

``auto`` (the default)
    the **bundled** copy when one is installed (see below), else the public
    ``https://ui.perfetto.dev``.
``bundled``
    only the bundled copy, served by this server at ``/perfetto/`` (under the
    base path). Nothing is fetched from outside, which is what a GovCloud network
    that cannot reach Google needs. Without a bundle there is no viewer, and the
    trace action falls back to downloading the JSON.
``off``
    no viewer; the action downloads the trace JSON.
an ``http(s)://`` URL
    a Perfetto UI hosted elsewhere (``https://ui.perfetto.dev`` or a mirror).

**The bundle** is a pinned Perfetto UI release, mirrored file-for-file from
``ui.perfetto.dev/<version>/`` by :func:`fetch_bundle` (``python -m
vivarium_workbench.lib.perfetto_ui``; the image build runs it). Every file is
verified: ``index.html`` and ``manifest.json`` against hashes pinned here, and
every resource the manifest lists against the SHA-256 the manifest itself
carries -- so the chain of trust starts at this file, and a moved or tampered
release fails the build instead of shipping. Perfetto's ``LICENSE`` (Apache-2.0,
from the release commit, also hash-pinned) is written beside it; the project
has no ``NOTICE`` file. The bundle is ~64 MB, which is why it is fetched into
the image, never committed or put in the wheel.

A pinned version directory is self-contained: its ``index.html`` carries no
channel map and loads ``./frontend_bundle.js`` relative to itself, so it works
under any sub-path (``/workbench/perfetto/``) -- with one exception, which
:func:`served_bytes` corrects. ``frontend.css`` declares each font twice: once
correctly (``url(assets/Roboto.woff2)``, relative to the stylesheet) and again,
from stylesheets compiled deeper in Perfetto's source tree, as
``url(../assets/assets/Roboto.woff2)``, ``url(../../assets/assets/…)`` and
``url(../../../../assets/assets/…)``. Those resolve OUTSIDE the release directory
(``/assets/assets/…`` -- a 404 on ``ui.perfetto.dev`` itself too) and here to
``<base>/assets/assets/…`` and ``<origin>/assets/assets/…``: outside ``/perfetto/``
and, for the second, outside the workbench altogether. No directory layout reaches
both depths, so the stylesheet is served with those URLs pointed back at the
bundle's own ``assets/`` file -- only for files that exist there. The file on disk
stays byte-for-byte the verified one; the correction is applied as it is served.

Served from the workbench's own origin, a posted trace is trusted without
Perfetto's "Open trace?" prompt (Perfetto trusts ``window.origin``);
``ui.perfetto.dev`` asks once per origin.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen

from vivarium_workbench.lib.env_compat import get_env

#: The pinned release: ``ui.perfetto.dev/<PERFETTO_UI_VERSION>/``. Its suffix is the
#: short hash of PERFETTO_UI_COMMIT (google/perfetto, tag v58.3's stable UI build).
PERFETTO_UI_VERSION = "v58.3-11fbaed83"
PERFETTO_UI_COMMIT = "11fbaed836361258f23604daf67c837f3034e663"
PERFETTO_UI_ORIGIN = "https://ui.perfetto.dev"

#: Hex SHA-256 of the two files the manifest does not cover, and of the licence.
#: Bump all three (and the version) together; ``--print-hashes`` computes them.
INDEX_SHA256 = "33ae569b0cfd7ed66b2be9f649de4aaadae0dd42177643a552460a6225d2da99"
MANIFEST_SHA256 = "0e35cfc8227ed7fdd24eab7da24f91e9cd9e44517ac68ee8b359c3879b4345cb"
LICENSE_SHA256 = "9a682a56cffc9524dfa9b0b1c0dca9cb81a19e96d5bd0793aaf02c08a95ee7ca"
LICENSE_URL = f"https://raw.githubusercontent.com/google/perfetto/{PERFETTO_UI_COMMIT}/LICENSE"

#: Written last by :func:`fetch_bundle`; a directory without it is not a bundle
#: (an interrupted fetch never looks installed).
STAMP_NAME = ".vivarium-workbench-perfetto.json"

MODE_ENV = "PERFETTO_UI"          # VIVARIUM_WORKBENCH_PERFETTO_UI
DIR_ENV = "PERFETTO_UI_DIR"       # VIVARIUM_WORKBENCH_PERFETTO_UI_DIR

#: The path the bundle is served at, relative to the workbench's base path.
SERVE_PREFIX = "/perfetto"


class BundleVerificationError(RuntimeError):
    """A fetched file did not match its pinned or manifest hash."""


def default_bundle_dir() -> Path:
    """Where the bundle lives when ``VIVARIUM_WORKBENCH_PERFETTO_UI_DIR`` is unset."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vivarium-workbench" / "perfetto-ui" / PERFETTO_UI_VERSION


def bundle_dir() -> Path:
    configured = get_env(DIR_ENV)
    return Path(configured) if configured else default_bundle_dir()


def installed_version(directory: "Path | None" = None) -> Optional[str]:
    """The version of the bundle at ``directory`` (default :func:`bundle_dir`), or ``None``."""
    d = directory if directory is not None else bundle_dir()
    try:
        stamp = json.loads((d / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (d / "index.html").is_file():
        return None
    version = stamp.get("version") if isinstance(stamp, dict) else None
    return str(version) if version else None


@dataclass(frozen=True)
class ViewerConfig:
    """How this server offers Perfetto. ``url`` is ``None`` when there is no viewer."""

    mode: str                     # "bundled" | "external" | "off"
    url: Optional[str]            # bundled: relative to the base path ("/perfetto/")
    version: Optional[str] = None

    def as_dict(self) -> dict:
        return {"mode": self.mode, "url": self.url, "version": self.version}


def viewer_config() -> ViewerConfig:
    """Resolve ``VIVARIUM_WORKBENCH_PERFETTO_UI`` (see the module docstring)."""
    raw = (get_env(MODE_ENV) or "auto").strip()
    choice = raw.lower()
    if choice == "off":
        return ViewerConfig(mode="off", url=None)
    if choice.startswith(("http://", "https://")):
        return ViewerConfig(mode="external", url=raw.rstrip("/") + "/")
    version = installed_version()
    if version is not None and choice in ("auto", "bundled"):
        return ViewerConfig(mode="bundled", url=SERVE_PREFIX + "/", version=version)
    if choice == "bundled":
        return ViewerConfig(mode="off", url=None)
    # "auto" without a bundle, or an unrecognised value: the public UI.
    return ViewerConfig(mode="external", url=PERFETTO_UI_ORIGIN + "/")


class AssetTraversal(Exception):
    """A ``..`` segment in a requested bundle path (the route answers 403)."""


def resolve_asset(rel: str) -> Optional[Path]:
    """The bundle file for ``rel`` (``""`` → ``index.html``), or ``None`` without a bundle."""
    rel = rel or "index.html"
    if ".." in rel.split("/") or rel.startswith("/"):
        raise AssetTraversal(rel)
    d = bundle_dir()
    # Allowlist, not just the denylist above: whatever ``rel`` is, the file it names must
    # resolve INSIDE the bundle directory -- which also stops a symlink in the bundle from
    # pointing out of it, and keeps holding if the join logic ever changes.
    root = d.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise AssetTraversal(rel)
    if installed_version(d) is None:
        return None
    return target


_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".wasm": "application/wasm",   # required for WebAssembly.instantiateStreaming
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
}


def mime_for(rel: str) -> str:
    return _MIME.get(Path(rel).suffix.lower(), "application/octet-stream")


#: A font URL in ``frontend.css`` that climbs out of the release directory (see the
#: module docstring): any number of ``../`` then ``assets/assets/<file>``.
_ESCAPING_ASSET_URL = re.compile(
    r"""url\((?P<q>['"]?)(?:\.\./)+assets/assets/(?P<name>[A-Za-z0-9_-][A-Za-z0-9_.-]*)(?P=q)\)""")


def fix_stylesheet(css: str, bundle: Path) -> str:
    """``css`` with every escaping ``…/assets/assets/<file>`` URL pointed at the bundle's
    ``assets/<file>`` (relative to the stylesheet, as the correct declarations already are).
    A URL naming a file the bundle does not have is left alone -- the rewrite only ever
    points at files that are in the verified bundle."""
    assets = bundle / "assets"

    def _sub(m: "re.Match[str]") -> str:
        name = m.group("name")
        if not (assets / name).is_file():
            return m.group(0)
        q = m.group("q")
        return f"url({q}assets/{name}{q})"

    return _ESCAPING_ASSET_URL.sub(_sub, css)


def served_bytes(rel: str, target: Path) -> Optional[bytes]:
    """The body to serve for the bundle file ``target`` when it differs from the file,
    else ``None`` (serve the file as-is). Only a top-level stylesheet differs: see
    :func:`fix_stylesheet`."""
    if "/" in rel or not rel.endswith(".css"):
        return None
    return fix_stylesheet(target.read_text(encoding="utf-8"), target.parent).encode("utf-8")


# --------------------------------------------------------------------------- fetch

Opener = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    with urlopen(url, timeout=120) as r:  # noqa: S310 -- fixed https URLs pinned above
        return bytes(r.read())


def _sri_sha256(data: bytes) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


def _check_hex(name: str, data: bytes, expected: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if got != expected:
        raise BundleVerificationError(f"{name}: sha256 {got} != pinned {expected}")


def fetch_bundle(dest: Path, *, opener: Opener = _http_get,
                 base_url: str = PERFETTO_UI_ORIGIN) -> Path:
    """Mirror the pinned Perfetto UI into ``dest``, verifying every file.

    Builds into a sibling temp directory and renames it into place only when
    every file verified, so ``dest`` is either the previous bundle or a complete
    new one. Returns ``dest``. Idempotent: an existing bundle of the pinned
    version is left alone.
    """
    dest = Path(dest)
    if installed_version(dest) == PERFETTO_UI_VERSION:
        return dest
    root = f"{base_url.rstrip('/')}/{PERFETTO_UI_VERSION}/"
    index = opener(root + "index.html")
    _check_hex("index.html", index, INDEX_SHA256)
    manifest_bytes = opener(root + "manifest.json")
    _check_hex("manifest.json", manifest_bytes, MANIFEST_SHA256)
    licence = opener(LICENSE_URL)
    _check_hex("LICENSE", licence, LICENSE_SHA256)
    resources = json.loads(manifest_bytes.decode("utf-8")).get("resources") or {}

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".perfetto-", dir=dest.parent))
    try:
        (tmp / "index.html").write_bytes(index)
        (tmp / "manifest.json").write_bytes(manifest_bytes)
        (tmp / "LICENSE").write_bytes(licence)
        total = len(index) + len(manifest_bytes)
        for rel, sri in sorted(resources.items()):
            if ".." in rel.split("/") or rel.startswith("/"):
                raise BundleVerificationError(f"manifest names an unsafe path: {rel!r}")
            data = opener(root + rel)
            if _sri_sha256(data) != sri:
                raise BundleVerificationError(f"{rel}: {_sri_sha256(data)} != manifest {sri}")
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            total += len(data)
        (tmp / STAMP_NAME).write_text(json.dumps({
            "version": PERFETTO_UI_VERSION, "commit": PERFETTO_UI_COMMIT,
            "source": root, "files": len(resources) + 2, "bytes": total,
        }, indent=2) + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def _print_hashes(opener: Opener = _http_get) -> None:
    root = f"{PERFETTO_UI_ORIGIN}/{PERFETTO_UI_VERSION}/"
    for name, url in (("INDEX_SHA256", root + "index.html"),
                      ("MANIFEST_SHA256", root + "manifest.json"),
                      ("LICENSE_SHA256", LICENSE_URL)):
        print(f'{name} = "{hashlib.sha256(opener(url)).hexdigest()}"')


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m vivarium_workbench.lib.perfetto_ui",
        description=f"Fetch and verify the pinned Perfetto UI ({PERFETTO_UI_VERSION}).")
    p.add_argument("--dest", type=Path, default=None,
                   help="target directory (default: $VIVARIUM_WORKBENCH_PERFETTO_UI_DIR "
                        f"or {default_bundle_dir()})")
    p.add_argument("--print-hashes", action="store_true",
                   help="print the pinned-hash constants for PERFETTO_UI_VERSION and exit "
                        "(for bumping the version)")
    args = p.parse_args(argv)
    if args.print_hashes:
        _print_hashes()
        return 0
    dest = args.dest if args.dest is not None else bundle_dir()
    try:
        fetch_bundle(dest)
    except BundleVerificationError as e:
        print(f"perfetto-ui: verification FAILED: {e}", file=sys.stderr)
        return 1
    print(f"perfetto-ui {PERFETTO_UI_VERSION} installed at {dest}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
