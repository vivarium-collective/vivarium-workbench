// compose-api sign-in from the page (lib/compose_signin.py).
//
// On load: GET /api/auth/compose-api/status. Sign-in not offered here (no compose-api backend, no sign-in extra,
// COMPOSE_API_TOKEN set, or the CLI profile is for another API) -> the row stays hidden. Otherwise the chip says
// "Sign in" or "Signed in · <profile>".
//
// "Sign in": POST /api/auth/compose-api/start {method: "auto"}; the client opens this computer's browser, or, over SSH
// or without a display, shows a code to enter on any device. "Use a code instead" asks for the code directly (a
// workbench on another machine). While it waits, the page polls the status and shows the client's own messages (never
// a token). Signed in: clicking signs out (for the compose-api CLI too) after a confirm; "Sign in again" makes a new
// login. The client keeps the current one if that sign-in is cancelled or fails before Google returns a new login;
// once it does, the new login replaces the old.
//
// window.ComposeSignin.start(method) is the same flow for other places (the composite card's "Sign in" beside its
// "Runs: Cloud" badge), and window.ComposeSignin.available() says whether a sign-in is offered here at all (the badge
// offers it only then). Its progress shows in #viv-compose-flow, a panel outside any page, so it is seen from anywhere;
// a click on the panel dismisses it. When a sign-in ends, the run-target badges are re-checked.

(function () {
  const row = document.getElementById('viv-compose-row');
  const chip = document.getElementById('viv-compose-chip');
  const hint = document.getElementById('viv-compose-hint');
  const flowBox = document.getElementById('viv-compose-flow');
  if (!row || !chip) return;

  const base = () => window.__BASE_PATH__ || '';
  const POLL_MS = 1500;
  let pollTimer = null;
  let wasWaiting = false;
  let available = false;
  let dismissedFlow = null;

  // The panel: one place for the sign-in's instructions and outcome. Text is rewritten only when it changes (an
  // aria-live region announces every change); a click hides it, and a dismissed sign-in stays hidden.
  function setPanel(text) {
    if (!text) {
      flowBox.style.display = 'none';
      flowBox.textContent = '';
      return;
    }
    flowBox.style.display = '';
    if (flowBox.textContent !== text) flowBox.textContent = text;
  }
  flowBox.title = 'Click to dismiss';
  flowBox.onclick = () => { dismissedFlow = flowBox.dataset.flow || null; setPanel(''); };

  async function getStatus() {
    try {
      const resp = await fetch(base() + '/api/auth/compose-api/status');
      return await resp.json();
    } catch (_e) {
      return { available: false };
    }
  }

  // Only a sign-in that is waiting, or one that failed, is shown: a finished one is the chip's to say.
  function showFlow(flow) {
    if (!flow || flow.state === 'ok' || (!flow.notes.length && !flow.error) || flow.id === dismissedFlow) {
      if (flowBox.dataset.flow) setPanel('');
      return;
    }
    flowBox.dataset.flow = flow.id;
    setPanel(flow.notes.join('\n') + (flow.error ? '\n' + 'Sign-in failed: ' + flow.error : ''));
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function render(status) {
    available = !!status.available;
    if (typeof window._applyRunTargetBadges === 'function') window._applyRunTargetBadges();
    if (!status.available) {
      row.style.display = 'none';
      stopPolling();
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
      const again = document.createElement('a');
      again.href = '#';
      again.textContent = 'Sign in again';
      again.title = 'A new login; the current one stays if you cancel';
      again.onclick = (e) => { e.preventDefault(); startFlow('auto'); };
      hint.append(again);
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
    if (!waiting) stopPolling();
    if (wasWaiting && !waiting && typeof window._refreshRunTargetBadges === 'function') {
      window._refreshRunTargetBadges();
    }
    wasWaiting = !!waiting;
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
      if (!resp.ok) {
        flowBox.dataset.flow = '';
        setPanel('Could not start signing in: ' + (body.error || body.message || resp.status));
      }
    } catch (e) {
      flowBox.dataset.flow = '';
      setPanel('Could not reach the workbench: ' + e);
    }
    refresh();
  }

  async function signOut() {
    if (!confirm('Sign out of compose-api? This signs the compose-api CLI out too.')) return;
    chip.disabled = true;
    chip.textContent = 'Signing out…';
    let message = '';
    try {
      const resp = await fetch(base() + '/api/auth/compose-api/logout', { method: 'POST' });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) message = 'Could not sign out: ' + (body.error || resp.status);
      else if (body.revocation === 'unavailable') {
        message = 'Signed out on this computer. Auth0 could not be reached to revoke the login; it ends when it expires.';
      }
    } catch (e) {
      message = 'Could not reach the workbench: ' + e;
    }
    await refresh();
    if (message) { flowBox.dataset.flow = ''; setPanel(message); }
  }

  window.ComposeSignin = { start: startFlow, refresh, available: () => available };
  refresh();
})();
