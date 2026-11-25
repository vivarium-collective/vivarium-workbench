// compose-api sign-in from the page (lib/compose_signin.py).
//
// On load: GET /api/auth/compose-api/status. Sign-in not offered here (no compose-api backend, no sign-in extra,
// COMPOSE_API_TOKEN set, or the CLI profile is for another API) -> the row stays hidden. Otherwise the chip says
// "Sign in" or "Signed in · <profile>".
//
// "Sign in": POST /api/auth/compose-api/start {method: "auto"}; the client opens this computer's browser, or, over SSH
// or without a display, shows a code to enter on any device. "Use a code instead" asks for the code directly (a
// workbench on another machine). While it waits, the page polls the status and shows the client's own messages (never
// a token). Signed in: clicking signs out (for the compose-api CLI too) after a confirm.

(function () {
  const row = document.getElementById('viv-compose-row');
  const chip = document.getElementById('viv-compose-chip');
  const hint = document.getElementById('viv-compose-hint');
  const flowBox = document.getElementById('viv-compose-flow');
  if (!row || !chip) return;

  const base = () => window.__BASE_PATH__ || '';
  const POLL_MS = 1500;
  let pollTimer = null;

  async function getStatus() {
    try {
      const resp = await fetch(base() + '/api/auth/compose-api/status');
      return await resp.json();
    } catch (_e) {
      return { available: false };
    }
  }

  function showFlow(flow) {
    if (!flow || (!flow.notes.length && !flow.error)) {
      flowBox.style.display = 'none';
      flowBox.textContent = '';
      return;
    }
    flowBox.style.display = '';
    flowBox.textContent = flow.notes.join('\n') + (flow.error ? '\n' + 'Sign-in failed: ' + flow.error : '');
  }

  function render(status) {
    if (!status.available) {
      row.style.display = 'none';
      return;
    }
    row.style.display = '';
    const flow = status.flow;
    const waiting = flow && flow.state === 'running';
    chip.disabled = waiting;
    hint.innerHTML = '';
    if (waiting) {
      chip.dataset.state = 'waiting';
      chip.textContent = 'Signing in…';
      chip.title = 'Finish signing in in the browser, or enter the code shown below';
    } else if (status.signed_in) {
      chip.dataset.state = 'in';
      chip.textContent = 'Signed in · ' + status.profile;
      chip.title = 'Your runs go to ' + status.api_base_url + ' as you. Click to sign out.';
      chip.onclick = signOut;
    } else {
      chip.dataset.state = 'out';
      chip.textContent = 'Sign in';
      chip.title = 'Sign in to ' + status.api_base_url + ' (the compose-api CLI shares this login)';
      chip.onclick = () => startFlow('auto');
      const alt = document.createElement('a');
      alt.href = '#';
      alt.textContent = 'Use a code instead';
      alt.title = 'For a workbench on another machine: sign in on any device with a code';
      alt.onclick = (e) => { e.preventDefault(); startFlow('device'); };
      hint.append('Your runs go to compose-api as you once signed in. ', alt, ' (remote or SSH).');
      if (status.error) hint.append(' ' + status.error);
    }
    showFlow(flow);
    if (waiting && !pollTimer) pollTimer = setInterval(refresh, POLL_MS);
    if (!waiting && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function refresh() {
    render(await getStatus());
  }

  async function startFlow(method) {
    chip.disabled = true;
    chip.textContent = 'Starting…';
    try {
      const resp = await fetch(base() + '/api/auth/compose-api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok && body.error) {
        flowBox.style.display = '';
        flowBox.textContent = 'Could not start signing in: ' + body.error;
      }
    } catch (e) {
      flowBox.style.display = '';
      flowBox.textContent = 'Could not reach the workbench: ' + e;
    }
    refresh();
  }

  async function signOut() {
    if (!confirm('Sign out of compose-api? This signs the compose-api CLI out too.')) return;
    chip.disabled = true;
    chip.textContent = 'Signing out…';
    try {
      await fetch(base() + '/api/auth/compose-api/logout', { method: 'POST' });
    } catch (_e) { /* the status below says what happened */ }
    refresh();
  }

  refresh();
})();
