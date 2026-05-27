/**
 * hpc-dispatch.js — HPC backend dashboard page logic.
 *
 * Loaded by hpc_dashboard.html.j2. Expects `_HPC_BACKEND` defined in a
 * preceding inline <script> block (injected by the server-side template).
 *
 * API surface used:
 *   GET  /api/hpc/{backend}/status
 *   GET  /api/hpc/{backend}/slurm
 *   GET  /api/hpc/{backend}/runs
 *   POST /api/hpc/{backend}/build
 *   GET  /api/hpc/{backend}/build/{job_id}
 *   GET  /api/hpc/{backend}/build/{job_id}/log
 *   POST /api/hpc/{backend}/run
 *   GET  /api/hpc/{backend}/run/{job_id}
 *   GET  /api/hpc/{backend}/run/{run_hex_id}/log
 *   POST /api/hpc/{backend}/run/{job_id}/cancel
 */
(function () {
  'use strict';

  const TERMINAL = new Set(['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL']);

  // ---- HTTP helpers --------------------------------------------------------

  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let body = '';
      try { body = await r.text(); } catch (_) { /* ignore */ }
      throw new Error(`GET ${url} → ${r.status} ${body.slice(0, 200)}`);
    }
    return r.json();
  }

  async function jpost(url, body, method) {
    const r = await fetch(url, {
      method: method || 'POST',
      headers: { 'content-type': 'application/json' },
      body: body === undefined ? '' : JSON.stringify(body),
    });
    let json = null;
    try { json = await r.json(); } catch (_) { /* ignore */ }
    if (!r.ok) {
      const err = (json && (json.error || json.detail)) || `HTTP ${r.status}`;
      const e = new Error(err);
      e.status = r.status;
      e.body = json;
      throw e;
    }
    return json;
  }

  // ---- DOM helpers ---------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function _fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit' });
  }

  function _hpc503Warning(body) {
    const missing = (body && body.missing_fields) || [];
    const hint = (body && body.hint) || 'Fill in workspace/.pbg/hpc.env to enable HPC dispatch.';
    const el = document.createElement('div');
    el.className = 'viv-hpc-503';
    el.innerHTML =
      `<strong>HPC not configured</strong>${hint}` +
      (missing.length ? `<br>Missing: <code>${missing.join(', ')}</code>` : '');
    return el;
  }

  // ---- Progress bar helpers -----------------------------------------------

  /**
   * Show/update a panel's progress block.
   * @param {string} panelId  e.g. "build", "parca", "colony"
   * @param {number|null} pct  0–100 for determinate; null for indeterminate
   * @param {string} label     Text shown below the bar
   */
  function _showProgress(panelId, pct, label) {
    const wrap = $(`hpc-${panelId}-progress`);
    const bar  = $(`hpc-${panelId}-bar`);
    const lbl  = $(`hpc-${panelId}-prog-label`);
    if (!wrap) return;
    wrap.hidden = false;
    if (bar) {
      if (pct === null) {
        bar.classList.add('indeterminate');
        bar.style.width = '40%';
      } else {
        bar.classList.remove('indeterminate');
        bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
      }
    }
    if (lbl && label !== undefined) lbl.textContent = label;
  }

  function _hideProgress(panelId) {
    const wrap = $(`hpc-${panelId}-progress`);
    if (wrap) wrap.hidden = true;
  }

  // ---- Log helpers ---------------------------------------------------------

  function _appendLog(logEl, text) {
    if (!logEl || !text) return;
    logEl.textContent = text;
    logEl.hidden = false;
    logEl.scrollTop = logEl.scrollHeight;
  }

  // ---- Progress parsers ----------------------------------------------------

  /**
   * Parse ParCa 9-step progress from SLURM log text.
   * Scans for "checkpoint_step_N.pkl" lines (one per completed step).
   * Returns { step, total, label, pct }.
   */
  function _parseParcaStep(logText) {
    const TOTAL = 9;
    const NAMES = [
      'Initialize', 'InputAdjustments', 'BasalSpecs',
      'TfConditionSpecs', 'FitCondition', 'PromoterBinding',
      'AdjustPromoters', 'SetConditions', 'FinalAdjustments',
    ];
    let maxStep = 0;
    // Primary signal: checkpoint file lines written by parca.py after each step
    const re = /checkpoint_step_(\d+)\.pkl/g;
    let m;
    while ((m = re.exec(logText)) !== null) {
      const n = parseInt(m[1], 10);
      if (n > maxStep) maxStep = n;
    }
    // Secondary: "runtimes.json" written after each step
    const rtRe = /"step_(\d)":/g;
    while ((m = rtRe.exec(logText)) !== null) {
      const n = parseInt(m[1], 10);
      if (n > maxStep) maxStep = n;
    }
    // Final state: "Colony cache bundle complete"
    if (logText.includes('Colony cache bundle complete')) maxStep = TOTAL;

    const stepName = maxStep > 0 && maxStep <= TOTAL ? NAMES[maxStep - 1] : null;
    const label = maxStep >= TOTAL
      ? `All 9 steps complete ✓`
      : maxStep > 0
        ? `Step ${maxStep} / ${TOTAL} — ${stepName}`
        : 'Starting ParCa…';
    return { step: maxStep, total: TOTAL, pct: Math.round((maxStep / TOTAL) * 100), label };
  }

  /**
   * Parse Apptainer build stage from log text.
   * Returns { stage, label } for the progress label.
   */
  function _parseBuildStage(logText) {
    if (!logText) return { label: 'Starting build…' };
    if (logText.includes('Build completed') || logText.includes('Image saved')) {
      return { label: 'Build complete ✓' };
    }
    if (logText.includes('Writing manifest') || logText.includes('Finalizing')) {
      return { label: 'Finalizing image…' };
    }
    if (logText.includes('Copying config') || logText.includes('Writing layer')) {
      return { label: 'Writing SIF layers…' };
    }
    // Apptainer/Docker pull: "Copying blob sha256:abc..."
    const blobRe = /Copying blob (sha256:[a-f0-9]{8})/;
    const blobM = logText.match(blobRe);
    if (blobM) return { label: `Pulling layers… (${blobM[1].slice(0, 15)}…)` };
    if (logText.includes('Copying blob')) return { label: 'Pulling layers…' };
    if (logText.includes('docker://') || logText.includes('Pulling from')) {
      return { label: 'Connecting to registry…' };
    }
    return { label: 'Building…' };
  }

  // ---- Connectivity chip ---------------------------------------------------

  function _setChip(state, label, title) {
    const chip = $('hpc-conn-chip');
    if (!chip) return;
    chip.className = 'viv-hpc-chip viv-hpc-chip-' + state;
    chip.textContent = label;
    if (title) chip.title = title;
  }

  async function loadStatus(backend) {
    _setChip('loading', 'checking…', '');
    try {
      const data = await jget(`/api/hpc/${backend}/status`);
      if (data.reachable) {
        const sing = data.singularity_available
          ? ` · ${data.singularity_cmd || 'apptainer'} ✓`
          : ' · apptainer not found';
        _setChip('ok', 'reachable', (data.message || '') + sing);
      } else {
        _setChip('error', 'unreachable', data.message || 'SSH probe failed');
      }
    } catch (err) {
      if (err.status === 503) {
        _setChip('warn', 'not configured', 'Fill in workspace/.pbg/hpc.env');
        const panel = $('hpc-build-panel');
        if (panel) panel.insertBefore(_hpc503Warning(err.body), panel.querySelector('.viv-hpc-actions'));
      } else {
        _setChip('error', 'error', String(err));
      }
    }
  }

  // ---- Cluster status (SLURM) ----------------------------------------------

  function _renderSlurmData(data, container) {
    if (data.error) {
      container.innerHTML = `<div class="viv-hpc-status-box error">${data.error}</div>`;
      return;
    }
    const parts = [];
    if (Array.isArray(data.partitions) && data.partitions.length) {
      const rows = data.partitions.map((p) =>
        `<tr><td>${p.name || p}</td><td>${p.state || ''}</td><td>${p.nodes || ''}</td></tr>`
      ).join('');
      parts.push(
        '<table class="viv-hpc-slurm-table"><thead><tr>' +
        '<th>Partition</th><th>State</th><th>Nodes</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>'
      );
    }
    if (Array.isArray(data.jobs) && data.jobs.length) {
      const rows = data.jobs.map((j) =>
        `<tr><td>${j.job_id || j.id || ''}</td><td>${j.state || ''}</td>` +
        `<td>${j.name || ''}</td><td>${j.user || ''}</td></tr>`
      ).join('');
      parts.push(
        '<br><strong style="font-size:12px">Running jobs</strong>' +
        '<table class="viv-hpc-slurm-table"><thead><tr>' +
        '<th>Job ID</th><th>State</th><th>Name</th><th>User</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>'
      );
    }
    container.innerHTML = parts.length
      ? parts.join('')
      : '<span style="color:var(--muted,#888)">No active jobs.</span>';
  }

  async function loadSlurmStatus(backend) {
    const el = $('hpc-slurm-info');
    if (!el) return;
    el.textContent = 'Loading…';
    try {
      const data = await jget(`/api/hpc/${backend}/slurm`);
      _renderSlurmData(data, el);
    } catch (err) {
      if (err.status === 503) {
        el.innerHTML = '';
        el.appendChild(_hpc503Warning(err.body));
      } else {
        el.textContent = `Error: ${err.message}`;
      }
    }
  }

  // ---- Build panel ---------------------------------------------------------

  let _buildPollTimer = null;
  let _activeBuildJobId = null;

  function _clearBuildPoll() {
    if (_buildPollTimer) { clearInterval(_buildPollTimer); _buildPollTimer = null; }
  }

  async function pollBuildLog(backend, jobId) {
    // Fetch log and update progress bar stage label
    try {
      const data = await jget(`/api/hpc/${backend}/build/${jobId}/log`);
      const logEl = $('hpc-build-log');
      if (logEl && data.log != null) {
        _appendLog(logEl, data.log);
        // Update build stage label from log content
        const { label } = _parseBuildStage(data.log);
        _showProgress('build', null, label);
      }
    } catch (_) { /* non-fatal */ }

    // Poll job state
    try {
      const status = await jget(`/api/hpc/${backend}/build/${jobId}`);
      const box = $('hpc-build-status');
      if (box) {
        const state = (status.state || 'UNKNOWN').toUpperCase();
        box.textContent = `Build job ${jobId}: ${state}` +
          (status.reason ? ` (${status.reason})` : '');
        box.className = 'viv-hpc-status-box ' +
          (['COMPLETED', 'RUNNING'].includes(state) ? 'ok' :
           ['FAILED', 'TIMEOUT', 'CANCELLED'].includes(state) ? 'error' : '');
        box.hidden = false;

        if (TERMINAL.has(state)) {
          _clearBuildPoll();
          _hideProgress('build');
          $('hpc-build-cancel-btn').hidden = true;
          $('hpc-build-btn').disabled = false;
          $('hpc-build-btn').textContent = 'Start Build';
        }
      }
    } catch (_) { /* non-fatal */ }
  }

  async function startBuild(backend) {
    const btn = $('hpc-build-btn');
    const cancelBtn = $('hpc-build-cancel-btn');
    const statusBox = $('hpc-build-status');
    btn.disabled = true;
    btn.textContent = 'Submitting…';
    if (statusBox) { statusBox.hidden = true; statusBox.textContent = ''; }
    _hideProgress('build');
    try {
      const data = await jpost(`/api/hpc/${backend}/build`);
      const jobId = data.slurm_job_id || data.build_id;
      _activeBuildJobId = jobId;
      btn.textContent = 'Building…';
      if (cancelBtn) cancelBtn.hidden = false;
      // Show indeterminate progress bar immediately
      _showProgress('build', null, 'Submitting to SLURM…');
      _clearBuildPoll();
      _buildPollTimer = setInterval(() => pollBuildLog(backend, jobId), 5000);
      pollBuildLog(backend, jobId);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Start Build';
      _hideProgress('build');
      if (statusBox) {
        if (err.status === 503) {
          statusBox.innerHTML = '';
          statusBox.appendChild(_hpc503Warning(err.body));
        } else {
          statusBox.textContent = `Error: ${err.message}`;
          statusBox.className = 'viv-hpc-status-box error';
        }
        statusBox.hidden = false;
      }
    }
  }

  async function cancelBuild(backend) {
    if (!_activeBuildJobId) return;
    if (!confirm(`Cancel SLURM job ${_activeBuildJobId}?`)) return;
    try {
      await jpost(`/api/hpc/${backend}/run/${_activeBuildJobId}/cancel`);
    } catch (_) { /* ignore */ }
    _clearBuildPoll();
    _hideProgress('build');
    $('hpc-build-cancel-btn').hidden = true;
    $('hpc-build-btn').disabled = false;
    $('hpc-build-btn').textContent = 'Start Build';
  }

  // ---- Auto-log polling for run panels ------------------------------------

  /**
   * Start auto-polling a run job's log and progress bar.
   *
   * @param {string}   backend
   * @param {string}   runId       hex run ID (for log endpoint)
   * @param {number}   slurmJobId  SLURM job ID (for status endpoint)
   * @param {string}   panelId     "parca" | "colony"
   * @param {Function} progressFn  (logText) → {pct|null, label} — null pct = indeterminate
   * @param {number}   intervalMs  polling interval in ms
   */
  function _startAutoLog(backend, runId, slurmJobId, panelId, progressFn, intervalMs) {
    const logEl    = $(`hpc-${panelId}-log`);
    const statusEl = $(`hpc-${panelId}-status`);

    // Show progress immediately
    const init = progressFn('');
    _showProgress(panelId, init.pct, init.label);

    const tid = setInterval(async () => {
      // Fetch log
      if (runId) {
        try {
          const data = await jget(`/api/hpc/${backend}/run/${runId}/log`);
          if (data.log) {
            _appendLog(logEl, data.log);
            const prog = progressFn(data.log);
            _showProgress(panelId, prog.pct, prog.label);
          }
        } catch (_) { /* non-fatal */ }
      }

      // Poll job state
      try {
        const s = await jget(`/api/hpc/${backend}/run/${slurmJobId}`);
        const state = (s.state || '').toUpperCase();
        _setQuickStatus(statusEl, state, panelId === 'parca' ? 'ParCa' : 'Colony', slurmJobId);

        if (TERMINAL.has(state)) {
          clearInterval(tid);
          // On completion do one final log fetch
          if (runId) {
            try {
              const data = await jget(`/api/hpc/${backend}/run/${runId}/log`);
              if (data.log) _appendLog(logEl, data.log);
            } catch (_) { /* non-fatal */ }
          }
          // Final progress state
          if (state === 'COMPLETED') {
            const finalProg = progressFn(logEl ? logEl.textContent : '');
            _showProgress(panelId, finalProg.pct, finalProg.label);
            // Hide progress bar after a short delay so user sees 100%
            setTimeout(() => _hideProgress(panelId), 3000);
          } else {
            _hideProgress(panelId);
          }
          loadJobHistory(backend);
        }
      } catch (_) { /* non-fatal */ }
    }, intervalMs);

    return tid;
  }

  // ---- ParCa panel ----------------------------------------------------------

  function _buildParcaCmd() {
    const mode   = ($('hpc-parca-mode')  || {}).value || 'fast';
    const cpus   = parseInt(($('hpc-parca-cpus')  || {}).value || '8', 10);
    const extra  = (($('hpc-parca-extra') || {}).value || '').trim();
    let cmd = `uv run v2ecoli-parca --mode ${mode} --cpus ${cpus}`;
    if (extra) cmd += ` ${extra}`;
    return cmd;
  }

  function _updateParcaPreview() {
    const el = $('hpc-parca-preview');
    if (el) el.textContent = _buildParcaCmd();
  }

  async function submitParCa(backend) {
    const errEl    = $('hpc-parca-error');
    const submitBtn = $('hpc-parca-submit');
    const statusEl  = $('hpc-parca-status');

    const cmd    = _buildParcaCmd();
    const cpus   = parseInt(($('hpc-parca-cpus') || {}).value || '8', 10);
    const mem_gb = parseInt(($('hpc-parca-mem')  || {}).value || '16', 10);
    const time_min = parseInt(($('hpc-parca-time') || {}).value || '240', 10);

    if (errEl) errEl.textContent = '';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting…';
    _hideProgress('parca');

    try {
      const data = await jpost(`/api/hpc/${backend}/run`, { command: cmd, cpus, mem_gb, time_min });
      const jobId = data.slurm_job_id;
      const runId = data.run_id;

      _setQuickStatus(statusEl, 'PENDING', 'ParCa', jobId);
      loadJobHistory(backend);

      // Start auto-log with 9-step determinate progress
      _startAutoLog(backend, runId, jobId, 'parca',
        (log) => {
          const p = _parseParcaStep(log);
          return { pct: p.pct, label: p.label };
        },
        20000,  // poll every 20 s
      );

    } catch (err) {
      _hideProgress('parca');
      if (err.status === 503) {
        if (errEl) { errEl.innerHTML = ''; errEl.appendChild(_hpc503Warning(err.body)); }
      } else {
        if (errEl) errEl.textContent = `Error: ${err.message}`;
      }
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '▶ Run ParCa';
    }
  }

  // ---- Colony panel ---------------------------------------------------------

  function _buildColonyCmd() {
    const nCells   = parseInt(($('hpc-colony-ncells')   || {}).value || '4', 10);
    const duration = parseInt(($('hpc-colony-duration') || {}).value || '50', 10);
    const cacheDir = (($('hpc-colony-cache')  || {}).value || 'out/sim_data/cache').trim();
    const extra    = (($('hpc-colony-extra')  || {}).value || '').trim();
    let cmd = `uv run v2ecoli-colony --n-cells ${nCells} --duration-min ${duration}`;
    if (cacheDir) cmd += ` --cache-dir ${cacheDir}`;
    if (extra) cmd += ` ${extra}`;
    return cmd;
  }

  function _updateColonyPreview() {
    const el = $('hpc-colony-preview');
    if (el) el.textContent = _buildColonyCmd();
  }

  async function submitColony(backend) {
    const errEl     = $('hpc-colony-error');
    const submitBtn = $('hpc-colony-submit');
    const statusEl  = $('hpc-colony-status');

    const cmd    = _buildColonyCmd();
    const cpus   = parseInt(($('hpc-colony-cpus') || {}).value || '4', 10);
    const mem_gb = parseInt(($('hpc-colony-mem')  || {}).value || '32', 10);
    const time_min = parseInt(($('hpc-colony-time') || {}).value || '120', 10);

    if (errEl) errEl.textContent = '';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting…';
    _hideProgress('colony');

    try {
      const data = await jpost(`/api/hpc/${backend}/run`, { command: cmd, cpus, mem_gb, time_min });
      const jobId = data.slurm_job_id;
      const runId = data.run_id;

      _setQuickStatus(statusEl, 'PENDING', 'Colony', jobId);
      loadJobHistory(backend);

      // Start auto-log with indeterminate progress
      _startAutoLog(backend, runId, jobId, 'colony',
        () => ({ pct: null, label: 'Colony running…' }),
        30000,  // poll every 30 s
      );

    } catch (err) {
      _hideProgress('colony');
      if (err.status === 503) {
        if (errEl) { errEl.innerHTML = ''; errEl.appendChild(_hpc503Warning(err.body)); }
      } else {
        if (errEl) errEl.textContent = `Error: ${err.message}`;
      }
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '🧬 Run Colony';
    }
  }

  // ---- Shared quick-panel helpers ------------------------------------------

  function _setQuickStatus(statusEl, state, label, jobId) {
    if (!statusEl) return;
    statusEl.hidden = false;
    const cls = state === 'COMPLETED' ? 'ok'
              : ['FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL'].includes(state) ? 'error'
              : '';
    statusEl.className = `viv-hpc-status-box ${cls}`;
    statusEl.textContent = `${label} · SLURM ${jobId}: ${state || '…'}`;
  }

  // ---- Custom run panel ----------------------------------------------------

  let _runPollTimers = {};

  function _clearRunPoll(jobId) {
    if (_runPollTimers[jobId]) { clearInterval(_runPollTimers[jobId]); delete _runPollTimers[jobId]; }
  }

  async function pollRunStatus(backend, jobId, rowEl) {
    try {
      const data = await jget(`/api/hpc/${backend}/run/${jobId}`);
      const state = (data.state || '').toUpperCase();
      const stateEl = rowEl && rowEl.querySelector('.viv-hpc-job-run-state');
      if (stateEl) stateEl.textContent = state;
      if (TERMINAL.has(state)) {
        _clearRunPoll(jobId);
        const cancelEl = rowEl && rowEl.querySelector('.viv-hpc-job-cancel');
        if (cancelEl) cancelEl.hidden = true;
      }
    } catch (_) { /* non-fatal */ }
  }

  async function submitRun(backend) {
    const cmdEl = $('hpc-run-cmd');
    const errEl = document.querySelector('#hpc-custom-panel .viv-hpc-run-error');
    const submitBtn = $('hpc-run-submit');
    const cmd = (cmdEl && cmdEl.value || '').trim();
    if (!cmd) {
      if (errEl) errEl.textContent = 'Command is required.';
      if (cmdEl) cmdEl.focus();
      return;
    }
    if (errEl) errEl.textContent = '';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting…';
    const resources = {
      cpus: parseInt($('hpc-run-cpus').value, 10) || 4,
      mem_gb: parseInt($('hpc-run-mem').value, 10) || 8,
      time_min: parseInt($('hpc-run-time').value, 10) || 60,
    };
    try {
      const data = await jpost(`/api/hpc/${backend}/run`, { command: cmd, resources });
      const jobId = data.slurm_job_id;
      if (errEl) errEl.textContent = `Submitted: SLURM job ${jobId}`;
      cmdEl.value = '';
      loadJobHistory(backend);
      _runPollTimers[jobId] = setInterval(() => {
        pollRunStatus(backend, jobId, document.querySelector(`[data-job-id="${jobId}"]`));
      }, 10000);
    } catch (err) {
      if (err.status === 503) {
        if (errEl) { errEl.innerHTML = ''; errEl.appendChild(_hpc503Warning(err.body)); }
      } else {
        if (errEl) errEl.textContent = `Error: ${err.message}`;
      }
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit Job';
    }
  }

  // ---- Job history ---------------------------------------------------------

  async function cancelRunJob(backend, jobId, rowEl) {
    if (!confirm(`Cancel SLURM job ${jobId}?`)) return;
    try {
      await jpost(`/api/hpc/${backend}/run/${jobId}/cancel`);
      _clearRunPoll(jobId);
      const cancelEl = rowEl && rowEl.querySelector('.viv-hpc-job-cancel');
      if (cancelEl) cancelEl.hidden = true;
      const stateEl = rowEl && rowEl.querySelector('.viv-hpc-job-run-state');
      if (stateEl) stateEl.textContent = 'CANCELLED';
    } catch (err) {
      alert(`Cancel failed: ${err.message}`);
    }
  }

  async function loadJobHistory(backend) {
    const container = $('hpc-job-history');
    if (!container) return;
    try {
      const data = await jget(`/api/hpc/${backend}/runs`);
      const jobs = data.jobs || [];
      if (!jobs.length) {
        container.textContent = 'No recent jobs.';
        return;
      }
      container.innerHTML = '';
      jobs.forEach((job) => {
        const row = document.createElement('div');
        row.className = 'viv-hpc-job-row';
        row.dataset.jobId = job.id || '';
        const typeSpan = document.createElement('span');
        typeSpan.className = `viv-hpc-job-type ${job.type || 'run'}`;
        typeSpan.textContent = (job.type || 'run').toUpperCase();
        const idSpan = document.createElement('span');
        idSpan.className = 'viv-hpc-job-id';
        idSpan.textContent = job.script || job.id || '';
        const timeSpan = document.createElement('span');
        timeSpan.className = 'viv-hpc-job-time';
        timeSpan.textContent = job.mtime ? _fmtTime(job.mtime) : '';
        const stateSpan = document.createElement('span');
        stateSpan.className = 'viv-hpc-job-run-state';
        stateSpan.style.cssText = 'font-size:11px;color:var(--muted,#888)';
        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'viv-hpc-btn-sm viv-hpc-job-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.onclick = () => cancelRunJob(backend, job.id, row);
        row.appendChild(typeSpan);
        row.appendChild(idSpan);
        row.appendChild(stateSpan);
        row.appendChild(timeSpan);
        row.appendChild(cancelBtn);
        container.appendChild(row);
      });
    } catch (err) {
      container.textContent = `Error loading jobs: ${err.message}`;
    }
  }

  // ---- Live preview wiring ------------------------------------------------

  function _bindPreviewUpdates() {
    ['hpc-parca-mode', 'hpc-parca-cpus', 'hpc-parca-extra'].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener('input', _updateParcaPreview);
    });
    ['hpc-colony-ncells', 'hpc-colony-duration', 'hpc-colony-cache', 'hpc-colony-extra'].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener('input', _updateColonyPreview);
    });
  }

  // ---- Init ----------------------------------------------------------------

  function initHpcPage(backend) {
    loadStatus(backend);
    loadSlurmStatus(backend);
    loadJobHistory(backend);

    const buildBtn = $('hpc-build-btn');
    if (buildBtn) buildBtn.addEventListener('click', () => startBuild(backend));

    const cancelBtn = $('hpc-build-cancel-btn');
    if (cancelBtn) cancelBtn.addEventListener('click', () => cancelBuild(backend));

    const slurmRefresh = $('hpc-slurm-refresh');
    if (slurmRefresh) slurmRefresh.addEventListener('click', () => loadSlurmStatus(backend));

    const parcaForm = $('hpc-parca-form');
    if (parcaForm) parcaForm.addEventListener('submit', (e) => { e.preventDefault(); submitParCa(backend); });

    const colonyForm = $('hpc-colony-form');
    if (colonyForm) colonyForm.addEventListener('submit', (e) => { e.preventDefault(); submitColony(backend); });

    const runForm = $('hpc-run-form');
    if (runForm) runForm.addEventListener('submit', (e) => { e.preventDefault(); submitRun(backend); });

    _bindPreviewUpdates();
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (typeof _HPC_BACKEND !== 'undefined') {
      initHpcPage(_HPC_BACKEND);
    }
  });

  // Export for testing.
  if (typeof module !== 'undefined') {
    module.exports = { initHpcPage, _parseParcaStep, _parseBuildStage };
  }
}());
