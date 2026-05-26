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
 *   POST /api/hpc/{backend}/run/{job_id}/cancel
 */
(function () {
  'use strict';

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
    try {
      const data = await jget(`/api/hpc/${backend}/build/${jobId}/log`);
      const logEl = $('hpc-build-log');
      if (logEl && data.log != null) {
        logEl.textContent = data.log;
        logEl.hidden = false;
        logEl.scrollTop = logEl.scrollHeight;
      }
    } catch (_) { /* non-fatal */ }
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
        if (['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL'].includes(state)) {
          _clearBuildPoll();
          $('hpc-build-cancel-btn').hidden = true;
          $('hpc-build-btn').disabled = false;
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
    try {
      const data = await jpost(`/api/hpc/${backend}/build`);
      const jobId = data.slurm_job_id || data.build_id;
      _activeBuildJobId = jobId;
      btn.textContent = 'Building…';
      if (cancelBtn) cancelBtn.hidden = false;
      _clearBuildPoll();
      _buildPollTimer = setInterval(() => pollBuildLog(backend, jobId), 5000);
      pollBuildLog(backend, jobId);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Start Build';
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
    } catch (_) { /* ignore — build job cancel shares run cancel endpoint */ }
    _clearBuildPoll();
    $('hpc-build-cancel-btn').hidden = true;
    $('hpc-build-btn').disabled = false;
    $('hpc-build-btn').textContent = 'Start Build';
  }

  // ---- Run panel -----------------------------------------------------------

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
      if (['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL'].includes(state)) {
        _clearRunPoll(jobId);
        const cancelEl = rowEl && rowEl.querySelector('.viv-hpc-job-cancel');
        if (cancelEl) cancelEl.hidden = true;
      }
    } catch (_) { /* non-fatal */ }
  }

  async function submitRun(backend) {
    const cmdEl = $('hpc-run-cmd');
    const errEl = document.querySelector('.viv-hpc-run-error');
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

    const runForm = $('hpc-run-form');
    if (runForm) runForm.addEventListener('submit', (e) => { e.preventDefault(); submitRun(backend); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (typeof _HPC_BACKEND !== 'undefined') {
      initHpcPage(_HPC_BACKEND);
    }
  });

  // Export for testing.
  if (typeof module !== 'undefined') {
    module.exports = { initHpcPage };
  }
}());
