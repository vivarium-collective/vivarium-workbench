// tests/js/test_run_target_signin.js — run with: node tests/js/test_run_target_signin.js
//
// The composite card's "Sign in" beside its run-target badge (static/composite-card.js). compose-api allows anonymous
// runs, so the badge itself stays "Runs: Cloud" when the person is not signed in; the card offers the sign-in there,
// where students look. The real _signInOffered and _applyRunTargetBadges are lifted out and executed (as
// test_run_target_badge.js does), over the /api/remote-dispatch-preflight body.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'vivarium_workbench', 'static', 'composite-card.js'), 'utf8');

function extract(name) {
  const start = SRC.indexOf('function ' + name + '(');
  assert.ok(start !== -1, name + ' not found in composite-card.js');
  let depth = 0, i = SRC.indexOf('{', start);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    else if (SRC[i] === '}' && --depth === 0) break;
  }
  return SRC.slice(start, i + 1);
}

function card(windowExtras) {
  const badge = { style: {}, setAttribute() {}, textContent: '', title: '' };
  const signIn = { style: { display: 'none' } };
  const document = {
    querySelectorAll: (sel) => (sel.indexOf('pcard-runtarget') !== -1 ? [badge]
      : sel.indexOf('pcard-signin') !== -1 ? [signIn] : []),
  };
  const window = Object.assign({}, windowExtras);
  // eslint-disable-next-line no-new-func
  const apply = new Function('window', 'document',
    ['_selectedBuildTip', '_computeRunTarget', '_signInOffered', '_runTargetClickable', '_applyRunTargetBadges']
      .map(extract).join('\n') + '\nreturn _applyRunTargetBadges;')(window, document);
  return { apply, badge, signIn };
}

const COMPOSE = { target: 'deployment', ok: true, reason: 'inferred', dispatch: 'compose-api',
                  message: 'compose-api runs this workspace' };
const SIGNIN = { ComposeSignin: { start() {}, available: () => true } };

// Falsifies: a person about to send unsigned runs to compose-api is not offered the sign-in on the card.
let c = card(SIGNIN);
c.apply(Object.assign({}, COMPOSE, { signed_in: false }));
assert.strictEqual(c.badge.textContent, 'Runs: Cloud', 'anonymous runs are allowed: the badge stays Cloud');
assert.strictEqual(c.signIn.style.display, 'inline-block', 'not signed in -> Sign in offered beside the badge');

// Falsifies: the action shows where it does not apply.
c = card(SIGNIN);
c.apply(Object.assign({}, COMPOSE, { signed_in: true }));
assert.strictEqual(c.signIn.style.display, 'none', 'signed in -> no Sign in');
c = card(SIGNIN);
c.apply({ target: 'local', ok: true, reason: 'local', message: 'runs locally' });
assert.strictEqual(c.signIn.style.display, 'none', 'a local workspace -> no Sign in');
c = card(SIGNIN);
c.apply({ target: 'deployment', ok: true, reason: 'ok', dispatch: 'viva-v1-document', message: 'viva' });
assert.strictEqual(c.signIn.style.display, 'none', 'a backend that reports no signed_in -> no Sign in');
c = card({});
c.apply(Object.assign({}, COMPOSE, { signed_in: false }));
assert.strictEqual(c.signIn.style.display, 'none', 'no sign-in flow on the page -> no Sign in');

// Falsifies: the link shows where a sign-in here would not sign the calls in (no sign-in extra, a CLI profile for
// another API): the preflight says signed_in false there too, but the sign-in flow says it is not available.
c = card({ ComposeSignin: { start() {}, available: () => false } });
c.apply(Object.assign({}, COMPOSE, { signed_in: false }));
assert.strictEqual(c.signIn.style.display, 'none', 'sign-in not offered here -> no Sign in');

// Falsifies: clicking "Sign in" does not start the shared flow, or flips Local/Cloud as a badge click does.
const started = [], scopes = [];
const win = { ComposeSignin: { start: (m) => started.push(m), available: () => true },
              VivEnv: { setScope: (x) => scopes.push(x), isCloud: () => true } };
// eslint-disable-next-line no-new-func
const onClick = new Function('window', ['_runTargetClickable', '_onRunTargetClick'].map(extract).join('\n') +
  '\nreturn _onRunTargetClick;')(win);
const ev = (match) => ({ target: { closest: (sel) => (sel.indexOf(match) !== -1
  ? { getAttribute: () => '1' } : null) }, preventDefault() {}, stopPropagation() {} });
onClick(ev('pcard-signin'));
assert.deepStrictEqual([started, scopes], [['auto'], []], 'Sign in starts the flow and leaves the scope alone');
onClick(ev('pcard-runtarget'));
assert.deepStrictEqual([started, scopes], [['auto'], ['local']], 'the badge itself still flips Local/Cloud');

console.log('PASS  tests/js/test_run_target_signin.js');
