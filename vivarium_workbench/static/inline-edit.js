/* inline-edit.js — click-to-edit prose fields on the LIVE workbench.
 *
 * Any element tagged with data-edit-field becomes click-to-edit: click it, edit
 * the text, Save, and the value is written back into the workspace YAML
 * (study.yaml / investigation.yaml) on disk via POST /api/prose-edit — so it
 * shows up in `git diff` and you commit it yourself. No auto-commit.
 *
 * Tagging contract (added by the server-rendered templates / SPA renderers):
 *   data-edit-field="<yaml path>"       e.g. "question", "hypothesis",
 *                                        "findings[F-01].statement",
 *                                        "executive.what_is_this"
 *   data-edit-kind="study"|"investigation"
 *   data-edit-slug="<slug>"             (optional — else resolved from globals)
 *   data-edit-multiline                 (optional — present → textarea, tall)
 *   data-edit-label="<human label>"     (optional — shown in the editor header)
 *
 * LIVE ONLY: disabled in snapshot / published bundles (no backend to save to).
 */
(function () {
  "use strict";
  if (window.__vivaInlineEditLoaded) return;
  window.__vivaInlineEditLoaded = true;

  function isSnapshot() {
    try {
      if (document.body && document.body.classList.contains("snapshot")) return true;
      if (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.mode === "snapshot") return true;
    } catch (e) {}
    return false;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function studySlug() {
    return (window._study && (window._study.name || window._study.slug)) || window._studyName || "";
  }
  function investigationSlug() {
    var d = window._currentIsetData;
    return (d && (d.slug || d.name)) || window._currentIset || window._currentIsetSlug || "";
  }
  function slugFor(el, kind) {
    var s = el.getAttribute("data-edit-slug");
    if (s) return s;
    return kind === "investigation" ? investigationSlug() : studySlug();
  }

  function toast(msg, ok) {
    if (window.Toast && typeof window.Toast.show === "function") {
      window.Toast.show(msg, ok === false ? "error" : "success");
      return;
    }
    var t = document.createElement("div");
    t.className = "ie-toast" + (ok === false ? " err" : "");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("in"); }, 10);
    setTimeout(function () { t.classList.remove("in"); setTimeout(function () { t.remove(); }, 300); }, 2800);
  }

  // ── decoration ────────────────────────────────────────────────────────────────
  function decorate(el) {
    if (!el || el.__ieDecorated) return;
    if (!el.getAttribute("data-edit-field")) return;
    el.__ieDecorated = true;
    el.classList.add("ie-editable");
    // A corner button needs a real box: an inline element's absolute child
    // anchors to its first line box (→ the ✎ lands mid-text on wrapped spans).
    // Promote truly-inline fields to inline-block so top:4px/right lands at the
    // text block's actual top-right. Block fields are untouched.
    try {
      if (getComputedStyle(el).display === "inline") el.style.display = "inline-block";
    } catch (e) {}
    var pencil = document.createElement("button");
    pencil.type = "button";
    pencil.className = "ie-pencil";
    pencil.title = "Edit " + (el.getAttribute("data-edit-label") || "this field");
    pencil.setAttribute("aria-label", pencil.title);
    pencil.textContent = "✎";
    el.appendChild(pencil);
  }
  function decorateAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll("[data-edit-field]");
    for (var i = 0; i < nodes.length; i++) decorate(nodes[i]);
  }

  var activeEditor = null;

  function openEditor(el) {
    if (activeEditor) return; // one at a time
    var kind = el.getAttribute("data-edit-kind") || "study";
    var field = el.getAttribute("data-edit-field");
    var slug = slugFor(el, kind);
    var label = el.getAttribute("data-edit-label") || field;
    var multiline = el.hasAttribute("data-edit-multiline");
    if (!slug) { toast("Could not resolve which " + kind + " to save to", false); return; }

    // Edit the RAW yaml value (data-edit-value) when provided — several fields
    // render through transforms (markdown, whitespace-collapse, first-paragraph
    // only), so the displayed text is lossy. Fall back to the element's text.
    var original;
    if (el.hasAttribute("data-edit-value")) {
      original = el.getAttribute("data-edit-value") || "";
    } else {
      var clone = el.cloneNode(true);
      var p = clone.querySelector(".ie-pencil"); if (p) p.remove();
      original = (clone.textContent || "").replace(/\s+\n/g, "\n").trim();
    }

    var savedHTML = el.innerHTML; // restore on cancel
    el.classList.add("ie-editing");

    var wrap = document.createElement("div");
    wrap.className = "ie-editor";
    wrap.innerHTML =
      '<div class="ie-editor-h">Editing: ' + esc(label) + "</div>" +
      (multiline
        ? '<textarea class="ie-input" rows="6"></textarea>'
        : '<textarea class="ie-input" rows="2"></textarea>') +
      '<div class="ie-editor-foot">' +
      '<span class="ie-hint">⌘/Ctrl+Enter to save · Esc to cancel</span>' +
      '<span class="ie-spacer"></span>' +
      '<button type="button" class="ie-cancel">Cancel</button>' +
      '<button type="button" class="ie-save">Save</button>' +
      "</div>";
    el.innerHTML = "";
    el.appendChild(wrap);
    var ta = wrap.querySelector(".ie-input");
    ta.value = original;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    activeEditor = { el: el, savedHTML: savedHTML, field: field, kind: kind, slug: slug };

    function cancel() {
      el.innerHTML = savedHTML;
      el.classList.remove("ie-editing");
      activeEditor = null;
      // re-add pencil (innerHTML restore kept it; __ieDecorated stays true)
    }
    function doSave() {
      var value = ta.value;
      var saveBtn = wrap.querySelector(".ie-save");
      saveBtn.disabled = true; saveBtn.textContent = "Saving…";
      save(kind, slug, field, value).then(function (res) {
        if (res.ok && res.j && res.j.saved) {
          // show the new value as plain text + keep the raw value in sync + restore the pencil
          el.classList.remove("ie-editing");
          el.textContent = value;
          if (el.hasAttribute("data-edit-value")) el.setAttribute("data-edit-value", value);
          var pencil = document.createElement("button");
          pencil.type = "button"; pencil.className = "ie-pencil";
          pencil.title = "Edit " + esc(label); pencil.textContent = "✎";
          el.appendChild(pencil);
          activeEditor = null;
          toast("Saved to " + (res.j.file || "workspace") + " — commit when ready");
        } else {
          saveBtn.disabled = false; saveBtn.textContent = "Save";
          toast((res.j && res.j.error) || "Save failed", false);
        }
      }).catch(function (err) {
        saveBtn.disabled = false; saveBtn.textContent = "Save";
        toast("Save failed: " + err, false);
      });
    }
    wrap.querySelector(".ie-cancel").addEventListener("click", cancel);
    wrap.querySelector(".ie-save").addEventListener("click", doSave);
    ta.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.preventDefault(); cancel(); }
      else if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") { ev.preventDefault(); doSave(); }
    });
  }

  function save(kind, slug, field, value) {
    var url = "/api/prose-edit";
    try {
      // apiUrl only prefixes the base path for an absolute "/api/..." path; a
      // bare name is returned unchanged and would resolve relative to the page.
      if (window.DataSource && typeof window.DataSource.apiUrl === "function") url = window.DataSource.apiUrl("/api/prose-edit");
    } catch (e) {}
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: kind, slug: slug, field: field, value: value }),
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
  }

  function injectStyle() {
    if (document.getElementById("ie-style")) return;
    var css = [
      ".ie-editable{position:relative;}",
      // Corner ✎ button mirroring the feedback 💬 (.fb-corner), parked one slot
      // to its LEFT (right:30px vs 4px) so the two sit side-by-side when a field
      // is also a feedback region; reveals on hover of the field.
      ".ie-pencil{position:absolute;top:4px;right:30px;z-index:6;width:26px;height:26px;padding:0;border:none;border-radius:7px;",
      "  background:var(--card,#fff);box-shadow:0 1px 4px rgba(0,0,0,.18);color:#2d6cdf;cursor:pointer;font-size:13px;line-height:26px;text-align:center;",
      "  opacity:0;transform:translateY(-2px);transition:opacity .12s,transform .12s;}",
      ".ie-editable:hover>.ie-pencil,.ie-pencil:focus{opacity:.92;transform:none;}",
      ".ie-pencil:hover{opacity:1;box-shadow:0 2px 8px rgba(0,0,0,.28);}",
      ".ie-editing>.ie-pencil{display:none;}",
      ".ie-editor{display:block;margin:2px 0;}",
      ".ie-editor-h{font-size:11px;color:var(--ink-2,#64748b);margin-bottom:4px;text-transform:uppercase;letter-spacing:.03em;}",
      ".ie-input{width:100%;box-sizing:border-box;border:1px solid #2d6cdf;border-radius:8px;padding:8px 10px;font:inherit;line-height:1.5;resize:vertical;",
      "  background:var(--bg,#fff);color:var(--ink,#111);}",
      ".ie-editor-foot{display:flex;align-items:center;gap:8px;margin-top:7px;}",
      ".ie-editor-foot .ie-spacer{flex:1;}",
      ".ie-hint{font-size:11px;color:var(--ink-2,#94a3b8);}",
      ".ie-editor-foot button{padding:6px 14px;border:1px solid var(--line,#ddd);border-radius:7px;background:var(--bg,#f2f2f4);cursor:pointer;font-size:12px;color:var(--ink,#111);}",
      ".ie-editor-foot .ie-save{background:#2d6cdf;border-color:#2d6cdf;color:#fff;font-weight:600;}",
      ".ie-editor-foot .ie-save:disabled{opacity:.6;cursor:default;}",
      ".ie-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(12px);z-index:100000;background:#1f9d55;color:#fff;",
      "  padding:9px 16px;border-radius:9px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.3);opacity:0;transition:opacity .25s,transform .25s;}",
      ".ie-toast.in{opacity:1;transform:translateX(-50%) translateY(0);}",
      ".ie-toast.err{background:#d23f3f;}",
      "@media print{.ie-pencil{display:none !important;}}",
    ].join("\n");
    var st = document.createElement("style");
    st.id = "ie-style";
    st.textContent = css;
    (document.head || document.documentElement).appendChild(st);
  }

  var tmr = null;
  function schedule() { if (tmr) return; tmr = setTimeout(function () { tmr = null; decorateAll(); }, 150); }

  function init() {
    if (isSnapshot()) return; // live only
    injectStyle();
    decorateAll();
    var root = document.querySelector("main.study-page") || document.querySelector("#viv-content") || document.body;
    new MutationObserver(schedule).observe(root, { childList: true, subtree: true });
    // Edit is triggered only by the ✎ button (so the field text stays selectable).
    document.addEventListener("click", function (ev) {
      var pen = ev.target.closest(".ie-pencil");
      if (!pen) return;
      var el = pen.closest(".ie-editable");
      if (!el || el.classList.contains("ie-editing")) return;
      ev.preventDefault();
      ev.stopPropagation();
      openEditor(el);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
