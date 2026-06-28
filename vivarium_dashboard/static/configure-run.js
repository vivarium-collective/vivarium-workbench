// Shared "Configure & Run" widget (SP-C). Native, embeddable in the Composite
// Explorer, Composites list, and study Runs tab. mount(el, {composite, target, study}).
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  // Build a config form from composite-resolve's parameters:
  // {name: {type:"string"|"float"|"int"|"bool", default, description}}. May be null/{}.
  function _buildConfigForm(parameters) {
    var params = parameters || {};
    var names = Object.keys(params);
    if (!names.length) return '<p class="muted">This composite has no configurable parameters.</p>';
    return names.map(function (name) {
      var p = params[name] || {};
      var t = p.type, d = p.default, desc = p.description || "";
      var inputId = "cfg-" + name;
      var field;
      if (t === "bool") {
        field = '<input type="checkbox" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="bool"' + (d ? " checked" : "") + ">";
      } else if (t === "float" || t === "int") {
        field = '<input type="number" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="' + esc(t) + '" value="' + esc(d) + '"' + (t === "float" ? ' step="any"' : "") + ">";
      } else {
        field = '<input type="text" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="string" value="' + esc(d) + '">';
      }
      return '<label class="cfg-row" title="' + esc(desc) + '"><span class="cfg-name">' +
        esc(name) + "</span>" + field + "</label>";
    }).join("");
  }

  // Read the form back into a type-cast overrides dict (only changed-from-default need not be
  // tracked — send all; the runner overlays them).
  function _collectOverrides(formEl, parameters) {
    var out = {};
    var inputs = formEl.querySelectorAll("[data-param]");
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i], name = el.getAttribute("data-param"), type = el.getAttribute("data-type");
      if (type === "bool") out[name] = !!el.checked;
      else if (type === "float") out[name] = parseFloat(el.value);
      else if (type === "int") out[name] = parseInt(el.value, 10);
      else out[name] = el.value;
    }
    return out;
  }

  var ctxState = {};
  function mount(el, ctx) {
    ctxState = ctx || {};
    el.innerHTML = '<div class="cfg-run"><div class="cfg-loading">Loading composite…</div></div>';
    var id = ctxState.composite;
    if (!id) { el.querySelector(".cfg-run").innerHTML = '<p class="muted">Pick a composite to configure.</p>'; return; }
    fetch("/api/composite-resolve?id=" + encodeURIComponent(id) + "&overrides=%7B%7D")
      .then(function (r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { return { error: "HTTP " + r.status }; } }); })
      .then(function (d) {
        var box = el.querySelector(".cfg-run");
        if (!d || d.error || d.unresolved) {
          box.innerHTML = '<div class="inv-run-err">' + esc((d && (d.error || "composite not found")) ) + "</div>";
          return;
        }
        box.innerHTML =
          '<h4>' + esc(d.name || id) + '</h4>' +
          '<form class="cfg-form">' + _buildConfigForm(d.parameters) + '</form>' +
          '<label class="cfg-row"><span class="cfg-name">steps</span>' +
          '<input type="number" class="cfg-steps" value="' + esc(d.default_n_steps != null ? d.default_n_steps : 5) + '"></label>' +
          '<div class="cfg-actions"><button type="button" class="btn-mini cfg-run-btn">▶ Run</button></div>' +
          '<div class="cfg-status" hidden></div>';
        ConfigureRun._wireRun(el, d);   // defined in Task 5
      });
  }

  window.ConfigureRun = {
    mount: mount, _buildConfigForm: _buildConfigForm, _collectOverrides: _collectOverrides,
    _ctx: function () { return ctxState; },
  };
})();
