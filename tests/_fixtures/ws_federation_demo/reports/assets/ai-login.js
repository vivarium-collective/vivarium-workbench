// ai-login.js — the AI Settings sheet behind the panel's gear (docs/ai-chat.md).
//
// Talks to /api/ai/*: status (never contains a key), save (the server proves the endpoint/key
// with one real 1-token request before storing it), select, remove. There is no separate Provider control: the Model menu qualifies every model by provider, so choosing a model chooses the
// provider, and the key / base-URL fields below follow it. Provider handling mirrors
// marimo's AI Providers tab: OpenAI, Anthropic, Google, Ollama (local, no key, base URL),
// OpenCode Go (key, fixed URL), AWS Bedrock, OpenAI-compatible (base URL). The Model field is
// marimo's model dropdown (chat.js `VivAiModelMenu`): providers → their models, plus "Enter a
// custom model". The key is typed into a password field, sent once, and cleared from the DOM.
(function () {
  'use strict';
  var card = document.getElementById('viv-ai-card');
  var C = window.VivChatCore;
  if (!card || !C) return;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    keyRow: $('viv-ai-key-row'), key: $('viv-ai-key'),
    urlRow: $('viv-ai-url-row'), url: $('viv-ai-url'), model: $('viv-ai-model'),
    status: $('viv-ai-status'), msg: $('viv-ai-msg'), storage: $('viv-ai-storage'),
    save: $('viv-ai-save'), use: $('viv-ai-use'), remove: $('viv-ai-remove'),
  };
  var ENV = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', google: 'GOOGLE_API_KEY' };
  var OLLAMA_DEFAULT = 'http://localhost:11434/v1';
  var KEYLESS = ['bedrock', 'ollama', 'claude-code'];
  var name = function (p) { return C.providerMeta(p).label; };
  var status = null;
  var provider = 'openai';                          // implied by the model chosen in the Model menu (there is no separate Provider control)
  var model = '';                                   // the model chosen in the menu

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

  // The model trigger: provider mark + model, like marimo's dropdown trigger.
  function renderModel() {
    var m = C.providerMeta(provider);
    el.model.innerHTML = model
      ? '<span><span class="vp-badge" style="background:' + C.esc(m.color) + '">' + C.esc(m.mark) + '</span><span>' + C.esc(model) + '</span></span>'
      : '<span><span class="vp-placeholder">Select a model</span></span>';
    el.model.innerHTML += '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
  }

  function render() {
    if (!status) return;
    var p = provider, r = row(p);
    el.keyRow.hidden = KEYLESS.indexOf(p) >= 0;
    el.urlRow.hidden = !(p === 'ollama' || p === 'openai-compatible');
    if (p === 'ollama' && !el.url.value) el.url.value = (r && r.base_url) || OLLAMA_DEFAULT;
    el.url.placeholder = p === 'ollama' ? OLLAMA_DEFAULT : 'https://host/v1';
    if (!status.available) {
      el.status.textContent = status.reason || 'Chat extra not installed — pip install \'vivarium-workbench[chat]\'';
      [el.save, el.use, el.remove, el.model].forEach(function (b) { b.disabled = true; });
    } else if (r && r.configured) {
      var src = r.source === 'environment' ? 'from the server environment (' + (ENV[p] || 'env') + ')'
        : r.source === 'aws' ? 'using the server\'s AWS credentials'
        : r.source === 'cli' ? 'signed in — uses the `claude` on this machine (nothing is stored here)'
        : p === 'ollama' ? 'connected at ' + (r.base_url || OLLAMA_DEFAULT)
        : 'saved (' + r.source + ')';
      el.status.textContent = name(p) + ' — ' + src;
      el.save.disabled = false;
    } else if (p === 'claude-code') {
      el.status.textContent = 'Claude Code — not available: it needs a local server and the `claude` command signed in (run `claude auth login` in a terminal)';
      el.save.disabled = false;
    } else {
      el.status.textContent = name(p) + ' — not configured';
      el.save.disabled = false;
    }
    var configured = !!(r && r.configured);
    el.use.hidden = !configured;
    el.use.disabled = !status.available;
    el.remove.hidden = !(configured && (r.source === 'keyring' || r.source === 'memory' || r.source === 'config'));
    el.remove.textContent = p === 'ollama' ? 'Remove endpoint' : 'Remove key';
    if (r && r.base_url && !el.url.value) el.url.value = r.base_url;
    var sel = status.selected;
    if (sel && sel.provider === p && !model) model = sel.model;
    el.storage.textContent = p === 'claude-code'
      ? 'Claude Code stores nothing in the workbench: it uses the login of the `claude` command on this machine.'
      : status.storage_mode === 'keyring'
      ? 'Keys go to your operating-system keyring (kept in this server\'s memory only if no keyring is available).'
      : 'Hosted server: keys are kept in server memory for this browser session only and are never written to disk.';
    el.save.textContent = configured && !el.key.value && !KEYLESS.concat(['opencode']).some(function (x) { return x === p; })
      ? 'Test & save' : 'Save & test';
    renderModel();
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
        if (s.selected && !card.dataset.touched) provider = s.selected.provider;
        render();
      }, function (e) { el.status.textContent = (e && e.message) || 'Could not reach /api/ai/status'; });
  }
  function changed() { window.dispatchEvent(new CustomEvent('viv:ai-changed')); }

  // Switching provider clears what belonged to the old one; a model chosen from the dropdown
  // (marimo qualifies every model by provider) switches the provider with it.
  function setProvider(p, m) {
    card.dataset.touched = '1';
    provider = p; el.key.value = ''; el.url.value = ''; say('');
    model = m || '';
    var sel = status && status.selected;
    if (!model && sel && sel.provider === p) model = sel.model;
    render();
  }
  el.key.addEventListener('input', render);
  window.addEventListener('viv:ai-prefill', function (ev) {          // picked in the chat footer, provider not set up yet
    var d = (ev && ev.detail) || {};
    if (d.provider) setProvider(d.provider, d.model);
  });

  el.model.addEventListener('click', function () {
    var open = window.VivAiModelMenu;
    if (!open) return say('The model picker is unavailable (chat assets did not load).');
    open(el.model, {
      ollamaUrl: provider === 'ollama' ? el.url.value.trim() : '',     // the endpoint being edited, not only the saved one
      selected: model ? { provider: provider, model: model } : null,
      fallback: provider,
      onPick: function (p, m) {
        if (p === provider) { model = m; render(); } else setProvider(p, m);
        say('');
      },
    });
  });

  el.save.addEventListener('click', function () {
    var p = provider;
    var body = { provider: p, model: model };
    if (el.key.value.trim() && !el.keyRow.hidden) body.api_key = el.key.value.trim();
    if (!el.urlRow.hidden) body.base_url = el.url.value.trim();
    if (!body.model) return say('Choose a model first.');
    // A key already held by the server (environment / saved) is re-used by "select" — only for
    // the plain key-only providers; endpoint providers are always re-verified.
    var r = row(p);
    var reuse = !body.api_key && r && r.configured && ['openai', 'anthropic', 'google'].indexOf(p) >= 0;
    el.save.disabled = true; say('Checking with the provider…', true);
    (reuse ? json('POST', '/api/ai/select', { provider: p, model: body.model })
           : json('POST', '/api/ai/credentials', body))
      .then(function () {
        el.key.value = '';
        say('Ready — ' + name(p) + ' · ' + body.model, true); changed(); return refresh();
      })
      .catch(function (e) { say(e.message); })
      .then(function () { el.save.disabled = false; render(); });
  });

  el.use.addEventListener('click', function () {
    var p = provider;
    if (!model) return say('Choose a model first.');
    json('POST', '/api/ai/select', { provider: p, model: model })
      .then(function () { say('Selected ' + name(p) + ' · ' + model, true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  el.remove.addEventListener('click', function () {
    var p = provider;
    json('DELETE', '/api/ai/credentials/' + encodeURIComponent(p))
      .then(function () { say('Removed the saved ' + name(p) + (p === 'ollama' ? ' endpoint.' : ' key.'), true); changed(); return refresh(); })
      .catch(function (e) { say(e.message); });
  });

  window._loadAiLogin = refresh;
  refresh();
})();
