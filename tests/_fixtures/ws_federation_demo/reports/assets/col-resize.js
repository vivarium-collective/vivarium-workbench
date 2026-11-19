// col-resize.js — make a table's columns drag-resizable, with per-column widths
// persisted to localStorage so they survive re-renders and reloads.
//
// Usage: ColResize.apply(tableEl, storageKey)
//   - tableEl: the <table> whose <thead><tr><th>… columns should be resizable.
//   - storageKey: a stable string namespacing the saved widths (e.g. the id of
//     the Simulations DB table vs the per-study Simulations table). Columns are
//     keyed within that namespace by their <th data-sort-key> (falling back to
//     the column index) so a table can gain/lose columns without scrambling the
//     saved widths of the ones that remain.
//
// Works with table-layout:fixed (which the module switches the table to): under
// fixed layout each <th>'s width is authoritative for its whole column, so a
// single width on the header governs the column without a <colgroup>. Idempotent
// and re-render-safe: call apply() again after rebuilding the table (the
// per-study renderTable does exactly this) and stored widths are re-applied to
// the fresh <th> elements.
(function () {
  "use strict";

  var PREFIX = "vwb.colw.";
  var MIN_W = 44;   // px — never let a column collapse to unclickable

  function _load(storageKey) {
    try {
      var raw = localStorage.getItem(PREFIX + storageKey);
      var o = raw ? JSON.parse(raw) : null;
      return (o && typeof o === "object") ? o : {};
    } catch (e) { return {}; }
  }
  function _save(storageKey, widths) {
    try { localStorage.setItem(PREFIX + storageKey, JSON.stringify(widths)); }
    catch (e) { /* private mode / quota — resizing still works this session */ }
  }

  // A stable per-column key within the table's namespace.
  function _colKey(th, idx) {
    return th.getAttribute("data-sort-key") ||
           th.getAttribute("data-col-id") ||
           ("c" + idx);
  }

  function apply(table, storageKey) {
    if (!table) return;
    var head = table.querySelector("thead");
    if (!head) return;
    var ths = head.querySelectorAll("tr > th");
    if (!ths.length) return;

    var widths = _load(storageKey);

    // Measure current natural widths BEFORE forcing fixed layout, so a
    // never-resized table keeps the look it renders with today.
    var natural = [];
    for (var i = 0; i < ths.length; i++) natural.push(ths[i].offsetWidth);

    table.style.tableLayout = "fixed";

    for (var j = 0; j < ths.length; j++) {
      (function (th, idx) {
        var key = _colKey(th, idx);
        var w = widths[key];
        if (typeof w === "number" && w >= MIN_W) {
          th.style.width = w + "px";
        } else if (natural[idx]) {
          th.style.width = natural[idx] + "px";
        }
        th.style.position = "relative";

        // The last column has no right-edge grip (nothing to its right to
        // trade width with); it absorbs slack so the table still fills 100%.
        if (idx >= ths.length - 1) return;
        if (th.querySelector(".col-resize-grip")) return;  // already wired

        var grip = document.createElement("span");
        grip.className = "col-resize-grip";
        grip.setAttribute("aria-hidden", "true");
        grip.style.cssText =
          "position:absolute;top:0;right:0;width:8px;height:100%;" +
          "cursor:col-resize;user-select:none;touch-action:none;z-index:2;";
        th.appendChild(grip);

        var startX = 0, startW = 0, dragging = false;

        function onMove(e) {
          if (!dragging) return;
          var dx = (e.touches ? e.touches[0].clientX : e.clientX) - startX;
          var nw = Math.max(MIN_W, Math.round(startW + dx));
          th.style.width = nw + "px";
          if (e.cancelable) e.preventDefault();
        }
        function onUp() {
          if (!dragging) return;
          dragging = false;
          document.removeEventListener("mousemove", onMove, true);
          document.removeEventListener("mouseup", onUp, true);
          document.removeEventListener("touchmove", onMove, true);
          document.removeEventListener("touchend", onUp, true);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          var cur = _load(storageKey);
          cur[key] = parseInt(th.style.width, 10) || startW;
          _save(storageKey, cur);
        }
        function onDown(e) {
          // Don't let the drag double as a header sort-click.
          e.stopPropagation();
          e.preventDefault();
          dragging = true;
          startX = e.touches ? e.touches[0].clientX : e.clientX;
          startW = th.offsetWidth;
          document.addEventListener("mousemove", onMove, true);
          document.addEventListener("mouseup", onUp, true);
          document.addEventListener("touchmove", onMove, { capture: true, passive: false });
          document.addEventListener("touchend", onUp, true);
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }
        grip.addEventListener("mousedown", onDown, true);
        grip.addEventListener("touchstart", onDown, { capture: true, passive: false });
        // A stray click on the grip (e.g. end of a tiny drag) must not sort.
        grip.addEventListener("click", function (e) { e.stopPropagation(); }, true);
      })(ths[j], j);
    }
  }

  window.ColResize = { apply: apply };
})();
