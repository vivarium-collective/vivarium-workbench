"""Host a contributed launcher viewer under the workbench's own address.

A launcher viewer's ``launch`` returns the URL of a page it serves itself, often
on a loopback port of its own (``http://127.0.0.1:8334/#…``). Opening that URL
directly needs the browser to reach the port, which a workbench reached through a
tunnel, a proxy or another machine's browser cannot. A launch result that also
sets ``"proxy": True`` opts in to being hosted: the launch route swaps the URL
for ``<base>/api/analysis-viewer/<uid>/app/<key>/…`` and this module forwards
everything under that path to the viewer's own server.

Only loopback origins are hosted, so the forwarder cannot be pointed at another
host. ``key`` names the (workspace, viewer) pair; it is derived from both, so a
launch from the same workspace always gets the same address. The table lives in
memory: a link from before a workbench restart answers with a page saying to open
the viewer again. If the viewer's server has stopped, the forwarder launches it
once more (the contributor's ``launch`` starts it) and retries.

The viewer's page must request its own resources relative to itself (``api/x``,
not ``/api/x``): only then do they resolve under the hosted path.
"""
from __future__ import annotations

import hashlib
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator, NamedTuple
from urllib.parse import quote, urlsplit

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

# request headers passed to the viewer, and response headers passed back
_FORWARD_REQUEST = ("accept", "content-type", "range", "if-none-match", "if-modified-since")
_FORWARD_RESPONSE = ("content-type", "cache-control", "content-disposition", "etag",
                     "last-modified", "content-range", "accept-ranges", "location")
_CHUNK = 64 * 1024
_TIMEOUT_S = 300


class Hosted(NamedTuple):
    ws_root: Path
    uid: str
    origin: str        # http://127.0.0.1:<port>


_HOSTED: dict[str, Hosted] = {}
_LOCK = threading.Lock()


def _key(ws_root: Path, uid: str) -> str:
    return hashlib.sha256(f"{Path(ws_root).resolve()}\0{uid}".encode()).hexdigest()[:16]


def _loopback_origin(url: str) -> "tuple[str, str] | None":
    """``(origin, rest)`` for an ``http://<loopback>:<port>/…`` URL, else ``None``.
    ``rest`` is the path (without its leading slash), query and fragment."""
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return None
    if u.scheme != "http" or (u.hostname or "") not in LOOPBACK or port is None:
        return None
    host = "[::1]" if u.hostname == "::1" else u.hostname
    rest = u.path.lstrip("/")
    if u.query:
        rest += "?" + u.query
    if u.fragment:
        rest += "#" + u.fragment
    return f"http://{host}:{port}", rest


def prefix(uid: str, key: str, base_path: str = "") -> str:
    return f"{base_path}/api/analysis-viewer/{quote(uid, safe='')}/app/{key}/"


def host_url(ws_root: Path, uid: str, url: str, base_path: str = "") -> "str | None":
    """Record where ``uid`` serves for this workspace and return the hosted URL
    for ``url``, or ``None`` when ``url`` is not on a loopback port (left as is)."""
    found = _loopback_origin(url)
    if found is None:
        return None
    origin, rest = found
    key = _key(ws_root, uid)
    with _LOCK:
        _HOSTED[key] = Hosted(Path(ws_root), uid, origin)
    return prefix(uid, key, base_path) + rest


def lookup(uid: str, key: str) -> "Hosted | None":
    with _LOCK:
        h = _HOSTED.get(key)
    return h if h is not None and h.uid == uid else None


def relaunch(h: Hosted, key: str) -> "Hosted | None":
    """Launch the viewer again (its ``launch`` starts a stopped server) and record
    the origin it now serves on. ``None`` when the launch fails or leaves loopback."""
    from vivarium_workbench.lib.analysis_viewers import resolve_launch
    result = resolve_launch(h.ws_root, h.uid)
    found = _loopback_origin(str(result.get("url") or "")) if "error" not in result else None
    if found is None:
        return None
    new = Hosted(h.ws_root, h.uid, found[0])
    with _LOCK:
        _HOSTED[key] = new
    return new


class Upstream(NamedTuple):
    status: int
    headers: dict
    body: Generator[bytes, None, None]


def _iter(resp) -> Generator[bytes, None, None]:
    try:
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                return
            yield chunk
    finally:
        resp.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 — pass redirects back to the browser
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def forward(origin: str, subpath: str, query: str, method: str,
            headers: dict, body: "bytes | None") -> Upstream:
    """Send one request to the viewer's server and return its response, streamed.
    Raises ``ConnectionError`` (``URLError``'s reason) when the server is not there."""
    url = f"{origin}/{subpath}" + (f"?{query}" if query else "")
    fwd = {k: v for k, v in headers.items() if k.lower() in _FORWARD_REQUEST}
    req = urllib.request.Request(url, data=body if method != "GET" else None, method=method, headers=fwd)
    try:
        resp = _OPENER.open(req, timeout=_TIMEOUT_S)
    except urllib.error.HTTPError as e:      # a 4xx/5xx (or an unfollowed redirect) is still the viewer's answer
        resp = e
    except urllib.error.URLError as e:
        raise ConnectionError(str(e.reason)) from e
    out = {k: v for k, v in resp.headers.items() if k.lower() in _FORWARD_RESPONSE}
    return Upstream(resp.status if hasattr(resp, "status") else resp.code, out, _iter(resp))


GONE_PAGE = """<!doctype html><meta charset=utf-8><title>Open the viewer again</title>
<body style="font:15px system-ui;max-width:36em;margin:4em auto;padding:0 1em">
<h2>Open this viewer again from the workbench</h2>
<p>{why}</p>
<p>Open it again from the viewer's card on the Analyses page, or from a run's
Tools column.</p></body>"""
