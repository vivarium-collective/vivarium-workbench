/* Global search palette — a pop-out search window opened from the rail magnifier
 * (and ⌘K / Ctrl-K). Replaces the old inline "search studies" bar with one query
 * box that searches ACROSS studies, investigations, catalog (composites),
 * registry (processes), runs and resources (inputs + references), with results
 * grouped by type and a keyboard-navigable list.
 *
 * Self-contained: builds its own overlay DOM and drives everything through the
 * window-exported SPA globals (_marketOpen, _switchPage, _loadMarket,
 * _marketItems, _investigations, _isetIndex, _simRows) + DataSource. Because every
 * source is read client-side, it works in BOTH live and published (snapshot) mode
 * exactly like the old study filter did. Knowledge/how-tos is intentionally not a
 * source yet (no searchable corpus in the workbench) — the index is source-
 * pluggable so it can be added later.
 */
(function () {
  "use strict";

  var GROUP_ORDER = ["Studies", "Investigations", "Catalog", "Registry", "Runs", "Resources", "Explore"];
  var PER_GROUP = 6; // cap rows shown per group (with a "+N more" note)

  var overlay, input, resultsEl, loadingEl;
  var _built = false;
  var _sourcesKicked = false;
  var _pollTimer = null;
  var _extra = { runs: null, inputs: null, refs: null, analysis: null };
  var _rows = [];   // flat list of currently-rendered selectable rows {item, el}
  var _sel = -1;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tokenize(q) {
    q = String(q || "").trim().toLowerCase();
    return q ? q.split(/\s+/) : [];
  }
  function matchAllThenAny(hay, tokens) {
    if (!tokens.length) return true;
    var all = tokens.every(function (t) { return hay.indexOf(t) !== -1; });
    return all || tokens.some(function (t) { return hay.indexOf(t) !== -1; });
  }
  function hayOf(parts) {
    return parts.filter(Boolean).map(String).join(" ").toLowerCase();
  }
  function fetchJSON(url) {
    try {
      return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }
  function ds() { return window.DataSource; }

  // ---- source loading (once; re-render as each lands) -----------------------
  function kickSources() {
    if (_sourcesKicked) return;
    _sourcesKicked = true;

    // Catalog + Registry come from the federated market index; populate it if the
    // Catalog page hasn't been visited yet (safe to call off-page).
    if ((!window._marketItems || !window._marketItems.length) && typeof window._loadMarket === "function") {
      try { window._loadMarket(); } catch (e) {}
    }
    var D = ds();
    if (!D) return;

    // Runs: prefer already-loaded rows; else the baked/live simulations list.
    if (!(window._simRows && window._simRows.length) && D.simulationsUrl) {
      fetchJSON(D.simulationsUrl()).then(function (j) {
        _extra.runs = (j && (j.simulations || j.rows || j.runs)) || (Array.isArray(j) ? j : []);
        refresh();
      });
    }
    // Resources: workspace inputs/sources.
    if (D.loadInputs) {
      try {
        D.loadInputs("").then(function (j) { _extra.inputs = j; refresh(); }).catch(function () {});
      } catch (e) {}
    }
    // References: parsed papers.bib.
    if (D.referencesBibUrl) {
      fetchJSON(D.referencesBibUrl()).then(function (j) { _extra.refs = j; refresh(); });
    }
    // Analysis: the analysis-viewers/tools.
    if (D.analysisViewersUrl) {
      fetchJSON(D.analysisViewersUrl()).then(function (j) { _extra.analysis = j; refresh(); });
    }
  }

  // ---- normalize every source into {group, tag, title, sub, hay, open} ------
  function buildIndex() {
    var out = [];

    // Investigations
    (window._isetIndex || []).forEach(function (i) {
      if (!i || !i.name) return;
      out.push({
        group: "Investigations", tag: "Investig.",
        title: i.title || i.name,
        sub: i.question || i.status || ((i.studies || []).length + " studies"),
        hay: hayOf([i.name, i.title, i.status, i.question]),
        open: function () { window._marketOpen && window._marketOpen("investigation", i.name); }
      });
    });

    // Studies
    (window._investigations || []).forEach(function (s) {
      if (!s || !s.name) return;
      out.push({
        group: "Studies", tag: "Study",
        title: s.title || s.name,
        sub: s.question || s.objective || s.status || "",
        hay: hayOf([s.name, s.title, s.slug, s.objective, s.question, s.description,
                    s.summary, s.status, Array.isArray(s.tags) ? s.tags.join(" ") : ""]),
        open: function () { window._marketOpen && window._marketOpen("study", s.name); }
      });
    });

    // Catalog (composites) + Registry (processes/steps) from the market index
    (window._marketItems || []).forEach(function (it) {
      if (!it || !it.name) return;
      if (it.type === "composite") {
        out.push({
          group: "Catalog", tag: "Composite",
          title: it.title || it.name, sub: it.desc || it.repo || "",
          hay: hayOf([it.name, it.title, it.desc, it.repo]),
          open: function () { window._marketOpen && window._marketOpen("composite", it.name); }
        });
      } else if (it.type === "process") {
        out.push({
          group: "Registry", tag: (it.kind === "step" ? "Step" : "Process"),
          title: it.title || it.name, sub: it.desc || it.address || it.repo || "",
          hay: hayOf([it.name, it.title, it.desc, it.address, it.repo]),
          open: function () { window._marketOpen && window._marketOpen("process", it.name); }
        });
      }
    });

    // Runs
    var runRows = (window._simRows && window._simRows.length) ? window._simRows : (_extra.runs || []);
    runRows.forEach(function (r) {
      if (!r) return;
      var id = r.run_id || r.label || r.sim_name;
      if (!id) return;
      out.push({
        group: "Runs", tag: "Run",
        title: r.label || r.sim_name || r.run_id,
        sub: [r.study, r.status].filter(Boolean).join(" · "),
        hay: hayOf([r.study, r.investigation, r.status, r.emitter_type, r.sim_name, r.label, r.run_id, r.spec_id]),
        open: function () { openRun(r); }
      });
    });

    // Resources — inputs / data sources (shape is permissive)
    collectList(_extra.inputs, ["inputs", "sources", "data_sources", "items"]).forEach(function (x) {
      var title = x.title || x.name || x.id || x.key || x.label;
      if (!title) return;
      out.push({
        group: "Resources", tag: "Resource",
        title: title, sub: x.description || x.kind || x.type || x.path || "",
        hay: hayOf([title, x.description, x.kind, x.type, x.path, x.id, x.key]),
        open: function () { goPage("resources"); }
      });
    });
    // Resources — references (papers.bib)
    collectList(_extra.refs, ["references", "entries", "refs", "items"]).forEach(function (x) {
      var title = x.title || x.key || x.id;
      if (!title) return;
      var authors = Array.isArray(x.authors) ? x.authors.join(", ") : (x.authors || x.author || "");
      out.push({
        group: "Resources", tag: "Reference",
        title: title, sub: [authors, x.year].filter(Boolean).join(" · ") || x.key || "",
        hay: hayOf([title, authors, x.year, x.key, x.id, x.journal]),
        open: function () { goPage("resources"); }
      });
    });

    // Analysis — viewers/tools
    collectList(_extra.analysis, ["viewers", "tools", "analysis", "items"]).forEach(function (x) {
      var title = x.title || x.name || x.id;
      if (!title) return;
      out.push({
        group: "Explore", tag: "Explore",
        title: title, sub: x.description || x.summary || "",
        hay: hayOf([title, x.description, x.summary, x.id]),
        open: function () { goPage("visualizations"); }
      });
    });

    return out;
  }

  // Pull an array out of a payload that may be an array or {key: [...]} for one of keys.
  function collectList(payload, keys) {
    if (!payload) return [];
    if (Array.isArray(payload)) return payload.filter(function (x) { return x && typeof x === "object"; });
    for (var i = 0; i < keys.length; i++) {
      if (Array.isArray(payload[keys[i]])) return payload[keys[i]].filter(function (x) { return x && typeof x === "object"; });
    }
    return [];
  }

  function goPage(pageId) {
    close();
    if (typeof window._switchPage === "function") window._switchPage(pageId);
    else window.location.hash = "#" + pageId;
  }
  function openRun(r) {
    goPage("simulations");
    var q = r.run_id || r.label || r.sim_name || "";
    setTimeout(function () {
      var el = document.getElementById("sim-text-filter");
      if (el) { el.value = q; el.dispatchEvent(new Event("input", { bubbles: true })); }
    }, 70);
  }

  // ---- render ---------------------------------------------------------------
  function refresh() {
    if (!overlay || !overlay.classList.contains("viv-gsearch-open")) return;
    render();
  }
  function render() {
    var tokens = tokenize(input.value);
    var index = buildIndex();
    _rows = [];
    _sel = -1;

    var pending = (!window._marketItems || !window._marketItems.length)
      || (!(window._simRows && window._simRows.length) && _extra.runs === null)
      || _extra.inputs === null || _extra.refs === null || _extra.analysis === null;
    loadingEl.textContent = pending ? "indexing…" : "";
    loadingEl.style.display = (pending && tokens.length) ? "" : "none";

    if (!tokens.length) {
      resultsEl.innerHTML = '<div class="viv-gsearch-hint">Search studies, catalog, registry, runs and resources — start typing.</div>';
      return;
    }

    var matched = index.filter(function (it) { return matchAllThenAny(it.hay, tokens); });
    if (!matched.length) {
      resultsEl.innerHTML = '<div class="viv-gsearch-empty">No matches for &ldquo;' + esc(input.value.trim()) + '&rdquo;</div>';
      return;
    }

    // group → ordered sections
    var byGroup = {};
    matched.forEach(function (it) { (byGroup[it.group] = byGroup[it.group] || []).push(it); });

    resultsEl.innerHTML = "";
    GROUP_ORDER.forEach(function (g) {
      var list = byGroup[g];
      if (!list || !list.length) return;
      var head = document.createElement("div");
      head.className = "viv-gsearch-group-head";
      head.innerHTML = "<span>" + esc(g) + '</span><span class="viv-gsearch-group-count">' + list.length + "</span>";
      resultsEl.appendChild(head);
      list.slice(0, PER_GROUP).forEach(function (it) {
        var row = document.createElement("div");
        row.className = "viv-gsearch-row";
        row.setAttribute("role", "option");
        row.innerHTML = '<span class="viv-gsearch-tag">' + esc(it.tag) + "</span>"
          + '<span class="viv-gsearch-body">'
          + '<span class="viv-gsearch-title">' + esc(it.title) + "</span>"
          + (it.sub ? '<span class="viv-gsearch-sub">' + esc(it.sub) + "</span>" : "")
          + "</span>";
        var idx = _rows.length;
        row.addEventListener("click", function () { choose(idx); });
        row.addEventListener("mousemove", function () { if (_sel !== idx) select(idx); });
        resultsEl.appendChild(row);
        _rows.push({ item: it, el: row });
      });
      if (list.length > PER_GROUP) {
        var more = document.createElement("div");
        more.className = "viv-gsearch-sub";
        more.style.cssText = "padding:2px 12px 6px 90px;color:#94a3b8;";
        more.textContent = "+" + (list.length - PER_GROUP) + " more — refine your search";
        resultsEl.appendChild(more);
      }
    });
    if (_rows.length) select(0);
  }

  function select(idx) {
    if (idx < 0 || idx >= _rows.length) return;
    if (_rows[_sel]) _rows[_sel].el.classList.remove("viv-gsearch-sel");
    _sel = idx;
    _rows[_sel].el.classList.add("viv-gsearch-sel");
    _rows[_sel].el.scrollIntoView({ block: "nearest" });
  }
  function move(delta) {
    if (!_rows.length) return;
    var n = _rows.length;
    select(((_sel + delta) % n + n) % n);
  }
  function choose(idx) {
    if (idx == null) idx = _sel;
    var row = _rows[idx];
    if (!row) return;
    close();
    try { row.item.open(); } catch (e) {}
  }

  // ---- open / close ---------------------------------------------------------
  function open() {
    build();
    kickSources();
    overlay.classList.add("viv-gsearch-open");
    document.body.classList.add("viv-gsearch-active");
    input.value = "";
    render();
    setTimeout(function () { input.focus(); }, 0);
    if (_pollTimer) clearInterval(_pollTimer);
    // Catch late-arriving sources (registry build is slow in live mode) a few times.
    var ticks = 0;
    _pollTimer = setInterval(function () {
      if (++ticks > 8 || !overlay.classList.contains("viv-gsearch-open")) { clearInterval(_pollTimer); _pollTimer = null; return; }
      refresh();
    }, 1000);
  }
  function close() {
    if (!overlay) return;
    overlay.classList.remove("viv-gsearch-open");
    document.body.classList.remove("viv-gsearch-active");
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  function build() {
    if (_built) return;
    _built = true;
    overlay = document.createElement("div");
    overlay.id = "viv-gsearch-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Search the workbench");
    overlay.innerHTML =
      '<div class="viv-gsearch-dialog">'
      + '<div class="viv-gsearch-inputrow">'
      + '<svg class="viv-gsearch-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>'
      + '<input id="viv-gsearch-input" type="search" autocomplete="off" spellcheck="false" placeholder="Search studies, catalog, registry, runs, resources…" aria-label="Search query">'
      + '<span class="viv-gsearch-esc">esc</span>'
      + "</div>"
      + '<div class="viv-gsearch-loading" style="display:none"></div>'
      + '<div class="viv-gsearch-results" role="listbox"></div>'
      + '<div class="viv-gsearch-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>'
      + "</div>";
    document.body.appendChild(overlay);
    input = overlay.querySelector("#viv-gsearch-input");
    resultsEl = overlay.querySelector(".viv-gsearch-results");
    loadingEl = overlay.querySelector(".viv-gsearch-loading");

    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) close(); });
    overlay.querySelector(".viv-gsearch-esc").addEventListener("click", close);
    input.addEventListener("input", render);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); choose(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
  }

  // Public opener (the rail magnifier calls this).
  window._vivOpenGlobalSearch = open;
  window._vivCloseGlobalSearch = close;

  // ⌘K / Ctrl-K opens from anywhere.
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (overlay && overlay.classList.contains("viv-gsearch-open")) close(); else open();
    }
  });
})();
