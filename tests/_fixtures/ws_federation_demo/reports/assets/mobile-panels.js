/* mobile-panels.js — phone "panel switcher" bar for the vivarium-workbench SPA.
 *
 * On a phone the dockable side panels (Studies / Cycle / Registry / Chat / Code /
 * Graph — all built on VivPanelDock) cannot tile: there's no room to dock/split, so
 * each opens as a full-screen sheet OVER the content (responsive.css). Before this,
 * that meant any open panel (Studies especially) covered everything with no unified
 * way to see what panels exist, switch between them, or get back to the page — and
 * their launchers were scattered (Studies / Cycle / Registry / Chat / Code in the
 * rail, Graph a contextual toolbar button that's easy to miss).
 *
 * This injects a single phone-only bottom bar that is BOTH the launcher and the
 * switcher — one consistent control, every panel one tap away. The panel order and
 * icons mirror the desktop rail:
 *
 *     [ ▣ Content ] [ Studies ] [ Cycle ] [ Registry ] [ Chat ] [ Code ] [ Graph ]
 *
 *   - Only panels RELEVANT to the current page are shown (Chat/Code are live-only
 *     and vanish in the read-only snapshot; Graph only on the investigations page).
 *   - Tapping a panel opens it as the sheet and closes any other — one panel at a
 *     time on a phone — and highlights it.
 *   - Tapping "Content" (or the active panel again) closes all and returns to the
 *     page. The sheet shrinks to sit ABOVE this bar, so the bar always stays
 *     reachable (responsive.css reserves the space).
 *
 * responsive.css owns every pixel; this file is purely the toggle + relevance
 * logic and is completely inert on desktop (the bar is display:none above 640px).
 * No framework / build step. Ships to the published read-only bundle like every
 * other static asset (publish copies all of static/).
 */
(function () {
  "use strict";

  var BAR_ID = "viv-mobile-panelbar";

  function el(id) { return document.getElementById(id); }

  // "Rendered" = present, not [hidden], and laying out boxes. An off-canvas rail
  // (translateX) still has client rects, so this stays stable whether the drawer
  // is open or closed; a display:none launcher (snapshot) reports zero rects.
  function rendered(node) {
    return !!(node && !node.hidden && node.getClientRects().length);
  }

  // The active SPA page id ("investigations", "studies", "market", …). Prefer the
  // #page-<id>.active section, fall back to the active rail link, then the hash.
  function activePage() {
    var pg = document.querySelector('[id^="page-"].active');
    if (pg) return pg.id.replace(/^page-/, "");
    var a = document.querySelector(".menu-link.active[data-page]");
    if (a) return a.getAttribute("data-page") || "";
    var h = (location.hash || "").replace(/^#/, "").split(/[?&]/)[0];
    return h || "investigations";
  }

  // Each dockable panel: how to tell it's relevant, whether it's open, and how to
  // open/close it. The chat & code setters are TOGGLES (no open/close API); they
  // are only ever called when the current state differs from the target, so the
  // toggle always flips to the intended state.
  var PANELS = [
    {
      key: "studies", label: "Studies", panelId: "viv-studies-panel",
      available: function () { return rendered(el("viv-studies-toggle")) && !!el("viv-studies-panel"); },
      setOpen: function (want) { if (window.StudiesPanel) { want ? StudiesPanel.open() : StudiesPanel.close(); } },
    },
    {
      key: "cycle", label: "Cycle", panelId: "viv-cycle-panel",
      available: function () { return rendered(el("viv-cycle-toggle")) && !!el("viv-cycle-panel"); },
      setOpen: function (want) { if (window.CyclePanel) { want ? CyclePanel.open() : CyclePanel.close(); } },
    },
    {
      key: "registry", label: "Registry", panelId: "viv-registry-panel",
      available: function () { return rendered(el("viv-registry-toggle")) && !!el("viv-registry-panel"); },
      setOpen: function (want) { if (window.RegistryPanel) { want ? RegistryPanel.open() : RegistryPanel.close(); } },
    },
    {
      key: "chat", label: "Chat", panelId: "viv-ai-panel",
      available: function () { return rendered(el("viv-ai-toggle")) && !!el("viv-ai-panel"); },
      setOpen: function () { var t = el("viv-ai-toggle"); if (t) t.click(); },
    },
    {
      key: "code", label: "Code", panelId: "viv-code-rail",
      available: function () { return rendered(el("viv-code-toggle")) && !!el("viv-code-rail"); },
      setOpen: function () { if (window.ProcessCode && ProcessCode.toggle) ProcessCode.toggle(); },
    },
    {
      // No persistent launcher — opened from the inline investigation-graph toolbar
      // and auto-closed when leaving the investigations page (walkthrough.js), so
      // relevance tracks that page exactly.
      key: "graph", label: "Graph", panelId: "viv-graph-panel",
      available: function () { return activePage() === "investigations" && !!el("viv-graph-panel") && !!window.GraphPanel; },
      setOpen: function (want) { if (window.GraphPanel) { want ? GraphPanel.open() : GraphPanel.close(); } },
    },
  ];

  function isOpen(p) { var e = el(p.panelId); return !!(e && !e.hidden); }

  // Open exactly one panel (closing any other); key "" closes all. Iterate ALL
  // panels — not just the currently-relevant ones — so a panel left open by a
  // previous page is always tidied up to the single-panel-at-a-time invariant.
  function openOnly(key) {
    PANELS.forEach(function (p) {
      var want = p.key === key;
      if (isOpen(p) !== want) p.setOpen(want);
    });
  }
  function closeAll() { openOnly(""); }

  // Panel icons mirror the desktop rail launchers (templates/index.html.j2) so the
  // same panel reads identically on a phone and on the desktop — the geometry is
  // copied verbatim from each rail toggle's SVG, wrapped in the bar's own svg style
  // (stroke-width 1.8 for a consistent weight across the bar). "content" is a
  // phone-only affordance (return to the page) and "graph" has no rail launcher, so
  // both keep their bespoke icons.
  var ICON = {
    content: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9 H20.5"/></svg>',
    studies: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12h-8"/><path d="M21 6H8"/><path d="M21 18h-8"/><path d="M3 6v4c0 1.1.9 2 2 2h3"/><path d="M3 10v6c0 1.1.9 2 2 2h3"/></svg>',
    cycle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/></svg>',
    registry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.97 12.92A2 2 0 0 0 2 14.63v3.24a2 2 0 0 0 .97 1.71l3 1.8a2 2 0 0 0 2.06 0L12 19v-5.5l-5-3-4.03 2.42Z"/><path d="m7 16.5-4.74-2.85"/><path d="m7 16.5 5-3"/><path d="M7 16.5v5.17"/><path d="M12 13.5V19l3.97 2.38a2 2 0 0 0 2.06 0l3-1.8a2 2 0 0 0 .97-1.71v-3.24a2 2 0 0 0-.97-1.71L17 10.5l-5 3Z"/><path d="m17 16.5-5-3"/><path d="m17 16.5 4.74-2.85"/><path d="M17 16.5v5.17"/><path d="M7.97 4.42A2 2 0 0 0 7 6.13v4.37l5 3 5-3V6.13a2 2 0 0 0-.97-1.71l-3-1.8a2 2 0 0 0-2.06 0l-3 1.8Z"/><path d="M12 8 7.26 5.15"/><path d="m12 8 4.74-2.85"/><path d="M12 13.5V8"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>',
    code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/></svg>',
    graph: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.3"/><circle cx="18" cy="9" r="2.3"/><circle cx="9" cy="18" r="2.3"/><path d="M8 7.2 L16 8.4 M7.5 8 L8.5 16"/></svg>',
  };

  function btnHTML(key, label, icon) {
    return (
      '<button type="button" class="vpb-btn" data-key="' + key + '" aria-pressed="false">' +
      '<span class="vpb-ico" aria-hidden="true">' + (icon || "") + "</span>" +
      '<span class="vpb-lab">' + label + "</span>" +
      "</button>"
    );
  }

  function refresh() {
    var bar = el(BAR_ID);
    if (!bar) return;
    var avail = PANELS.filter(function (p) { return p.available(); });
    // Nothing relevant → no bar at all (don't reserve space for an empty switcher).
    if (!avail.length) {
      bar.hidden = true;
      document.body.classList.remove("viv-has-panelbar");
      bar._sig = "";
      return;
    }
    bar.hidden = false;
    document.body.classList.add("viv-has-panelbar");
    // Rebuild buttons only when the available set changes, so a layout event
    // (fired on every open/close) doesn't clobber the DOM mid-interaction.
    var sig = avail.map(function (p) { return p.key; }).join(",");
    if (bar._sig !== sig) {
      bar._sig = sig;
      var html = btnHTML("", "Content", ICON.content);
      avail.forEach(function (p) { html += btnHTML(p.key, p.label, ICON[p.key]); });
      bar.innerHTML = html;
    }
    // Highlight whichever panel is open (one at a time on a phone), else Content.
    var activeKey = "";
    PANELS.forEach(function (p) { if (isOpen(p)) activeKey = p.key; });
    Array.prototype.forEach.call(bar.querySelectorAll(".vpb-btn"), function (b) {
      var on = b.getAttribute("data-key") === activeKey;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // Coalesce the many refresh triggers (layout events + rail mutations) into one
  // per frame.
  var pending = false;
  function scheduleRefresh() {
    if (pending) return;
    pending = true;
    (window.requestAnimationFrame || window.setTimeout)(function () { pending = false; refresh(); }, 16);
  }

  function build() {
    if (el(BAR_ID)) return;
    if (!document.body) return;

    var bar = document.createElement("nav");
    bar.id = BAR_ID;
    bar.className = "viv-mobile-panelbar";
    bar.setAttribute("aria-label", "Panels");
    bar.hidden = true;
    document.body.appendChild(bar);

    bar.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".vpb-btn") : null;
      if (!b) return;
      var key = b.getAttribute("data-key");
      if (!key) {
        closeAll();
      } else {
        var p = PANELS.filter(function (x) { return x.key === key; })[0];
        // Tapping the already-open panel toggles back to the content.
        if (p && isOpen(p)) closeAll(); else openOnly(key);
      }
      scheduleRefresh();
    });

    refresh();

    // Re-evaluate on navigation, on any panel open/close, and as the rail renders
    // its (async) studies list / toggles.
    window.addEventListener("hashchange", scheduleRefresh);
    window.addEventListener("viv:panel-layout", scheduleRefresh);
    window.addEventListener("viv:ai-layout", scheduleRefresh);
    var rail = el("viv-rail");
    if (rail && window.MutationObserver) {
      new window.MutationObserver(scheduleRefresh).observe(rail, { childList: true, subtree: true });
    }
    // A couple of delayed passes catch panels whose controllers wire up after us.
    window.setTimeout(refresh, 300);
    window.setTimeout(refresh, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
