// checklist-select.js — a filterable checkbox list, the shared "select several
// from a known finite set" widget this app never had (every existing <select>
// is hand-rolled per call site — see item 69's own plan doc). Replaces a bare
// native <select multiple>, which requires an undiscoverable Cmd/Ctrl+click
// gesture and gives no visual feedback about what's selected.
(function () {
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // items: [{value, label, selected, flagged, title}]
  function render(mount, opts) {
    if (!mount) return;
    opts = opts || {};
    var items = opts.items || [];
    var itemsHtml = items.length
      ? items.map(function (it) {
          return '<label class="viv-checklist-item' + (it.flagged ? ' viv-checklist-item-flagged' : '') + '"' +
            (it.title ? ' title="' + _esc(it.title) + '"' : '') + '>' +
            '<input type="checkbox" value="' + _esc(it.value) + '"' + (it.selected ? ' checked' : '') + '>' +
            '<span>' + _esc(it.label) + '</span>' +
            (it.flagged ? '<span class="viv-checklist-flag">not in registry</span>' : '') +
          '</label>';
        }).join('')
      : '<p class="empty-state">' + _esc(opts.emptyText || 'Nothing to select.') + '</p>';
    mount.innerHTML =
      '<div class="viv-checklist">' +
        '<input type="text" class="viv-search viv-checklist-filter" placeholder="' + _esc(opts.filterPlaceholder || 'Filter…') + '">' +
        '<div class="viv-checklist-items">' + itemsHtml + '</div>' +
        '<div class="viv-checklist-count"></div>' +
      '</div>';
    _wire(mount);
  }

  function _wire(mount) {
    var filter = mount.querySelector('.viv-checklist-filter');
    var itemsHost = mount.querySelector('.viv-checklist-items');
    var count = mount.querySelector('.viv-checklist-count');
    function updateCount() {
      if (!count) return;
      var n = itemsHost ? itemsHost.querySelectorAll('input:checked').length : 0;
      count.textContent = n ? (n + ' selected') : '';
    }
    if (filter && itemsHost) {
      filter.addEventListener('input', function () {
        var q = filter.value.trim().toLowerCase();
        Array.prototype.forEach.call(itemsHost.querySelectorAll('.viv-checklist-item'), function (row) {
          row.style.display = (!q || row.textContent.toLowerCase().indexOf(q) >= 0) ? '' : 'none';
        });
      });
    }
    if (itemsHost) itemsHost.addEventListener('change', updateCount);
    updateCount();
  }

  function selected(mount) {
    if (!mount) return [];
    return Array.prototype.map.call(
      mount.querySelectorAll('.viv-checklist-items input:checked'),
      function (cb) { return cb.value; }
    );
  }

  window.ChecklistSelect = { render: render, selected: selected };
})();
