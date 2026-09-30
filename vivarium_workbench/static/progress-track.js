// vivarium_workbench/static/progress-track.js
// Plan 7 (WS-1): a small, dependency-free, reusable progress component for
// long-running UI-triggered processes. First consumer is the pinned-build
// "Run on remote" card (see study-detail.js _renderRemoteRunProgress), but the
// component is deliberately call-site-agnostic.
//
// It supports two honest shapes of progress (see the plan's feasibility verdict):
//   - `stages`   — a segmented MILESTONE bar over a known ordered stage set
//                  (determinate at the milestone level), with an optional
//                  time-based SOFT-FILL inside the active stage
//                  (min(elapsed/typical, cap), capped below 100%, snaps to the
//                  next milestone on the real transition). Truthful movement.
//   - `measured` — a genuine value/max fraction (e.g. the local composite-run
//                  path's progress_step / n_steps). A real 0–100% bar.
//
// Pure string builder (`html`) + pure fraction helpers are exported for Node so
// they are unit-testable without a DOM (mirrors aig-graph.js). `render`/`tick`
// are the browser entry points and make ZERO network calls — snapshot-safe by
// construction.
//
// ── ADOPTION NOTE (WS-3b): the local composite-run path (a DIFFERENT subsystem
//    from the remote pinned-build card) exposes a genuine fraction and can adopt
//    `measured` mode as a drop-in with a real 0–100% bar — no soft-fill needed:
//
//      const s = await (await fetch(`/api/composite-run/${runId}/status`)).json();
//      ProgressTrack.render(mountEl, {
//        mode: 'measured',
//        value: s.progress_step,      // current step
//        max: s.n_steps,              // denominator
//        heartbeatAt: s.heartbeat_at, // optional; for a "stalled?" hint
//        note: `<strong>Running…</strong> step ${s.progress_step}/${s.n_steps}`,
//      });
//
//    Poll on an interval and re-call render() each tick; the signature diff keeps
//    it cheap. Not wired this iteration — documented so the next adopter is a
//    drop-in. (Finer remote substages need sms-api to forward the Batch substate;
//    see plan 7 WS-3c.)
(function (global) {
  'use strict';

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function _clamp(x, lo, hi) { return x < lo ? lo : (x > hi ? hi : x); }

  // ---- pure fraction helpers (exported, node-testable) -------------------

  // Honest time-based soft-fill: min(elapsed/typical, cap), never negative,
  // never reaching 1.0 on its own (cap < 1 keeps it truthful — the bar only
  // completes when the real milestone transition fires).
  function softFraction(elapsedMs, typicalMs, cap) {
    if (cap == null) cap = 0.9;
    if (!(typicalMs > 0)) return 0;
    var f = elapsedMs / typicalMs;
    if (!isFinite(f) || f < 0) f = 0;
    return f > cap ? cap : f;
  }

  // Genuine measured fraction, clamped to [0, 1].
  function measuredFraction(value, max) {
    if (!(max > 0)) return 0;
    var f = value / max;
    if (!isFinite(f) || f < 0) return 0;
    return f > 1 ? 1 : f;
  }

  // Milestone fraction = (#done stages + soft-fill of the active stage) / total.
  function stageFraction(model, now) {
    var stages = (model && model.stages) || [];
    var total = stages.length;
    if (total === 0) return 0;
    var known = {};
    for (var i = 0; i < total; i++) known[stages[i].key] = true;
    var done = 0;
    (model.done || []).forEach(function (k) { if (known[k]) done += 1; });
    var frac = done / total;
    if (model.active && model.soft && model.soft.typicalMs) {
      var t = (now == null ? Date.now() : now) - (model.soft.startedAt || 0);
      frac += softFraction(t, model.soft.typicalMs, model.soft.cap) / total;
    }
    return _clamp(frac, 0, 1);
  }

  // ---- state helpers -----------------------------------------------------

  function _segState(model, key) {
    if (model.failed === key) return 'failed';
    if ((model.done || []).indexOf(key) !== -1) return 'done';
    if (model.active === key) return 'active';
    return 'pending';
  }

  // Signature used by render() to decide rebuild-vs-soft-update. Deliberately
  // EXCLUDES soft-fill progress + measured value so the tween repaints only the
  // fill width (no DOM churn, no aria-live re-announce) between milestones.
  function _sig(model) {
    model = model || {};
    if (model.mode === 'measured') {
      return 'measured|' + _esc(model.note || '') + '|' + _esc(model.detail || '');
    }
    var keys = (model.stages || []).map(function (s) { return s.key; }).join(',');
    return 'stages|' + keys
      + '|d:' + (model.done || []).join(',')
      + '|a:' + (model.active || '')
      + '|f:' + (model.failed || '')
      + '|n:' + _esc(model.note || '');
  }

  function _measuredText(model) {
    var v = model.value, m = model.max;
    if (m > 0 && v != null) return 'step ' + v + ' of ' + m;
    return Math.round(measuredFraction(v, m) * 100) + '%';
  }

  // ---- pure string builder (exported, node-testable) ---------------------

  function html(model, now) {
    model = model || {};
    if (now == null) now = Date.now();
    var mode = model.mode || 'stages';
    var note = model.note ? '<div class="ptrack-note">' + model.note + '</div>' : '';
    var detail = model.detail ? '<div class="ptrack-detail">' + _esc(model.detail) + '</div>' : '';
    var sig = _sig(model);

    if (mode === 'measured') {
      var mf = measuredFraction(model.value, model.max);
      var mpct = Math.round(mf * 100);
      var mtext = _measuredText(model);
      return '<div class="ptrack ptrack-measured" role="progressbar"'
        + ' data-sig="' + _esc(sig) + '" data-valuetext="' + _esc(mtext) + '"'
        + ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + mpct + '"'
        + ' aria-valuetext="' + _esc(mtext) + '">'
        + note
        + '<div class="ptrack-bar ptrack-bar-solo">'
        + '<div class="ptrack-seg ptrack-seg-active"><span class="ptrack-fill" style="width:' + mpct + '%"></span></div>'
        + '</div>'
        + '<div class="ptrack-meta"><span class="ptrack-pct">' + mpct + '%</span>'
        + '<span class="ptrack-steptext">' + _esc(mtext) + '</span></div>'
        + detail
        + '<div class="ptrack-live" aria-live="polite">' + _esc(mtext) + '</div>'
        + '</div>';
    }

    // stages mode
    var stages = model.stages || [];
    var frac = stageFraction(model, now);
    var pct = Math.round(frac * 100);
    var activeSoft = 0;
    if (model.soft && model.soft.typicalMs) {
      activeSoft = softFraction(now - (model.soft.startedAt || 0), model.soft.typicalMs, model.soft.cap);
    }
    var segs = '', labels = '', announce = '';
    for (var i = 0; i < stages.length; i++) {
      var s = stages[i];
      var st = _segState(model, s.key);
      var fillW = (st === 'done' || st === 'failed') ? 100
        : (st === 'active' ? Math.round(activeSoft * 100) : 0);
      var spin = (st === 'active' && !model.failed)
        ? '<span class="ptrack-spin" aria-hidden="true"></span>' : '';
      segs += '<div class="ptrack-seg ptrack-seg-' + st + '" data-key="' + _esc(s.key) + '">'
        + '<span class="ptrack-fill" style="width:' + fillW + '%"></span>' + spin + '</div>';
      labels += '<span class="ptrack-label ptrack-label-' + st + '" data-key="' + _esc(s.key) + '">'
        + _esc(s.label) + '</span>';
      if (st === 'active') announce = s.label + '…';
      if (st === 'failed') announce = s.label + ' failed';
    }
    if (!announce && (model.done || []).length === stages.length && stages.length) announce = 'Complete';
    var valuetext = announce ? (announce + ' — ' + pct + '%') : (pct + '%');

    return '<div class="ptrack ptrack-stages" role="progressbar"'
      + ' data-sig="' + _esc(sig) + '" data-valuetext="' + _esc(valuetext) + '"'
      + ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"'
      + ' aria-valuetext="' + _esc(valuetext) + '">'
      + note
      + '<div class="ptrack-bar">' + segs + '</div>'
      + '<div class="ptrack-labels">' + labels + '</div>'
      + detail
      + '<div class="ptrack-live" aria-live="polite">' + _esc(announce) + '</div>'
      + '</div>';
  }

  // ---- browser entry points (no network) ---------------------------------

  // Lightweight repaint of only the active fill + aria-valuenow. Used both by
  // render() when the milestone signature is unchanged and by tick() from the
  // adapter's soft-fill tween — avoids rebuilding the DOM (and re-triggering
  // aria-live) 4×/second.
  function _softUpdate(root, model, now) {
    var mode = model.mode || 'stages';
    var pct;
    if (mode === 'measured') {
      pct = Math.round(measuredFraction(model.value, model.max) * 100);
      var fill = root.querySelector('.ptrack-fill');
      if (fill) fill.style.width = pct + '%';
      var stepText = _measuredText(model);
      var pt = root.querySelector('.ptrack-pct'); if (pt) pt.textContent = pct + '%';
      var stx = root.querySelector('.ptrack-steptext'); if (stx) stx.textContent = stepText;
      root.setAttribute('aria-valuetext', stepText);
      root.setAttribute('data-valuetext', stepText);
    } else {
      pct = Math.round(stageFraction(model, now) * 100);
      var soft = (model.soft && model.soft.typicalMs)
        ? softFraction(now - (model.soft.startedAt || 0), model.soft.typicalMs, model.soft.cap) : 0;
      var af = root.querySelector('.ptrack-seg-active .ptrack-fill');
      if (af) af.style.width = Math.round(soft * 100) + '%';
    }
    root.setAttribute('aria-valuenow', pct);
  }

  // Render `model` into `mount`. Rebuilds on milestone change; otherwise does a
  // cheap soft-update so the tween stays smooth without DOM churn.
  function render(mount, model) {
    if (!mount) return;
    var now = Date.now();
    var root = mount.firstElementChild;
    if (root && root.getAttribute && root.getAttribute('data-sig') === _sig(model)) {
      _softUpdate(root, model, now);
      return;
    }
    mount.innerHTML = html(model, now);
  }

  // Soft-update only (assumes render() already built the DOM). Safe no-op if the
  // mount is empty. Called by the adapter's rAF/interval tween.
  function tick(mount, model) {
    if (!mount) return;
    var root = mount.firstElementChild;
    if (!root) { render(mount, model); return; }
    if (root.getAttribute && root.getAttribute('data-sig') !== _sig(model)) {
      render(mount, model); return;
    }
    _softUpdate(root, model, Date.now());
  }

  var api = {
    render: render, tick: tick, html: html,
    stageFraction: stageFraction, softFraction: softFraction, measuredFraction: measuredFraction,
  };

  global.ProgressTrack = api;
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);