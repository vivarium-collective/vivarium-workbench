/* mobile-nav.js — phone drawer controller for the vivarium-workbench SPA.
 *
 * On a phone (the <=640px rules in responsive.css) the fixed left rail is an
 * off-canvas drawer. This script injects the chrome that drives it:
 *   - a slim mobile topbar (hamburger + context title) as the first child of
 *     .viv-main, and
 *   - a full-screen backdrop appended to <body>.
 * Opening/closing is a single body class, `viv-mobile-rail-open`; responsive.css
 * owns every pixel of the visual behavior, so this file is purely the toggle and
 * is completely inert on desktop (the injected elements are display:none there).
 *
 * No framework, no build step — matches the vanilla-JS frontend. Ships to the
 * published read-only bundle like every other static asset.
 */
(function () {
  "use strict";

  function build() {
    var main = document.querySelector(".viv-main");
    if (!main || document.querySelector(".viv-mobile-topbar")) return;

    // --- Slim mobile topbar (first child of .viv-main) ---
    var bar = document.createElement("div");
    bar.className = "viv-mobile-topbar";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "viv-mobile-menu-btn";
    btn.setAttribute("aria-label", "Open menu");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = "☰"; // ☰

    var title = document.createElement("span");
    title.className = "viv-mobile-title";

    bar.appendChild(btn);
    bar.appendChild(title);
    main.insertBefore(bar, main.firstChild);

    // --- Backdrop ---
    var backdrop = document.createElement("div");
    backdrop.className = "viv-mobile-backdrop";
    backdrop.setAttribute("aria-hidden", "true");
    document.body.appendChild(backdrop);

    function setOpen(open) {
      document.body.classList.toggle("viv-mobile-rail-open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }
    function close() { setOpen(false); }

    btn.addEventListener("click", function () {
      setOpen(!document.body.classList.contains("viv-mobile-rail-open"));
    });
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    // Navigating from inside the drawer should dismiss it. Delegate so it keeps
    // working as the studies tree re-renders. Ignore the header's collapse /
    // search toggles (they don't navigate).
    var rail = document.getElementById("viv-rail");
    if (rail) {
      rail.addEventListener("click", function (e) {
        var t = e.target;
        if (t.closest(".viv-rail-toggle")) return;
        if (t.closest("a[href], .menu-link, [data-page]")) close();
      });
    }

    // --- Context title: reflect the active page, fall back to the workspace. ---
    function activeLabel() {
      var link = document.querySelector(".menu-link.active, .viv-rail-link.active");
      var txt = link && link.textContent ? link.textContent.trim() : "";
      if (txt) return txt;
      return (window._workspaceName || "Vivarium workbench");
    }
    function syncTitle() { title.textContent = activeLabel(); }
    syncTitle();
    window.addEventListener("hashchange", function () {
      // Let _switchPage flip the .active class first.
      setTimeout(syncTitle, 0);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
