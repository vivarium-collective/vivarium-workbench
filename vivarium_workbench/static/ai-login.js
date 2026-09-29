// ai-login.js — the "AI provider" card on the Account page (docs/ai-chat.md).
//
// Talks to /api/ai/*: status (never contains a key), save (the server proves the
// key with one real 1-token request before storing it), select, remove. The key
// is typed into a password field, sent once, and cleared from the DOM.
(function () {
  'use strict';
  var card = document.getElementById('viv-ai-card');
  if (!card) return;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    provider: $('viv-ai-provider'), keyRow: $('viv-ai-key-row'), key: $('viv-ai-key'),
    urlRow: $('viv-ai-url-row'), url: $('viv-ai-url'), model: $('viv-ai-model'),
    status: $('viv-ai-status'), msg: $('viv-ai-msg'), storage: $('viv-ai-storage'),
    save: $('viv-ai-save'), use: $('viv-ai-use'), remove: $('viv-ai-remove'),
  };
  var NAMES = { anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google', 'openai-compatible': 'OpenAI-compatible (Ollama, vLLM, OpenRouter…)', bedrock: 'AWS Bedrock' };
  var ENV = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', google: 'GOOGLE_API_KEY' };
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

  function render() {
    if (!status) return;
    var p = el.provider.value, r = row(p);
    el.keyRow.hidden = (p === 'bedrock');
    el.urlRow.hidden = (p !== 'openai-compatible');
    if (!status.available) {
      el.status.textContent = 'Chat extra not installed — pip install \'vivarium-workbench[chat]\'';
      [el.save, el.use, el.remove].forEach(function (b) { b.disabled = true; });
    } else if (r && r.configured) {
      var src = r.source === 'environment' ? 'from the server environment (' + (ENV[p] || 'env') + ')'
        : r.source === 'aws' ? 'using the server\'s AWS credentials' : 'saved (' + r.source + ')';
      el.status.textContent = NAMES[p] + ' — ' + src;
      el.save.disabled = false;
    } else {
      el.status.textContent = NAMES[p] + ' — not configured';
      el.save.disabled = false;
    }
    var configured = !!(r && r.configured);
    el.use.hidden = !configured;
    el.use.disabled = !status.available;
    el.remove.hidden = !(configured && (r.source === 'keyring' || r.source === 'memory'));
    if (r && r.base_url && !el.url.value) el.url.value = r.base_url;
    var sel = status.selected;
    if (sel && sel.provider === p && !el.model.value) el.model.value = sel.model;
    el.storage.textContent = status.storage_mode === 'keyring'
      ? 'Keys are stored in your operating-system keyring.'
      : 'Hosted server: keys are kept in server memory for this browser session only and are never written to disk.';
    el.save.textContent = configured && !el.key.value && p !== 'bedrock' ? 'Test & save' : 'Save & test';
  }

  function refresh() {
    // A published snapshot has no live server behind /api/ai — the card is hidden there.
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return Promise.resolve();
    return fetch(api('/api/ai/status')).then(function (r) { return r.json(); })
      .then(function (s) {
        status = s;
        if (s.selected && !card.dataset.touched) el.provider.value = s.selected.provider;
        render();
      }, function () { el.status.textContent = 'Could not reach /api/ai/status'; });
  }
  function changed() { window.dispatchEvent(new CustomEvent('viv:ai-changed')); }

  el.provider.addEventListener('change', function () {
    card.dataset.touched = '1'; el.model.value = ''; el.key.value = ''; el.url.value = ''; say(''); render();
    var sel = status && status.selected;
    if (sel && sel.provider === el.provider.value) el.model.value = sel.model;
  });
  el.key.addEventListener('input', render);

  el.save.addEventListener('click', function () {
    var p = el.provider.value;
    var body = { provider: p, model: el.model.value.trim() };
    if (el.key.value.trim()) body.api_key = el.key.value.trim();
    if (p === 'openai-compatible') body.base_url = el.url.value.trim();
    if (!body.model) return say('Enter a model name.');
    // A key already held by the server (environment / saved) is re-used by "select".
    var r = row(p);
    var reuse = !body.api_key && r && r.configured && p !== 'bedrock' && p !== 'openai-compatible';
    el.save.disabled = true; say('Checking with the provider…', true);
    (reuse ? json('POST', '/api/ai/select', { provider: p, model: body.model })
           : json('POST', '/api/ai/credentials', body))
      .then(function () { el.key.value = ''; say('Ready — ' + NAMES[p] + ' · ' + body.model, true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); })
      .then(function () { el.save.disabled = false; render(); });
  });

  el.use.addEventListener('click', function () {
    var p = el.provider.value, model = el.model.value.trim();
    if (!model) return say('Enter a model name.');
    json('POST', '/api/ai/select', { provider: p, model: model })
      .then(function () { say('Selected ' + NAMES[p] + ' · ' + model, true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  el.remove.addEventListener('click', function () {
    var p = el.provider.value;
    json('DELETE', '/api/ai/credentials/' + encodeURIComponent(p))
      .then(function () { say('Removed the saved ' + NAMES[p] + ' key.', true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  window._loadAiLogin = refresh;
  refresh();
})();
