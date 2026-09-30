// ai-login.js — the AI Settings sheet behind the panel's gear (docs/ai-chat.md).
//
// Talks to /api/ai/*: status (never contains a key), save (the server proves the endpoint/key
// with one real 1-token request before storing it), select, remove, and model discovery.
// Provider handling mirrors marimo's AI Providers tab: OpenAI, Anthropic, Google, Ollama (local,
// no key, base URL), OpenCode Go (key, fixed URL), AWS Bedrock, OpenAI-compatible (base URL).
// Models are added by name (marimo's "Add model") and, for endpoints that list them (Ollama,
// OpenCode Go, OpenAI-compatible), discovered. The key is typed into a password field, sent
// once, and cleared from the DOM.
(function () {
  'use strict';
  var card = document.getElementById('viv-ai-card');
  var C = window.VivChatCore;
  if (!card || !C) return;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    provider: $('viv-ai-provider'), keyRow: $('viv-ai-key-row'), key: $('viv-ai-key'),
    urlRow: $('viv-ai-url-row'), url: $('viv-ai-url'), model: $('viv-ai-model'), models: $('viv-ai-models'),
    chips: $('viv-ai-chips'), addModel: $('viv-ai-addmodel'), discover: $('viv-ai-discover'),
    status: $('viv-ai-status'), msg: $('viv-ai-msg'), storage: $('viv-ai-storage'),
    save: $('viv-ai-save'), use: $('viv-ai-use'), remove: $('viv-ai-remove'),
  };
  var ENV = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', google: 'GOOGLE_API_KEY' };
  var OLLAMA_DEFAULT = 'http://localhost:11434/v1';
  // Suggested models (no discovery endpoint). Ollama/OpenCode/OpenAI-compatible are discovered.
  var SUGGEST = { anthropic: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-4-5-20251001'] };
  var DISCOVERABLE = ['ollama', 'opencode', 'openai-compatible'];
  var KEYLESS = ['bedrock', 'ollama'];
  var name = function (p) { return C.providerMeta(p).label; };
  var status = null;

  function api(p) {
    return (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(p) : p;
  }
  function row(id) { return ((status && status.providers) || []).filter(function (p) { return p.id === id; })[0] || null; }
  function say(text, ok) {
    el.msg.textContent = text || '';
    el.msg.style.color = ok ? '#15803d' : '#b91c1c';
  }
  function json(method, path, body) {
    var opts = { method: method };
    if (body) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    return fetch(api(path), opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }

  // The provider's model list: what we know (added/discovered/used) + suggestions + the selection.
  function modelsFor(p) {
    var sel = status && status.selected && status.selected.provider === p ? [status.selected.model] : [];
    return C.mergeModels(sel, C.loadKnown()[p], SUGGEST[p]);
  }
  function renderModels(p) {
    var known = C.loadKnown()[p] || [];
    var list = modelsFor(p);
    el.models.innerHTML = list.map(function (m) { return '<option value="' + C.esc(m) + '"></option>'; }).join('');
    el.chips.innerHTML = list.length ? list.map(function (m) {
      var removable = known.indexOf(m) >= 0;
      return '<span class="vp-chip' + (m === el.model.value.trim() ? ' on' : '') + '" data-model="' + C.esc(m) + '">' +
        '<span>' + C.esc(m) + '</span>' +
        (removable ? '<button type="button" data-rm="' + C.esc(m) + '" title="Remove from this list" aria-label="Remove">×</button>' : '') +
        '</span>';
    }).join('') : '<span class="vp-hint">No models yet — add one by name' +
      (DISCOVERABLE.indexOf(p) >= 0 ? ' or discover them.' : '.') + '</span>';
  }

  function render() {
    if (!status) return;
    var p = el.provider.value, r = row(p);
    el.keyRow.hidden = KEYLESS.indexOf(p) >= 0;
    el.urlRow.hidden = !(p === 'ollama' || p === 'openai-compatible');
    el.discover.hidden = DISCOVERABLE.indexOf(p) < 0;
    if (p === 'ollama' && !el.url.value) el.url.value = (r && r.base_url) || OLLAMA_DEFAULT;
    el.url.placeholder = p === 'ollama' ? OLLAMA_DEFAULT : 'https://host/v1';
    if (!status.available) {
      el.status.textContent = 'Chat extra not installed — pip install \'vivarium-workbench[chat]\'';
      [el.save, el.use, el.remove, el.discover, el.addModel].forEach(function (b) { b.disabled = true; });
    } else if (r && r.configured) {
      var src = r.source === 'environment' ? 'from the server environment (' + (ENV[p] || 'env') + ')'
        : r.source === 'aws' ? 'using the server\'s AWS credentials'
        : p === 'ollama' ? 'connected at ' + (r.base_url || OLLAMA_DEFAULT)
        : 'saved (' + r.source + ')';
      el.status.textContent = name(p) + ' — ' + src;
      el.save.disabled = false;
    } else {
      el.status.textContent = name(p) + ' — not configured';
      el.save.disabled = false;
    }
    var configured = !!(r && r.configured);
    el.use.hidden = !configured;
    el.use.disabled = !status.available;
    el.remove.hidden = !(configured && (r.source === 'keyring' || r.source === 'memory'));
    el.remove.textContent = p === 'ollama' ? 'Remove endpoint' : 'Remove key';
    if (r && r.base_url && !el.url.value) el.url.value = r.base_url;
    var sel = status.selected;
    if (sel && sel.provider === p && !el.model.value) el.model.value = sel.model;
    el.storage.textContent = status.storage_mode === 'keyring'
      ? 'Keys go to your operating-system keyring (kept in this server\'s memory only if no keyring is available).'
      : 'Hosted server: keys are kept in server memory for this browser session only and are never written to disk.';
    el.save.textContent = configured && !el.key.value && !KEYLESS.concat(['opencode']).some(function (x) { return x === p; })
      ? 'Test & save' : 'Save & test';
    renderModels(p);
  }

  function refresh() {
    // A published snapshot has no live server behind /api/ai — the card is hidden there.
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return Promise.resolve();
    return fetch(api('/api/ai/status')).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
          return j;
        });
      })
      .then(function (s) {
        status = s;
        if (s.selected && !card.dataset.touched) el.provider.value = s.selected.provider;
        render();
      }, function (e) { el.status.textContent = (e && e.message) || 'Could not reach /api/ai/status'; });
  }
  function changed() { window.dispatchEvent(new CustomEvent('viv:ai-changed')); }

  el.provider.addEventListener('change', function () {
    card.dataset.touched = '1'; el.model.value = ''; el.key.value = ''; el.url.value = ''; say(''); render();
    var sel = status && status.selected;
    if (sel && sel.provider === el.provider.value) el.model.value = sel.model;
    render();
  });
  el.key.addEventListener('input', render);
  el.model.addEventListener('input', function () { renderModels(el.provider.value); });

  el.addModel.addEventListener('click', function () {
    var m = el.model.value.trim();
    if (!m) return say('Type a model name first.');
    C.addKnown(el.provider.value, [m]); say('Added ' + m + '.', true); changed(); renderModels(el.provider.value);
  });
  el.chips.addEventListener('click', function (ev) {
    var rm = ev.target.closest('[data-rm]');
    if (rm) { C.removeKnown(el.provider.value, rm.getAttribute('data-rm')); changed(); return renderModels(el.provider.value); }
    var chip = ev.target.closest('[data-model]');
    if (chip) { el.model.value = chip.getAttribute('data-model'); renderModels(el.provider.value); }
  });
  el.discover.addEventListener('click', function () {
    var p = el.provider.value, q = 'provider=' + encodeURIComponent(p);
    if (!el.urlRow.hidden && el.url.value.trim()) q += '&base_url=' + encodeURIComponent(el.url.value.trim());
    el.discover.disabled = true; say('Asking the endpoint for its models…', true);
    json('GET', '/api/ai/models?' + q)
      .then(function (r) {
        C.addKnown(p, r.models);
        say('Found ' + r.models.length + ' model' + (r.models.length === 1 ? '' : 's') + '.', true);
        if (!el.model.value.trim() && r.models.length) el.model.value = r.models[0];
        changed(); renderModels(p);
      })
      .catch(function (e) { say(e.message); })
      .then(function () { el.discover.disabled = false; });
  });

  el.save.addEventListener('click', function () {
    var p = el.provider.value;
    var body = { provider: p, model: el.model.value.trim() };
    if (el.key.value.trim() && !el.keyRow.hidden) body.api_key = el.key.value.trim();
    if (!el.urlRow.hidden) body.base_url = el.url.value.trim();
    if (!body.model) return say('Enter a model name.');
    // A key already held by the server (environment / saved) is re-used by "select" — only for
    // the plain key-only providers; endpoint providers are always re-verified.
    var r = row(p);
    var reuse = !body.api_key && r && r.configured && ['openai', 'anthropic', 'google'].indexOf(p) >= 0;
    el.save.disabled = true; say('Checking with the provider…', true);
    (reuse ? json('POST', '/api/ai/select', { provider: p, model: body.model })
           : json('POST', '/api/ai/credentials', body))
      .then(function () {
        el.key.value = ''; C.addKnown(p, [body.model]);
        say('Ready — ' + name(p) + ' · ' + body.model, true); changed(); return refresh();
      })
      .catch(function (e) { say(e.message); })
      .then(function () { el.save.disabled = false; render(); });
  });

  el.use.addEventListener('click', function () {
    var p = el.provider.value, model = el.model.value.trim();
    if (!model) return say('Enter a model name.');
    json('POST', '/api/ai/select', { provider: p, model: model })
      .then(function () { C.addKnown(p, [model]); say('Selected ' + name(p) + ' · ' + model, true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  el.remove.addEventListener('click', function () {
    var p = el.provider.value;
    json('DELETE', '/api/ai/credentials/' + encodeURIComponent(p))
      .then(function () { say('Removed the saved ' + name(p) + (p === 'ollama' ? ' endpoint.' : ' key.'), true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  window._loadAiLogin = refresh;
  refresh();
})();
