// sms-launch.js — the SMS / vEcoli launch UI for the study page, quarantined
// here from study-detail.js (docs/backend-adapters.md, Q-launch-ui option c).
//
// Owns: the remote-pinned "Run current spec" dispatch (vEcoli generations x
// seeds sized from the study baseline, confirmed before an AWS Batch job is
// submitted) and the "⚙ Dispatch composite" advanced panel (multi_node_dispatch /
// mbp_dispatch / nextflow_dispatch). Everything goes through
// POST /api/remote-run-submit, as before.
//
// Does NOT own the generic remote-run lifecycle (chain progress, Stop, polling):
// that serves every remote run, compose-api's included, and stays in
// study-detail.js.
//
// Loaded before study-detail.js, which calls window.SmsLaunch.mount(host) once
// with a small explicit interface:
//   host.api(method, path, body) -> Promise<{status, body}>
//   host.studyName() -> slug
//   host.confirmModal(msg) -> Promise<bool>, host.alertModal(msg) -> Promise
//   host.cancelled — the {status:0, body:{cancelled:true}} sentinel
//   host.loadStudySims(force) — refresh the Runs table
//   host.actionsHost — the #sim-run-actions element (button row), or null
// and gets back {dispatchBaseline(), setRemoteConfigured(bool)}.
// Later the core loads this file only when the backend declares the
// `sms-launch` feature.
(function () {
  // Bound from the host in mount(); the moved code below keeps its old names.
  var api, studyName, _confirmModal, _alertModal, _CANCELLED, _loadStudySims;

  // "Run current spec" is mode-aware: ONE button, the target decided by
  // deployment config. Remote-pinned deployments (VIVARIUM_WORKBENCH_REMOTE_PINNED,
  // e.g. the live smscdk prod deployment) dispatch to AWS Batch via
  // remote-run-submit. Resolves that response, or null when this deployment
  // is not remote-pinned (study-detail.js then runs locally, as before).
  function _dispatchBaseline() {
    return api('GET', '/api/remote-run-config').then(function(cfgRes) {
      var cfg = (cfgRes.status === 200 && cfgRes.body) || {};
      if (cfg.pinned && cfg.simulator_id) return _dispatchRemotePinned(cfg);
      return null;
    });
  }

  // item 20: the resolved target (repo/branch/commit/simulator id) is fetched
  // fresh via /api/remote-run-config immediately above -- never a stale
  // client-rendered label -- but nothing surfaced it to a human before this
  // function fired the actual AWS Batch dispatch. Show it and require an
  // explicit confirm, so a workspace-identity mismatch is caught here, before
  // money gets spent, not discovered afterward via aws batch describe-jobs.
  //
  // Deliberate addition beyond the || 1 removal below: window._study can be a
  // STALE in-memory copy fetched before a param edit landed server-side (a
  // confirmed real failure mode, not theoretical -- a tab left open across a
  // baseline-param save re-dispatched the OLD 1x1 params from memory even
  // though study.yaml on disk was already correct). Re-fetching via
  // window.DataSource.loadStudy immediately before reading params closes that
  // gap; window._study is refreshed too so the rest of the page stops reading
  // stale state from this point on as well.
  function _dispatchRemotePinned(cfg) {
    var slug = studyName();
    var refetch = (window.DataSource && window.DataSource.loadStudy)
      ? window.DataSource.loadStudy(slug).catch(function () { return null; })
      : Promise.resolve(null);
    return refetch.then(function (freshStudy) {
      if (freshStudy) window._study = freshStudy;
      var baseline = (window._study && window._study.baseline) || [];
      var params = (baseline[0] && baseline[0].params) || {};
      var numGenerations = params.n_generations;
      var numSeeds = params.n_seeds;
      // n_generations/n_seeds directly size a real AWS Batch job -- unlike
      // ordinary composite params (already correctly default-backed via
      // /api/composite-resolve, untouched here), an explicit value the user
      // set must NEVER be silently replaced by a default. An unset value
      // blocks the dispatch outright rather than falling back to 1x1.
      var missing = [];
      if (!numGenerations) missing.push('n_generations');
      if (!numSeeds) missing.push('n_seeds');
      if (missing.length) {
        // item 81: styled modal, not window.alert() -- see _confirmModal's comment
        // in study-detail.js for why (blocks the event loop, including
        // browser-automation UI verification).
        return _alertModal(
          'Cannot dispatch: ' + missing.join(' and ') +
          (missing.length > 1 ? ' are' : ' is') + ' not set.\n\n' +
          'Set ' + (missing.length > 1 ? 'both' : 'it') + ' in the Model tab ' +
          '(Runnable models → edit ' + missing.join(' / ') + ' → Save parameter changes) before running.'
        ).then(function () { return _CANCELLED; });
      }
      var msg = 'Dispatch to AWS Batch:\n\n' +
        '  repo:    ' + (cfg.repo_url || '(unknown)') + '\n' +
        '  branch:  ' + (cfg.branch || '(unknown)') + '\n' +
        '  commit:  ' + ((cfg.commit || '(unknown)').slice(0, 12)) + '\n' +
        '  simulator id: ' + cfg.simulator_id + '\n' +
        '  generations:  ' + numGenerations + '\n' +
        '  seeds:        ' + numSeeds + '\n\n' +
        'Proceed?';
      // item 81: styled modal, not window.confirm() -- reuses item 20b's
      // existing _confirmModal (study-detail.js, passed in to mount) rather than adding a second
      // implementation; see its own comment above for the rationale.
      return _confirmModal(msg).then(function (ok) {
        if (!ok) return _CANCELLED;
        return api('POST', '/api/remote-run-submit', {
          study: slug,
          simulator_id: cfg.simulator_id,
          num_generations: numGenerations,
          num_seeds: numSeeds,
        });
      });
    });
  }

  // ─── item 110: dispatch an arbitrary process-bigraph composite_id (e.g.
  // pbg-native's v2ecoli.composites.lineage_ray_batch, item 101/109) to the
  // remote compute backend -- independent of this study's own pinned
  // baseline composite. Mirrors `atlantis composite run`'s already-proven
  // parameter surface (viva-api PR #382) exactly: named fields for the
  // common params + a raw-JSON escape hatch for anything else
  // (injected_processes/variants/config_overrides/emitter_arg/cache_dir/
  // media/...), rather than inventing a new shape. Fully additive: a new
  // button, a new panel, a new function -- `_dispatchRemotePinned` and the
  // default "Run current spec" flow above are untouched.
  //
  // Reaches viva-api through the SAME endpoint `_dispatchRemotePinned`
  // already uses (`POST /api/remote-run-submit`), which already accepts a
  // top-level `extra_params` field verbatim (`remote_run_views.
  // remote_run_submit`: `extra_params=body.get("extra_params") or None` ->
  // `SmsApiClient.run_simulation(extra_params=...)` -> the real
  // `POST /api/v1/simulations` JSON body's own `extra_params` key) -- the
  // exact field name/shape every real pbg-native dispatch this session
  // fired (database_id 253/255/282/283/288) used. No new server-side code
  // needed; confirmed directly against current source before building this,
  // not assumed from the earlier gap report alone (which cited a different,
  // more complex passthrough in study_runs.py that this simpler, more
  // direct field makes unnecessary for this feature).
  function _compositePanelEl() {
    var el = document.getElementById('study-composite-panel');
    if (el) return el;
    var btn = document.getElementById('study-run-composite');
    var host = btn && btn.parentNode;
    if (!host) return null;
    el = document.createElement('div');
    el.id = 'study-composite-panel';
    el.style.cssText = 'display:none;position:absolute;z-index:20;margin-top:6px;padding:12px;'
      + 'background:var(--panel-bg,#fff);border:1px solid var(--border,#e2e8f0);border-radius:6px;'
      + 'box-shadow:0 4px 16px rgba(0,0,0,0.12);font:12px/1.5 system-ui,-apple-system,sans-serif;'
      + 'width:360px;right:0;top:100%';
    el.innerHTML =
      '<div style="font-weight:600;margin-bottom:8px">Dispatch composite (advanced)</div>'
      + '<label style="display:block;margin-top:6px">mechanism'
      + '<select id="cp-mechanism" style="width:100%;box-sizing:border-box;margin-top:2px">'
      + '<option value="multi_node_dispatch">multi_node_dispatch (lineage_ray_batch, etc.)</option>'
      + '<option value="mbp_dispatch">mbp_dispatch (run_mbp_tracked.py, e.g. reactor_bird_coupled)</option>'
      + '<option value="nextflow_dispatch">nextflow_dispatch (workflow_nf: Nextflow head, one Batch task per lineage)</option>'
      + '</select></label>'
      + '<div id="cp-mnp-fields">'
      + '<label style="display:block;margin-top:6px">composite_id'
      + '<input type="text" id="cp-composite-id" placeholder="v2ecoli.composites.lineage_ray_batch.lineage_ray_batch" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<div style="display:flex;gap:8px;margin-top:6px">'
      + '<label style="flex:1">num_nodes<input type="number" id="cp-num-nodes" min="1" value="2" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="flex:1">n_seeds<input type="number" id="cp-n-seeds" min="1" value="2" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="flex:1">n_generations<input type="number" id="cp-n-generations" min="1" value="1" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '</div>'
      + '</div>'
      + '<div id="cp-mbp-fields" style="display:none">'
      + '<label style="display:block;margin-top:6px">variant'
      + '<input type="text" id="cp-mbp-variant" placeholder="reactor_bird_coupled" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<div style="display:flex;gap:8px;margin-top:6px">'
      + '<label style="flex:1">max_generations<input type="number" id="cp-mbp-max-generations" min="1" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="flex:1">seed<input type="number" id="cp-mbp-seed" min="0" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '</div>'
      + '</div>'
      // nextflow_dispatch (viva-api's third dispatch path, docs/plan-nextflow-dispatch.md):
      // the field set mirrors `atlantis composite nextflow` (app/cli.py
      // _nf_dispatch_payload/_nf_generator_params) -- composite_id/executor/
      // launch sit flat on nextflow_dispatch; seeds/generations/cache_uri/
      // include_analysis/independent_founders live under nextflow_dispatch.params
      // (the workflow_nf generator's own parameters); task_env is the
      // per-task environment passthrough (viva-api#568). The two tri-state
      // selects exist because the CLI OMITS an unset option rather than
      // sending null (a null would override a deployment-derived default), and
      // a checkbox cannot express "unset".
      + '<div id="cp-nf-fields" style="display:none">'
      + '<label style="display:block;margin-top:6px">composite_id'
      + '<input type="text" id="cp-nf-composite-id" value="v2ecoli.composites.workflow_nf.workflow_nf" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<div style="display:flex;gap:8px;margin-top:6px">'
      + '<label style="flex:1">n_seeds<input type="number" id="cp-nf-n-seeds" min="1" value="1" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="flex:1">n_generations<input type="number" id="cp-nf-n-generations" min="1" value="1" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="flex:1">executor<input type="text" id="cp-nf-executor" value="awsbatch" style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '</div>'
      + '<label style="display:block;margin-top:6px">cache_uri (optional — an s3:// ParCa cache prefix to fetch instead of running ParCa, '
      + 'e.g. a staged founder or genotype cache)'
      + '<input type="text" id="cp-nf-cache-uri" placeholder="s3://<bucket>/ray-parca-cache/<commit>/" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<div style="display:flex;gap:8px;margin-top:6px">'
      + '<label style="flex:1">include_analysis<select id="cp-nf-include-analysis" style="width:100%;box-sizing:border-box;margin-top:2px">'
      + '<option value="">(unset)</option><option value="true">true</option><option value="false">false</option></select></label>'
      + '<label style="flex:1">independent_founders<select id="cp-nf-independent-founders" style="width:100%;box-sizing:border-box;margin-top:2px">'
      + '<option value="">(unset)</option><option value="true">true</option><option value="false">false</option></select></label>'
      + '<label style="flex:1;align-self:flex-end"><input type="checkbox" id="cp-nf-launch" checked> launch</label>'
      + '</div>'
      + '<label style="display:block;margin-top:6px">task_env (optional — NAME=VALUE, one per line; set in every Batch task, '
      + 'e.g. V2ECOLI_SKIP_CACHE_VERIFY=1 for a cache built at another commit)'
      + '<textarea id="cp-nf-task-env" rows="2" placeholder="V2ECOLI_SKIP_CACHE_VERIFY=1" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px;font-family:monospace;font-size:11px"></textarea></label>'
      + '</div>'
      + '<div id="cp-cache-variant-wrap">'
      + '<label style="display:block;margin-top:6px">cache_variant (optional — a pre-staged ParCa cache variant; '
      + 'blank uses the plain per-commit cache)'
      + '<input type="text" id="cp-cache-variant" placeholder="e.g. cd2-run1-k4-candidate-v1-lambda050" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '</div>'
      // config_filename sits OUTSIDE cp-cache-variant-wrap: it selects the
      // simulation config for every mechanism, including nextflow_dispatch,
      // whereas cache_variant is meaningless on the Nextflow path and is
      // hidden with the wrap (#1044 added this field; the wrap is this PR's).
      + '<label style="display:block;margin-top:6px">config_filename (optional — a real filename under '
      + 'vEcoli/configs/ in the pinned repo; sms-api 404s without one on repos with no '
      + 'api_simulation_default.json — GET /api/v1/simulations/discovery?simulator_id=&lt;id&gt; lists the '
      + 'pinned commit’s real options)'
      + '<input type="text" id="cp-config-filename" placeholder="e.g. mecillinam_wellmixed.json" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px"></label>'
      + '<label style="display:block;margin-top:6px"><span id="cp-params-desc">extra params (raw JSON, merged into multi_node_dispatch.params — '
      + 'e.g. injected_processes/variants/config_overrides/emitter_arg/cache_dir/out_dir/media)</span>'
      + '<textarea id="cp-params-json" rows="5" placeholder="{}" '
      + 'style="width:100%;box-sizing:border-box;margin-top:2px;font-family:monospace;font-size:11px"></textarea></label>'
      + '<div id="cp-error" style="color:var(--err-fg, #dc2626);margin-top:4px;display:none"></div>'
      + '<div style="display:flex;gap:8px;margin-top:10px;justify-content:flex-end">'
      + '<button type="button" id="cp-cancel" class="btn-mini">Cancel</button>'
      + '<button type="button" id="cp-dispatch" class="btn-mini">Dispatch</button>'
      + '</div>';
    host.style.position = host.style.position || 'relative';
    host.appendChild(el);
    el.querySelector('#cp-cancel').addEventListener('click', function () { el.style.display = 'none'; });
    el.querySelector('#cp-mechanism').addEventListener('change', _updateCompositePanelMechanism);
    _updateCompositePanelMechanism();
    // #cp-dispatch's own click is handled by ONE delegated document-level
    // listener (below, near the other header-button bindings) so the
    // disable/toast/refresh wrapping lives in exactly one place — binding it
    // here too would fire _dispatchRemoteComposite twice per click.
    return el;
  }

  // Toggles the panel's mechanism-specific field groups and the raw-JSON
  // description to match -- mbp_dispatch has no nested "params" sub-object
  // server-side (every field sits flat on mbp_dispatch itself, per
  // _submit_mbp_tracked_dispatch's real contract), unlike multi_node_dispatch's
  // params-wrapped shape, so the two raw-JSON boxes genuinely merge into
  // different places and the label needs to say so, not just the field set.
  //
  // nextflow_dispatch is the third shape: composite_id/executor/launch/
  // resources/work_dir/nextflow_args/task_env sit FLAT on nextflow_dispatch,
  // while the generator's own knobs (n_seeds/n_generations/cache_uri/
  // include_analysis/analysis_options/independent_founders/variants/
  // emit_paths...) live under nextflow_dispatch.params -- so its raw-JSON box
  // merges flat onto nextflow_dispatch EXCEPT a `params` key, which merges
  // into nextflow_dispatch.params (see _dispatchRemoteComposite). It has no
  // cache_variant (the Nextflow path fetches a `cache_uri` instead), so that
  // shared field is hidden for it rather than silently ignored.
  function _updateCompositePanelMechanism() {
    var sel = document.getElementById('cp-mechanism');
    var mechanism = (sel && sel.value) || 'multi_node_dispatch';
    var isMnp = mechanism === 'multi_node_dispatch';
    var isMbp = mechanism === 'mbp_dispatch';
    var isNf = mechanism === 'nextflow_dispatch';
    var mnpFields = document.getElementById('cp-mnp-fields');
    var mbpFields = document.getElementById('cp-mbp-fields');
    var nfFields = document.getElementById('cp-nf-fields');
    var cacheVariantWrap = document.getElementById('cp-cache-variant-wrap');
    var desc = document.getElementById('cp-params-desc');
    if (mnpFields) mnpFields.style.display = isMnp ? '' : 'none';
    if (mbpFields) mbpFields.style.display = isMbp ? '' : 'none';
    if (nfFields) nfFields.style.display = isNf ? '' : 'none';
    if (cacheVariantWrap) cacheVariantWrap.style.display = isNf ? 'none' : '';
    if (desc) {
      desc.textContent = isMnp
        ? 'extra params (raw JSON, merged into multi_node_dispatch.params — '
          + 'e.g. injected_processes/variants/config_overrides/emitter_arg/cache_dir/out_dir/media)'
        : isMbp
        ? 'extra params (raw JSON, merged directly onto mbp_dispatch — e.g. duration_sec/chunk/'
          + 'emitter/single_daughters/carbon_exhaustion_arrest/cells_per_agent/initial_glucose_mM/'
          + 'initial_ammonium_mM/injected_processes/reactor_config/aeration_schedule)'
        : 'extra params (raw JSON, merged directly onto nextflow_dispatch — e.g. resources/'
          + 'work_dir/nextflow_args/resume/resume_from; a "params" key merges into '
          + 'nextflow_dispatch.params — e.g. variants/injected_processes/analysis_options/emit_paths)';
    }
  }

  function _cpError(msg) {
    var e = document.getElementById('cp-error');
    if (!e) return;
    if (!msg) { e.style.display = 'none'; e.textContent = ''; return; }
    e.style.display = ''; e.textContent = msg;
  }

  function _dispatchRemoteComposite() {
    _cpError(null);
    var mechSel = document.getElementById('cp-mechanism');
    var mechanism = (mechSel && mechSel.value) || 'multi_node_dispatch';
    var cacheVariant = (document.getElementById('cp-cache-variant').value || '').trim();
    var configFilename = (document.getElementById('cp-config-filename').value || '').trim();
    var rawJson = (document.getElementById('cp-params-json').value || '').trim();
    var extraParams = {};
    if (rawJson) {
      try {
        extraParams = JSON.parse(rawJson);
      } catch (e) {
        _cpError('extra params is not valid JSON: ' + e.message);
        return;
      }
      if (typeof extraParams !== 'object' || extraParams === null || Array.isArray(extraParams)) {
        _cpError('extra params must be a JSON object, e.g. {"injected_processes": {...}}.');
        return;
      }
    }

    var numGenerations, numSeeds, dispatchExtraParams, confirmLines;

    if (mechanism === 'nextflow_dispatch') {
      var nfCompositeId = (document.getElementById('cp-nf-composite-id').value || '').trim();
      if (!nfCompositeId) { _cpError('composite_id is required.'); return; }
      var nfExecutor = (document.getElementById('cp-nf-executor').value || '').trim() || 'awsbatch';
      numSeeds = parseInt(document.getElementById('cp-nf-n-seeds').value, 10);
      numGenerations = parseInt(document.getElementById('cp-nf-n-generations').value, 10);
      if (!(numSeeds > 0)) { _cpError('n_seeds must be a positive integer.'); return; }
      if (!(numGenerations > 0)) { _cpError('n_generations must be a positive integer.'); return; }
      var nfCacheUri = (document.getElementById('cp-nf-cache-uri').value || '').trim();
      var nfIncludeAnalysis = document.getElementById('cp-nf-include-analysis').value;
      var nfIndependentFounders = document.getElementById('cp-nf-independent-founders').value;
      var nfLaunch = !!document.getElementById('cp-nf-launch').checked;
      // task_env: NAME=VALUE per line, split on the FIRST '=' (a value may
      // contain one) -- the same rule as the CLI's _parse_task_env. Refused
      // here rather than after the round trip so the message names the line.
      var nfTaskEnv = null;
      var taskEnvRaw = (document.getElementById('cp-nf-task-env').value || '').trim();
      if (taskEnvRaw) {
        nfTaskEnv = {};
        var envLines = taskEnvRaw.split(/\r?\n/);
        for (var li = 0; li < envLines.length; li++) {
          var envLine = envLines[li].trim();
          if (!envLine) continue;
          var eq = envLine.indexOf('=');
          if (eq <= 0) { _cpError('task_env line ' + (li + 1) + ' must be NAME=VALUE: ' + envLine); return; }
          nfTaskEnv[envLine.slice(0, eq)] = envLine.slice(eq + 1);
        }
      }
      // The Nextflow path has no cache_variant: it fetches a cache_uri. A
      // stray cache_variant in the raw JSON (copy-pasted from an MNP dispatch)
      // would be ignored server-side and the run would silently build/fetch the
      // plain per-commit cache, which is the exact silent-fallback class #1041
      // fixed for MNP -- so refuse it instead of dropping it.
      if ('cache_variant' in extraParams) {
        _cpError('nextflow_dispatch has no cache_variant; pass the staged cache as cache_uri instead.');
        return;
      }
      // Raw JSON merges FLAT onto nextflow_dispatch, except its `params` key,
      // which merges INTO nextflow_dispatch.params (the generator's own
      // parameters) -- never replacing the object the dedicated fields built.
      // Dedicated fields win over a duplicate inside raw params, mirroring the
      // cache_variant rule above.
      var rawParams = null;
      if ('params' in extraParams) {
        rawParams = extraParams.params;
        if (typeof rawParams !== 'object' || rawParams === null || Array.isArray(rawParams)) {
          _cpError('extra params "params" must be a JSON object (the workflow_nf generator parameters).');
          return;
        }
        extraParams = Object.assign({}, extraParams);
        delete extraParams.params;
      }
      var nfParams = Object.assign({}, rawParams || {}, { n_seeds: numSeeds, n_generations: numGenerations });
      if (nfCacheUri) nfParams.cache_uri = nfCacheUri;
      if (nfIncludeAnalysis !== '') nfParams.include_analysis = (nfIncludeAnalysis === 'true');
      if (nfIndependentFounders !== '') nfParams.independent_founders = (nfIndependentFounders === 'true');
      // Absent options are OMITTED, never sent as null: viva-api's
      // nextflow_dispatch is a passthrough, and a null would override a
      // deployment-derived default (work_dir, resources) with nothing.
      var nfDispatch = Object.assign({}, extraParams, {
        composite_id: nfCompositeId,
        executor: nfExecutor,
        launch: nfLaunch,
        params: nfParams,
      });
      if (nfTaskEnv) nfDispatch.task_env = nfTaskEnv;
      dispatchExtraParams = { nextflow_dispatch: nfDispatch };
      // num_generations/num_seeds: the workbench route hard-requires both (see
      // the mbp comment below), and viva-api records them on the simulation
      // row; the Nextflow path itself sizes the campaign from
      // nextflow_dispatch.params.n_seeds/n_generations. Same numbers, sent to
      // both places on purpose -- the row's metadata and the generator agree.
      confirmLines = '  mechanism:    nextflow_dispatch\n'
        + '  composite_id: ' + nfCompositeId + '\n'
        + '  executor:     ' + nfExecutor + (nfLaunch ? '' : '  (launch=false: render only)') + '\n'
        + '  n_seeds:      ' + numSeeds + '\n'
        + '  n_generations:' + numGenerations + '\n'
        + (nfCacheUri ? '  cache_uri:    ' + nfCacheUri + '\n' : '')
        + (nfIncludeAnalysis !== '' ? '  include_analysis: ' + nfIncludeAnalysis + '\n' : '')
        + (nfIndependentFounders !== '' ? '  independent_founders: ' + nfIndependentFounders + '\n' : '')
        + (nfTaskEnv ? '  task_env:     ' + JSON.stringify(nfTaskEnv) + '\n' : '')
        + (rawJson ? '  extra params: ' + rawJson + '\n' : '');
    } else if (mechanism === 'mbp_dispatch') {
      var variant = (document.getElementById('cp-mbp-variant').value || '').trim();
      if (!variant) { _cpError('variant is required.'); return; }
      var maxGenerations = parseInt(document.getElementById('cp-mbp-max-generations').value, 10);
      if (!(maxGenerations > 0)) { _cpError('max_generations must be a positive integer.'); return; }
      var seedRaw = document.getElementById('cp-mbp-seed').value;
      var seed = seedRaw === '' ? null : parseInt(seedRaw, 10);
      // cache_variant pulled out of extraParams (whichever way the caller
      // supplied it) so a value left over in the raw-JSON box from an older
      // dispatch can never silently diverge from the dedicated field --
      // the dedicated field wins when both are set.
      var extraCacheVariant = extraParams.cache_variant;
      if ('cache_variant' in extraParams) {
        extraParams = Object.assign({}, extraParams);
        delete extraParams.cache_variant;
      }
      var effectiveCacheVariant = cacheVariant || extraCacheVariant;
      // mbp_dispatch is a single-container job -- one dispatch = one lineage,
      // not a seed sweep, so it has no n_seeds concept of its own. The
      // workbench's own /api/remote-run-submit route hard-requires
      // num_generations/num_seeds regardless of mechanism (never silently
      // defaulted -- see _dispatchRemotePinned's own comment above); neither
      // is read by _submit_mbp_tracked_dispatch itself, which sizes the run
      // from mbp_dispatch.max_generations/.seed directly, so
      // num_generations reuses max_generations (the same concept under a
      // different name server-side) and num_seeds is a fixed 1.
      numGenerations = maxGenerations;
      numSeeds = 1;
      var mbpDispatch = Object.assign({ variant: variant, max_generations: maxGenerations }, extraParams);
      if (effectiveCacheVariant) mbpDispatch.cache_variant = effectiveCacheVariant;
      if (seed !== null && !isNaN(seed)) mbpDispatch.seed = seed;
      dispatchExtraParams = { mbp_dispatch: mbpDispatch };
      confirmLines = '  mechanism:    mbp_dispatch\n'
        + '  variant:      ' + variant + '\n'
        + '  max_generations: ' + maxGenerations + '\n'
        + (seed !== null && !isNaN(seed) ? '  seed:         ' + seed + '\n' : '')
        + (effectiveCacheVariant ? '  cache_variant: ' + effectiveCacheVariant + '\n' : '')
        + (rawJson ? '  extra params: ' + rawJson + '\n' : '');
    } else {
      var compositeId = (document.getElementById('cp-composite-id').value || '').trim();
      if (!compositeId) { _cpError('composite_id is required.'); return; }
      var numNodes = parseInt(document.getElementById('cp-num-nodes').value, 10);
      numSeeds = parseInt(document.getElementById('cp-n-seeds').value, 10);
      numGenerations = parseInt(document.getElementById('cp-n-generations').value, 10);
      if (!(numNodes > 0)) { _cpError('num_nodes must be a positive integer.'); return; }
      if (!(numSeeds > 0)) { _cpError('n_seeds must be a positive integer.'); return; }
      if (!(numGenerations > 0)) { _cpError('n_generations must be a positive integer.'); return; }
      // cache_variant AND require_clean_chain must both land as siblings of
      // `params`, never nested inside it -- viva-api reads both directly off
      // mnp_dispatch (simulation_service_ray.py:3285/:3289 --
      // mnp_dispatch.get("cache_variant")/mnp_dispatch.get("require_clean_chain"),
      // never mnp_dispatch["params"].get(...)). Pulled out of extraParams here
      // (whichever the caller supplied it through -- the raw-JSON box, old
      // habit or a copy-pasted dispatch body) BEFORE the params merge below,
      // exactly the bug this fix addresses; the dedicated cache_variant field
      // wins if both it and the raw JSON set one.
      var extraCacheVariant = extraParams.cache_variant;
      var extraRequireCleanChain = extraParams.require_clean_chain;
      if ('cache_variant' in extraParams || 'require_clean_chain' in extraParams) {
        extraParams = Object.assign({}, extraParams);
        delete extraParams.cache_variant;
        delete extraParams.require_clean_chain;
      }
      var effectiveCacheVariant = cacheVariant || extraCacheVariant;
      var params = Object.assign({ n_seeds: numSeeds, n_generations: numGenerations }, extraParams);
      var mnpDispatch = {
        composite_id: compositeId,
        num_nodes: numNodes,
        params: params,
      };
      if (effectiveCacheVariant) mnpDispatch.cache_variant = effectiveCacheVariant;
      if (extraRequireCleanChain !== undefined) mnpDispatch.require_clean_chain = extraRequireCleanChain;
      dispatchExtraParams = { multi_node_dispatch: mnpDispatch };
      confirmLines = '  mechanism:    multi_node_dispatch\n'
        + '  composite_id: ' + compositeId + '\n'
        + '  num_nodes:    ' + numNodes + '\n'
        + '  n_seeds:      ' + numSeeds + '\n'
        + '  n_generations:' + numGenerations + '\n'
        + (effectiveCacheVariant ? '  cache_variant: ' + effectiveCacheVariant + '\n' : '')
        + (rawJson ? '  extra params: ' + rawJson + '\n' : '');
    }

    var slug = studyName();
    return api('GET', '/api/remote-run-config').then(function (cfgRes) {
      var cfg = (cfgRes.status === 200 && cfgRes.body) || {};
      if (!cfg.pinned || !cfg.simulator_id) {
        _cpError('This deployment is not remote-pinned — composite dispatch needs a pinned simulator_id.');
        return _CANCELLED;
      }
      var msg = 'Dispatch composite to AWS Batch:\n\n'
        + '  simulator id: ' + cfg.simulator_id + '\n'
        + confirmLines
        + (configFilename ? '  config_filename: ' + configFilename + '\n' : '')
        + '\nProceed?';
      return _confirmModal(msg).then(function (ok) {
        if (!ok) return _CANCELLED;
        var panel = document.getElementById('study-composite-panel');
        if (panel) panel.style.display = 'none';
        return api('POST', '/api/remote-run-submit', {
          study: slug,
          simulator_id: cfg.simulator_id,
          num_generations: numGenerations,
          num_seeds: numSeeds,
          config_filename: configFilename || undefined,
          extra_params: dispatchExtraParams,
        });
      });
    });
  }
  window._dispatchRemoteComposite = _dispatchRemoteComposite;

  function _createCompositeButton(actionsHost) {
    if (!actionsHost) return null;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'study-run-composite';
    btn.className = 'btn-mini';
    btn.title = 'Dispatch a specific process-bigraph composite_id to the remote compute backend '
      + '(pbg-native multi_node/mbp, or the Nextflow campaign path) — advanced, independent of '
      + "this study's own pinned baseline";
    btn.textContent = '⚙ Dispatch composite';
    // Default-hidden; shown only once the host confirms a remote backend is
    // configured (never in a published snapshot).
    btn.style.display = 'none';
    actionsHost.appendChild(btn);
    return btn;
  }

  function mount(host) {
    api = host.api;
    studyName = host.studyName;
    _confirmModal = host.confirmModal;
    _alertModal = host.alertModal;
    _CANCELLED = host.cancelled;
    _loadStudySims = host.loadStudySims;
    var dispatchBtn = _createCompositeButton(host.actionsHost);

    // "⚙ Dispatch composite" (item 110) — toggles the advanced panel open/closed;
    // the actual dispatch is wired to the panel's own #cp-dispatch button
    // (_compositePanelEl, above). A toast + Runs-tab refresh on success mirrors
    // study-detail.js's run handlers; unlike them this button itself never disables —
    // the panel's own Dispatch button owns that during a real in-flight POST.
    if (dispatchBtn) dispatchBtn.addEventListener('click', function () {
      var panel = _compositePanelEl();
      if (!panel) return;
      panel.style.display = (panel.style.display === 'none') ? '' : 'none';
    });

    // #cp-dispatch's own click (rendered dynamically inside _compositePanelEl,
    // so bound here via delegation rather than at panel-creation time) with the
    // same disabled/toast/refresh convention study-detail.js's run buttons use.
    document.addEventListener('click', function (ev) {
      if (!ev.target || ev.target.id !== 'cp-dispatch') return;
      var btn = ev.target;
      if (btn.disabled) return;
      var orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '… dispatching';
      var result = _dispatchRemoteComposite();
      if (!result || typeof result.then !== 'function') {
        // Validation failed synchronously (_cpError already shown) — nothing to await.
        btn.disabled = false;
        btn.textContent = orig;
        return;
      }
      result
        .then(function (res) {
          btn.disabled = false;
          btn.textContent = orig;
          if (res.body && res.body.cancelled) return;
          if (res.status === 200 || res.status === 202) {
            var runId = res.body && (res.body.run_id || res.body.simulation_id);
            var msg = 'Composite dispatch launched' + (runId ? ' — new run ' + runId : '');
            if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
            if (typeof _loadStudySims === 'function') _loadStudySims(true);
          } else {
            _cpError('Dispatch failed: ' + ((res.body && res.body.error) || res.status));
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = orig;
          _cpError('Dispatch failed: network error — ' + err);
        });
    }, true);

    return {
      dispatchBaseline: _dispatchBaseline,
      setRemoteConfigured: function (on) {
        if (dispatchBtn) dispatchBtn.style.display = on ? '' : 'none';
      },
    };
  }

  window.SmsLaunch = { mount: mount };
})();
