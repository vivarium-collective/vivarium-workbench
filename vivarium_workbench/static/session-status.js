// session-status.js — favicon + title by materialization status (session-per-tab
// slice 3c).
//
// A tab is one workspace (pinned for life). This encodes that workspace's env
// state in the browser TAB itself — the favicon + title — so you can tell a
// preparing / failed tab from a ready one WITHOUT switching to it. Status comes
// from GET /api/source/materialization (materialization-lifecycle §4):
//
//   ready         → the workbench mark, plain title
//   materializing → hourglass favicon + "⏳ <title>", and POLL until it settles
//   failed        → red favicon + "⚠ <title>"
//
// A plain local workspace is `ready` at once (no poll). Managed/hosted sources
// clone + uv sync for minutes, so the hourglass is their progress surface. In a
// published snapshot (no live endpoint) the fetch just fails → ready mark, no poll.
(function () {
  "use strict";

  var POLL_MS = 2500;

  function svgDataUri(inner) {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
      inner + '</svg>';
    return 'data:image/svg+xml,' + encodeURIComponent(svg);
  }

  // Consistent rounded-square marks so the three states read as one family.
  var FAVICONS = {
    ready: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#157a70"/>' +
      '<text x="16" y="23" font-family="system-ui,sans-serif" font-size="20"' +
      ' font-weight="700" fill="#fff" text-anchor="middle">V</text>'),
    preparing: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#f4e6c8"/>' +
      '<text x="16" y="25" font-size="20" text-anchor="middle">⏳</text>'),
    failed: svgDataUri(
      '<rect width="32" height="32" rx="7" fill="#c14a34"/>' +
      '<text x="16" y="24" font-family="system-ui,sans-serif" font-size="23"' +
      ' font-weight="800" fill="#fff" text-anchor="middle">!</text>'),
  };

  function setFavicon(uri) {
    var link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      (document.head || document.documentElement).appendChild(link);
    }
    link.type = "image/svg+xml";
    link.href = uri;
  }

  // Capture the server-rendered title once (stripped of any status glyph) so we
  // can prefix/restore without compounding.
  var baseTitle = null;
  function setTitlePrefix(prefix) {
    if (baseTitle === null) {
      baseTitle = String(document.title || "").replace(/^[⏳⚠️!]+\s*/, "");
    }
    document.title = prefix ? prefix + " " + baseTitle : baseTitle;
  }

  // Map a materialization status → the tab treatment. Returns the normalized
  // state ('ready' | 'preparing' | 'failed').
  function apply(status) {
    if (status === "materializing" || status === "preparing") {
      setFavicon(FAVICONS.preparing);
      setTitlePrefix("⏳");
      return "preparing";
    }
    if (status === "failed") {
      setFavicon(FAVICONS.failed);
      setTitlePrefix("⚠️");
      return "failed";
    }
    setFavicon(FAVICONS.ready);
    setTitlePrefix("");
    return "ready";
  }

  function poll() {
    fetch("/api/source/materialization")
      .then(function (r) { return r && r.ok ? r.json() : null; })
      .then(function (d) {
        var state = apply((d && d.status) || "ready");
        if (state === "preparing") setTimeout(poll, POLL_MS);
      })
      .catch(function () { apply("ready"); });
  }

  var api = { apply: apply, poll: poll, svgDataUri: svgDataUri, FAVICONS: FAVICONS };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;                 // Node (tests): no auto-run
  } else {
    window.vivSessionStatus = api;        // browser: run on load
    if (document.readyState !== "loading") poll();
    else document.addEventListener("DOMContentLoaded", poll);
  }
})();
