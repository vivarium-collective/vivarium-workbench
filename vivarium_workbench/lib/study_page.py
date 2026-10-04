"""Study-detail page builder — Jinja2 render + page builder.

Extracted from ``vivarium_workbench.server`` so both the FastAPI seam
(``api/app.py``) and ``server.py``'s handler can share one implementation.
``server.py`` re-exports ``_render_study_detail_html`` as a thin shim (2-arg
``(name, spec)``) so ``publish.py`` and existing call-sites keep working
unchanged.

Public API
----------
render_study_detail_html(ws_root, name, spec)  → str
    Render the study-detail Jinja2 template for *name* against *spec*.

build_study_detail_page(ws_root, slug)  → (html, status_code)
    Full page builder: slug-validate → 404; spec-load → 404; render → 200.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

import vivarium_workbench as _vd_pkg

_TEMPLATES_DIR: Path = Path(_vd_pkg.__file__).parent / "templates"

from vivarium_workbench.lib.study_spec import SLUG_RE as _SLUG_RE  # noqa: E402
from vivarium_workbench.lib.workspace_paths import WorkspacePaths  # noqa: E402
from markupsafe import Markup as _Markup, escape as _escape  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (moved from server.py)
# ---------------------------------------------------------------------------

def _enrich_runs_with_meta(study_dir: Path, runs: list) -> list:
    """Merge per-run metadata from studies/<name>/runs.db into study.runs[].

    study.yaml's runs[] carries only the slim authoritative fields (run_id,
    variant, composite, label, status, n_steps). The runs_meta table in
    runs.db carries the rich per-run record (spec_id, params, started_at,
    completed_at, log_path). The Runs tab needs both. We copy the rich
    fields onto each entry under namespaced keys (``meta_*``) so the
    template doesn't have to know which DB they came from.

    Tolerant: if runs.db is absent, has no row for a run_id, or fails to
    open, the run entry is returned unchanged.
    """
    if not runs:
        return runs
    db = study_dir / "runs.db"
    rows: list = []
    if db.is_file():
        import sqlite3 as _sql
        try:
            conn = _sql.connect(str(db))
            conn.row_factory = _sql.Row
            rows = conn.execute(
                "SELECT run_id, spec_id, params_json, started_at, completed_at, "
                "n_steps, status, log_path FROM runs_meta"
            ).fetchall()
            conn.close()
        except _sql.Error:
            rows = []
    import json as _json
    by_id = {r["run_id"]: r for r in rows}
    enriched = []
    for r in runs:
        out = dict(r)
        # Always set meta_* keys so the Jinja template can call filters
        # against them unconditionally (None → empty cell).
        out.setdefault("meta_spec_id", None)
        out.setdefault("meta_started_at", None)
        out.setdefault("meta_completed_at", None)
        out.setdefault("meta_duration_sec", None)
        out.setdefault("meta_params", {})
        out.setdefault("meta_log_path", None)
        m = by_id.get(r.get("run_id"))
        if m is not None:
            try:
                params = _json.loads(m["params_json"] or "{}")
            except (ValueError, TypeError):
                params = {}
            started = m["started_at"]
            completed = m["completed_at"]
            duration = (completed - started) if (started and completed) else None
            out["meta_spec_id"] = m["spec_id"]
            out["meta_started_at"] = started
            out["meta_completed_at"] = completed
            out["meta_duration_sec"] = duration
            out["meta_params"] = params
            out["meta_log_path"] = m["log_path"]
        enriched.append(out)
    return enriched


def _humanize_study_name(slug: str) -> dict:
    """Mirror of JS _humanizeStudyName: peel a leading '<prefix>-NN[a-z]?-' into
    a chip and humanize the remainder. Keeps dashboard + report names identical."""
    m = re.match(r"^([a-z]+-\d+[a-z]*)-(.+)$", slug or "")
    if not m:
        return {"chip": "", "title": (slug or "").replace("-", " ")}
    rest = m.group(2).replace("-", " ")
    rest = rest[:1].upper() + rest[1:]
    if len(rest) > 60:
        rest = rest[:57] + "…"
    return {"chip": m.group(1), "title": rest}


def _jinja_fmt_ts(ts) -> str:
    """Format a unix timestamp as 'YYYY-MM-DD HH:MM' UTC, or '' if missing.

    Returns '' for None, empty values, AND undefined (Jinja's Undefined
    sentinel — e.g. when the template walks ``r.meta_started_at or
    r.started_at`` against a dict that has neither key). The previous
    ``(TypeError, ValueError)`` excludes Jinja's UndefinedError, which
    escaped here as a template-render failure for every <tr> in the
    Runs table whenever the merged run dict was missing both fields.
    """
    try:
        ts = float(ts)
    except Exception:
        return ""
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _jinja_fmt_duration(seconds) -> str:
    """Format a duration in seconds as '12s', '1m 30s', '2h 15m', or '' if missing.

    Same Undefined-tolerance contract as _jinja_fmt_ts above.
    """
    try:
        seconds = float(seconds)
    except Exception:
        return ""
    if seconds < 0:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _jinja_markdown(text):
    """Render authored prose (e.g. ``study.conclusion``) as Markdown.

    Authors often hard-wrap conclusion text at ~80 columns; rendered in a
    ``<pre>`` that produces a narrow column with wasted width. Markdown reflows
    soft-wrapped lines within a paragraph to fill the container, keeps
    blank-line paragraph breaks, and turns ``1. 2. 3.`` into an ordered list.

    Defensive: raw HTML in the source is NOT interpreted (CommonMark's default
    ``html=False``) so it is safe for study-authored content, and if
    ``markdown-it-py`` is unavailable/errors it falls back to the previous
    escaped, newline-preserving ``<pre>`` rendering.
    """
    from markupsafe import Markup, escape
    if text is None:
        return Markup("")
    s = str(text)
    if not s.strip():
        return Markup("")
    try:
        from markdown_it import MarkdownIt
        # html=False: the CommonMark preset otherwise passes raw HTML through;
        # disable it so study-authored content can't inject markup (XSS-safe).
        return Markup(MarkdownIt("commonmark", {"html": False}).render(s))
    except Exception:  # noqa: BLE001 — degrade to the old pre-wrap behavior
        return Markup(
            '<pre style="white-space:pre-wrap;font-family:inherit;margin:0">'
            + str(escape(s)) + '</pre>'
        )


# ---------------------------------------------------------------------------
# G3 — shared outcome vocabulary (Fable §10.1, §14.1(4))
# ---------------------------------------------------------------------------
# ONE display mapping from the existing verdict/test tokens onto the four-value
# audit vocabulary: met / conditional-pass / not met / not assessable. This is
# a DISPLAY-ONLY remap — it never changes a stored token or a computation, and
# every later G task (G4-G8) reuses it so the vocabulary reads identically
# everywhere on the study page.
#
# Three token families feed it (confirmed by grep, not invented):
#   - test/verdict tokens — study.tests[]/runs[].outcomes[].result,
#     conclusion_card tracks: PASS / FAIL / PARTIAL / SKIP / PENDING / GAP
#     (study_spec._latest_outcomes, study_derivations.GATE_RESULT_NORM,
#     conclusion_card._RESULT_TO_CANON).
#   - report-card verdict tokens — study_spec._REPORT_CARD_VERDICTS:
#     within_tol / drift / mismatch / ungraded.
#   - acceptance-criterion result tokens — spine_acceptance's per-criterion
#     `result` (study_enrichment.study_acceptance_criterion), sourced from
#     viva_superpowers.investigation_status's _CRIT_PASS/_CRIT_FAIL/
#     _CRIT_CAVEATS/_CRIT_PROGRESS: passing / failing /
#     passing-with-caveats / in-progress.
# Matching is case-insensitive. Anything outside these three families — an
# unknown token, empty string, or None — degrades to "not assessable" (spec
# §2 R2: absent != empty; never blank, never a crash).
_OUTCOME_TOKEN_MAP: dict[str, str] = {
    # test/verdict tokens
    "PASS": "met",
    "FAIL": "not met",
    "PARTIAL": "conditional-pass",
    "SKIP": "not assessable",
    "PENDING": "not assessable",
    "GAP": "not assessable",
    # report-card tokens (study_spec._REPORT_CARD_VERDICTS)
    "WITHIN_TOL": "met",
    "DRIFT": "conditional-pass",
    "MISMATCH": "not met",
    "UNGRADED": "not assessable",
    # acceptance-criterion tokens (viva_superpowers.investigation_status)
    "PASSING": "met",
    "FAILING": "not met",
    "PASSING-WITH-CAVEATS": "conditional-pass",
    "IN-PROGRESS": "not assessable",
}

# CSS-class-safe slug + single-character glyph per audit outcome, so every
# caller renders the same color/glyph instead of re-deriving one.
_OUTCOME_CLASS: dict[str, str] = {
    "met": "met",
    "conditional-pass": "conditional",
    "not met": "not-met",
    "not assessable": "not-assessable",
}
_OUTCOME_GLYPH: dict[str, str] = {
    "met": "✓",
    "conditional-pass": "◐",
    "not met": "✗",
    "not assessable": "○",
}


def outcome_label(token) -> str:
    """Map an existing verdict/test token to the four-value audit vocabulary.

    ``met`` / ``conditional-pass`` / ``not met`` / ``not assessable`` — see
    ``_OUTCOME_TOKEN_MAP`` above for the confirmed token set. Case-insensitive;
    ``None``/empty/unknown tokens map to ``"not assessable"`` (never raises,
    never blank). Registered as the Jinja filter ``outcome_label``.
    """
    key = str(token).strip().upper() if token is not None else ""
    return _OUTCOME_TOKEN_MAP.get(key, "not assessable")


def outcome_class(token) -> str:
    """CSS-class-safe slug for ``outcome_label(token)`` (e.g. ``"conditional"``)."""
    return _OUTCOME_CLASS[outcome_label(token)]


def outcome_glyph(token) -> str:
    """Single-character glyph for ``outcome_label(token)`` (e.g. ``"◐"``)."""
    return _OUTCOME_GLYPH[outcome_label(token)]


# ---------------------------------------------------------------------------
# G7 — honest attribution from existing fields (Fable §11.2, §14.1(5)).
#
# Confirmed actor-bearing fields (grepped, not invented) that are actually
# plumbed to the study-detail spec/template context:
#   - feedback_tracked.items[].author / .ts                — who raised a
#     tracked feedback item / when (viva_superpowers.feedback_tracking
#     .study_feedback_tracked, attached at study_spec.py's
#     spec["feedback_tracked"]).
#   - feedback_tracked.items[].responded_by / .responded_at — who answered /
#     when (same source; only present when a response exists).
#   - expert_decisions_needed[].asked_to                    — who a decision
#     is routed to (investigations._project_v4_redesign_to_legacy_view /
#     the mirrored design_pivot_required list). No timestamp field exists
#     for this one — the compact renderer omits "when" when absent.
#
# Checked and deliberately NOT rendered as attribution (no real actor
# identity behind them):
#   - computed_gate_verdict.evaluated_by (persisted pipeline_gate.gate_evaluator
#     / viva_superpowers.study_verdict.roll_up_verdict) is ALWAYS the literal
#     string "code" — a machine-evaluator marker, not a person/agent identity.
#     Rendering "by code" on every gate row would be decorative, not honest
#     attribution, so the six-gate ladder carries no per-gate actor here.
#   - conclusion.verdict.json (vivarium_workbench.lib.conclusion_card
#     .build_conclusion_verdict) is `{schema, overall, tracks, insight}` —
#     no actor, no timestamp field at all. G8 is the task that adds the
#     freeze-time surface; G7 only reads what's already there, so the
#     verdict card's attribution line renders the literal "unattributed"
#     token until that field exists.
#   - findings[] and spine_acceptance.criteria[] (study_acceptance_criterion /
#     viva_superpowers.investigation_status.roll_up_acceptance) carry no
#     author/responded_by/decided_by field in the current schema — only
#     {study, behavior, result}.
#
# Human vs agent: never inferred from a bare person-name guess. The ONLY
# thing that classifies a recorded name as "agent" is it matching a
# well-known LLM/automation naming token — the same category of signal
# viva_superpowers.investigation_close.derive_contributors already uses
# (there: an email's "noreply@anthropic.com" / "bot" / "ci" substring flags
# a git co-author as an agent). Every other non-empty name defaults to
# "human" — a documented DEFAULT, not a claim about who that person is:
# these fields are free-text names filled in by whoever authored them,
# historically human reviewers/authors. Empty/None -> "unattributed".
# ---------------------------------------------------------------------------

_KNOWN_AGENT_NAME_TOKENS: frozenset[str] = frozenset({
    "claude", "gpt", "chatgpt", "codex", "copilot", "gemini", "llama",
    "mistral", "deepseek", "qwen", "grok", "bot", "ci",
})


def actor_kind(actor) -> str:
    """Classify a recorded actor value as ``"human"`` / ``"agent"`` /
    ``"unattributed"``. See the module comment above for the honesty rule:
    only a known agent/automation naming TOKEN flips the classification to
    ``"agent"``; every other non-empty name defaults to ``"human"``; empty/
    None is ``"unattributed"`` (never blank, never raises)."""
    s = str(actor).strip() if actor is not None else ""
    if not s:
        return "unattributed"
    low = s.lower()
    first_token = re.split(r"[\s\-_/]+", low)[0]
    if low in _KNOWN_AGENT_NAME_TOKENS or first_token in _KNOWN_AGENT_NAME_TOKENS:
        return "agent"
    return "human"


def actor_model(actor) -> Optional[str]:
    """The model string for an ``"agent"``-classified actor, else ``None``.

    Never fabricated: this is exactly the recorded actor string (e.g.
    ``"claude-opus-4-8"``) — the same value ``actor_kind`` classified,
    re-surfaced under a clearer name. No lookup, no version guess."""
    if actor_kind(actor) != "agent":
        return None
    return str(actor).strip()


_ACTOR_KIND_GLYPH: dict[str, str] = {
    "human": "◇", "agent": "⚙", "unattributed": "○",
}


def actor_glyph(actor) -> str:
    """Single-character glyph for ``actor_kind(actor)``. Registered as the
    Jinja filter ``actor_glyph`` — mirrors ``outcome_glyph``'s style."""
    return _ACTOR_KIND_GLYPH[actor_kind(actor)]


def attribution_text(actor, when=None) -> str:
    """Compact ``"by <actor> · <when>"`` string, or the literal
    ``"unattributed"`` token when no actor is recorded — never blank (see
    module comment: absent != empty). ``when`` is omitted from the string
    when absent/falsy. Registered as the Jinja filter ``attribution_text``."""
    if actor_kind(actor) == "unattributed":
        return "unattributed"
    label = str(actor).strip()
    when_s = str(when).strip() if when else ""
    return f"by {label} · {when_s}" if when_s else f"by {label}"


# ---------------------------------------------------------------------------
# G8 — frozen-record indicator for the conclusion card (Fable §11.4).
#
# The conclusion card (vivarium_workbench.lib.conclusion_card
# .write_conclusion_card) is the ONLY writer of
# viz/report_card/conclusion.verdict.json, and it's called once per post-run
# flush — so the file's mere existence (parsed as a dict) already IS the
# freeze signal; there is no separate "frozen: true" field anywhere to read,
# and this task does not add one. No timestamp is stored INSIDE the payload
# either ({schema, overall, tracks, insight} — checked in conclusion_card.py,
# not guessed), so "when" comes from the file's own mtime.
# study_spec.load_study_detail_spec attaches both under
# spec["conclusion_card_frozen"] = {"when": <unix ts>, "payload": <dict>}
# (absent entirely when no card has been persisted — never a fabricated
# freeze). This module only owns the pure digest computation (no I/O) so it
# can be unit-tested directly, mirroring outcome_label/actor_kind's style.
# ---------------------------------------------------------------------------

def conclusion_digest(payload, length: int = 10) -> str:
    """Short deterministic content digest of a frozen verdict payload.

    Canonical serialization (``json.dumps(payload, sort_keys=True,
    separators=(",", ":"))``) sorts keys at every nesting level, so the
    digest is order-independent: the same payload with keys reordered at any
    level yields the same digest. First ``length`` hex chars of a
    ``hashlib.sha256`` over that serialization — deterministic (same payload
    -> same digest), and never fabricated (always computed fresh from
    whatever ``payload`` is passed in, never a stored/cached value). A
    non-dict ``payload`` degrades to hashing ``{}`` rather than raising.
    Registered as the Jinja filter ``conclusion_digest``."""
    canon = json.dumps(
        payload if isinstance(payload, dict) else {},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
# G2 — the gating model (Fable §13, docs/superpowers/specs/
# 2026-08-01-study-design-fable-pass.md). The six status axes ARE the six
# gates already (design/implementation/simulation/evaluation/gate/
# expert_review) — §13's table maps them:
#   Plan ← design_status · Execution ← simulation_status ·
#   Evidence ← evaluation_status · Quality ← evaluation_status (yes, the
#   SAME axis backs both Evidence and Quality — that's the spec's table, not
#   a bug here) · Decision ← gate_status · Release ← expert_review_status.
# `implementation_status` has no gate — §13's table simply never assigns it
# one (it stays a plain authored axis, unchanged from before this task).
# ---------------------------------------------------------------------------

# §13.2 gate-state vocabulary. Axis values are free-authored strings (not a
# closed enum), so classification is a heuristic token map; anything
# unrecognized degrades to "not-assessed" rather than guessing a pass —
# mirrors outcome_label's "never invent, never blank" rule.
_GATE_STATE_TOKENS: dict[str, str] = {
    # passed — the axis (or computed evaluator) is done and clean
    "passed": "passed", "pass": "passed", "ok": "passed", "approved": "passed",
    "complete": "passed", "completed": "passed", "ran": "passed",
    "evaluated": "passed", "done": "passed", "designed": "passed",
    "implemented": "passed", "signed_off": "passed",
    # passed-with-conditions — assessed, open findings / partial credit
    "needs_calibration": "passed-with-conditions", "partial": "passed-with-conditions",
    "mixed": "passed-with-conditions", "drift": "passed-with-conditions",
    # blocked — assessed and failing
    "blocked": "blocked", "failed": "blocked", "fail": "blocked",
    "mismatch": "blocked", "stale": "blocked", "failed_evaluation": "blocked",
    # waived — explicitly accepted as residual risk (D1.3; not authored
    # anywhere on this page yet, kept for forward-compat with §13.2's vocab)
    "waived": "waived",
    # not-assessed — nothing decided yet (includes "in progress"/"pending":
    # not yet a finding, just not started or not finished)
    "not_started": "not-assessed", "not_run": "not-assessed",
    "not_evaluated": "not-assessed", "planning": "not-assessed",
    "planned": "not-assessed", "pending": "not-assessed",
    "in_progress": "not-assessed", "running": "not-assessed",
}
_GATE_STATES = ("not-assessed", "passed", "passed-with-conditions", "blocked", "waived")
# Severity rank among the states that carry a REAL assessment (higher = more
# in need of attention): a blocked/at-risk state must never hide behind a
# clean pass. "not-assessed" has no rank here — it isn't a severity, it's the
# absence of an opinion, and _worst_gate_state below treats it as neutral
# (filtered out before ranking) rather than as a value that can outrank
# "passed". See _worst_gate_state's docstring — this was Fable G2's fix-round-1
# bug: not-assessed used to outrank passed and collapsed every gate with an
# empty authored axis + a passing COMPUTED value down to grey.
_GATE_STATE_RANK = {
    "blocked": 4, "passed-with-conditions": 3, "waived": 1, "passed": 0,
}
_GATE_STATE_GLYPH = {
    "not-assessed": "○", "passed": "✓", "passed-with-conditions": "◐",
    "blocked": "✗", "waived": "⚑",
}


def gate_state(token) -> str:
    """Classify a raw axis/computed status token into the §13.2 gate-state
    vocabulary (``not-assessed`` / ``passed`` / ``passed-with-conditions`` /
    ``blocked`` / ``waived``). Unset or unrecognized tokens degrade to
    ``not-assessed``. Registered as the Jinja filter ``gate_state``."""
    if not token:
        return "not-assessed"
    return _GATE_STATE_TOKENS.get(str(token).strip().lower(), "not-assessed")


def gate_state_glyph(token) -> str:
    """Single-character glyph for ``gate_state(token)``. Registered as the
    Jinja filter ``gate_state_glyph``."""
    return _GATE_STATE_GLYPH[gate_state(token)]


# ---------------------------------------------------------------------------
# C2 — findings-ledger assertion formatters (Fable §4.1, §6 #12).
#
# `study.findings[*].evidence.observed` and the cited behavior test's
# `pass_if`/`expect` band are both sometimes an assertion-shaped DICT (e.g.
# ``{"observed": 0.42, "op": "<=", "pass_if": 0.5}``) rather than a scalar.
# The old template rendered these two ways, both broken for a dict value:
# a bare ``{{ ... }}`` interpolation (prints a Python dict repr,
# ``{'op': '<=', ...}``, into the page) or a ``| tojson`` filter (crashes
# outright — ``TypeError: Object of type Undefined is not JSON serializable``
# — when a *related but absent* key, e.g. a behavior test's `measure`, is
# Jinja's Undefined sentinel rather than a real value; see the `study-detail
# .html` Tests-tab "Assertion" dump this task also guards).
#
# `humanize_assertion` never emits a `{...}` dict repr and never raises on a
# missing key; `kv` is its small-dict fallback (also used standalone in the
# drawer for any residual dict) and HTML-escapes every value itself (so it
# is safe even called outside the autoescaped Jinja context the unit tests
# exercise it in directly). Both are pure formatting — no schema/back-end
# change.
# ---------------------------------------------------------------------------

_ASSERTION_OP_SYMBOLS: dict[str, str] = {
    "<=": "≤", ">=": "≥", "==": "=", "!=": "≠", "<": "<", ">": ">",
}

# Preference order for the "target" half of the phrase when the dict doesn't
# use the canonical `pass_if` key (e.g. a raw `expected_behavior` band).
_ASSERTION_TARGET_KEYS = ("pass_if", "threshold", "expected", "value")


def kv(d) -> str:
    """Inline ``k: v · k: v`` text for a small dict, values HTML-escaped.

    Non-dict input is stringified as-is (``None`` -> ``""``). Nested dict
    values recurse through ``kv`` itself rather than falling back to a
    Python dict repr. Returns a ``Markup`` (pre-escaped) so it renders
    correctly whether called from Python or interpolated in the (already
    autoescaped) Jinja template without double-escaping. Registered as the
    Jinja filter ``kv``."""
    if not isinstance(d, dict):
        return str(d) if d is not None else ""
    parts = []
    for key, val in d.items():
        val_s = kv(val) if isinstance(val, dict) else _escape(str(val))
        parts.append(f"{_escape(str(key))}: {val_s}")
    return _Markup(" · ".join(parts))


def humanize_assertion(a) -> str:
    """Readable phrase for a finding/test assertion value that may be a dict.

    Handles the shapes actually seen on ``evidence.observed`` and a cited
    test's ``pass_if``/``expect`` band: ``{"observed": 0.42, "op": "<=",
    "pass_if": 0.5}`` -> ``"observed 0.42 ≤ 0.5"``; a bare comparator band
    like ``{"op": ">=", "threshold": 3}`` -> ``"≥ 3"``. Ops are mapped to
    their symbol (``<=``->``≤``, ``>=``->``≥``, ``==``->``=``); an
    unrecognized op string passes through unchanged. Missing keys degrade
    gracefully (never raises); a dict with nothing usable falls back to
    ``kv()`` rather than a Python dict repr — ``{`` never appears in the
    output. A scalar/str input is returned stringified (``None`` -> ``""``).
    Registered as the Jinja filter ``humanize_assertion``."""
    if a is None:
        return ""
    if not isinstance(a, dict):
        return str(a)

    op = a.get("op")
    op_symbol = _ASSERTION_OP_SYMBOLS.get(str(op).strip(), str(op)) if op is not None else ""

    # Every piece appended to `parts` is built as (or promoted to) a
    # markupsafe `Markup` — never dropped into a plain f-string. An f-string
    # coerces a `Markup` argument (e.g. `kv(measure)`'s already-escaped
    # output) back into a bare `str`, which silently DISCARDS its "safe"
    # marking; Jinja's autoescape then can't tell it was pre-escaped and
    # escapes the whole returned string a second time (`&lt;` -> `&amp;lt;`).
    # `Markup("%s") % value` / `Markup(...) + Markup(...)` both preserve
    # exactly-once escaping: `%` escapes a raw (non-Markup) substitution,
    # `+` is a no-op re-escape when both sides are already Markup.
    parts = []
    observed = a.get("observed")
    measure = a.get("measure")
    if observed is not None and not isinstance(observed, dict):
        parts.append(_Markup("observed %s") % (observed,))
    elif measure is not None:
        measure_s = kv(measure) if isinstance(measure, dict) else _escape(str(measure))
        parts.append(_Markup("measure ") + measure_s)

    target = None
    target_label = None
    for key in _ASSERTION_TARGET_KEYS:
        val = a.get(key)
        if val is not None and not isinstance(val, dict):
            target, target_label = val, key
            break

    if target is not None and op_symbol:
        parts.append(_Markup("%s %s") % (op_symbol, target))
    elif target is not None:
        parts.append(_Markup("%s: %s") % (target_label, target))

    if parts:
        return _Markup(" ").join(parts)
    return kv(a)


def _worst_gate_state(*states: str | None) -> str:
    """Combine gate states, treating ``not-assessed`` as NEUTRAL/absorbing.

    An empty/unassessed side must defer to whichever OTHER side actually
    carries an assessment — never drag a real ``passed`` down to grey just
    because, say, the authored axis is empty while the COMPUTED value says
    ``passed``. Among states that DO carry a real assessment (``passed`` /
    ``passed-with-conditions`` / ``blocked`` / ``waived``), the worst one
    wins (§_GATE_STATE_RANK) — a blocked/at-risk state must still dominate a
    clean pass. Returns ``not-assessed`` only when every input is absent or
    itself ``not-assessed``."""
    assessed = [s for s in states if s and s != "not-assessed"]
    if not assessed:
        return "not-assessed"
    return max(assessed, key=lambda s: _GATE_STATE_RANK.get(s, 0))


# (key, gate number, gate name, backing axis) — order is lifecycle order,
# exactly §13's table.
_GATES: tuple[tuple[str, int, str, str], ...] = (
    ("plan",      1, "Plan",      "design_status"),
    ("execution", 2, "Execution", "simulation_status"),
    ("evidence",  3, "Evidence",  "evaluation_status"),
    ("quality",   4, "Quality",   "evaluation_status"),
    ("decision",  5, "Decision",  "gate_status"),
    ("release",   6, "Release",   "expert_review_status"),
)


# Task V5 (Fable §5(B)/§6(c.3)): the gap/conditional tier a non-qualifying
# visualization contributes to Evidence's computed state. Reuses the EXISTING
# "passed-with-conditions" token from _GATE_STATES/_GATE_STATE_RANK above —
# never a new state, never "blocked" (a missing figure is a SOFT advisory
# gap, per Fable's decision, not a release blocker).
_VIZ_GAP_STATE = "passed-with-conditions"


def _apply_visualization_gap(gates: list[dict], ws_root, slug: str) -> None:
    """Fold V4's ``viz_gate.study_visualization_status`` into the Evidence
    gate entry of *gates* (mutated in place) — the same computed-state slot
    ``build_gate_ladder`` already populates from ``derived_status``.

    Task Vcal: downgrades ONLY on the ``gap_severity == "warning"`` case (no
    interactive figure at all — the genuine empty/boring problem). The
    ``"info"`` provenance nudge (has an interactive figure + has runs, just
    nothing's run-linked yet) and the silent unrun case (``gap_severity is
    None``) never touch Evidence — a study that simply hasn't been run yet
    isn't a visualization problem, and the soft nudge isn't a gate-worthy
    signal.

    On downgrade: mutates ``computed_state`` via the existing worst-of rank
    (:func:`_worst_gate_state`) to :data:`_VIZ_GAP_STATE`, appends to
    ``computed_source``, records the human reason in ``viz_gap_reason``, and
    flags ``diverges`` when the authored axis says "passed" but the (now
    downgraded) computed state doesn't.

    Tolerant like V4: ``study_visualization_status`` is itself internally
    tolerant (never raises), but this wraps the call anyway and treats ANY
    exception as NO SIGNAL — never a downgrade, never a 500. This is
    deliberately stricter than V4's own report-lint fallback (which treats
    an error as non-qualifying, i.e. still flags an advisory nudge) — a gate
    STATE is a stronger signal than a readiness-panel nudge, so an
    infrastructure error here must not color a gate at all.
    """
    try:
        from vivarium_workbench.lib.viz_gate import study_visualization_status
        viz_status = study_visualization_status(ws_root, slug)
    except Exception:  # noqa: BLE001 — unreadable study: no signal, no downgrade, no 500
        return
    if not isinstance(viz_status, dict) or viz_status.get("gap_severity") != "warning":
        return
    reason = viz_status.get("reason") or "no qualifying figure"
    for entry in gates:
        if entry["key"] != "evidence":
            continue
        prior_state = entry["computed_state"]
        entry["computed_state"] = _worst_gate_state(prior_state, _VIZ_GAP_STATE)
        if entry["computed_source"]:
            entry["computed_source"] = (
                f"{entry['computed_source']} + visualization readiness gate (V4)"
            )
        else:
            entry["computed_source"] = (
                "visualization readiness gate (viz_gate.study_visualization_status)"
            )
        entry["viz_gap_reason"] = reason
        if entry["authored_state"] == "passed" and entry["computed_state"] != "passed":
            entry["diverges"] = True
        entry["state"] = _worst_gate_state(entry["authored_state"], entry["computed_state"])


def build_gate_ladder(spec: dict, ws_root=None, slug: Optional[str] = None) -> list[dict]:
    """The ``status ▾`` gate ladder: the six axes relabeled as the six gates,
    each with its authored state plus — where a machine evaluator's value is
    ALREADY attached to *spec* by :func:`study_spec.load_study_detail_spec`
    (``derived_status`` from ``viva_superpowers.study_status.derive_status``;
    ``computed_gate_verdict`` from the persisted/rolled-up gate evaluator) — a
    computed state and a computed-vs-authored divergence flag.

    Plan and Release have NO computed source wired into *spec* today:
    ``study_verify`` (§13's named Plan evaluator) doesn't exist as an
    importable function in this codebase, and the report-linter's
    ``missing_question``/``undeclared_readouts`` checks are only fetched
    client-side (``GET /api/report-lint``) for the readiness panel, not
    attached to the render-time spec. The conclusion-card freeze (§13's named
    Release evaluator) isn't surfaced as a field yet either (that's task G8).
    Per the task's "don't invent a computed value" rule, both gates render
    authored-only.

    ``ws_root``/``slug`` (Task V5, both optional so existing spec-only
    callers/tests are unaffected): when BOTH are given, folds V4's
    visualization-readiness signal into the Evidence gate's computed state —
    see :func:`_apply_visualization_gap`. Omit either to skip this (e.g. a
    unit test that only cares about the authored/derived axes).

    Returns a list of 6 dicts, lifecycle order, each with: ``key``, ``number``,
    ``name``, ``axis``, ``authored_value``, ``authored_state``,
    ``computed_value``, ``computed_state``, ``computed_source``, ``diverges``,
    ``viz_gap_reason`` (Task V5; ``None`` unless Evidence was downgraded for a
    non-qualifying visualization), and ``state`` (the worst of
    authored/computed, for a single dot/chip color that never hides a red
    gate behind a green one).
    """
    derived = spec.get("derived_status") or {}
    disagreements = {
        d.get("axis"): d for d in (spec.get("status_disagreements") or [])
        if isinstance(d, dict)
    }
    cgv = spec.get("computed_gate_verdict") or {}

    gates: list[dict] = []
    for key, number, name, axis in _GATES:
        authored_value = spec.get(axis)
        entry: dict[str, Any] = {
            "key": key, "number": number, "name": name, "axis": axis,
            "authored_value": authored_value,
            "authored_state": gate_state(authored_value),
            "computed_value": None, "computed_state": None,
            "computed_source": None, "diverges": False,
            "viz_gap_reason": None,
        }
        if axis in derived and isinstance(derived.get(axis), dict):
            d = derived[axis]
            entry["computed_value"] = d.get("value")
            entry["computed_state"] = gate_state(d.get("value"))
            entry["computed_source"] = d.get("source") or "derived from execution state"
            entry["diverges"] = axis in disagreements
        elif axis == "gate_status" and cgv.get("result"):
            entry["computed_value"] = cgv.get("result")
            entry["computed_state"] = gate_state(cgv.get("result"))
            entry["computed_source"] = (
                "spine gate evaluator (study_verdict.roll_up_verdict / "
                "pipeline_gate.gate_evaluator)"
            )
            entry["diverges"] = bool(cgv.get("diverges_from_authored"))
        entry["state"] = _worst_gate_state(entry["authored_state"], entry["computed_state"])
        gates.append(entry)
    if ws_root is not None and slug is not None:
        _apply_visualization_gap(gates, ws_root, slug)
    return gates


def act_gate_states(gates: list[dict]) -> dict[str, str]:
    """Roll the six per-gate states up into the five act-rail dot states fed
    to G1's ``.act-gate-dot[data-gate=...]`` hooks (§9.3: "six gates, five
    dots"). Per §13's Act column: Design←Plan(I); Evidence←Execution+
    Evidence(II); Assurance←Quality(III); Decision←Decision+Release(IV).
    'study' (the Overview act — §9.2 lists it as "(abstract)", outside the
    four numbered acts, so §13 assigns it no gate) is rendered here as the
    worst state across all six gates: the closest single-dot reading of
    "furthest gate passed" (§13.1) available without reimplementing the
    header pill's logic, which this task explicitly leaves alone.

    Each roll-up picks the worst state (§_GATE_STATE_RANK), never an average,
    so a dot never hides a blocked gate behind a passed one.
    """
    by_key = {g["key"]: g["state"] for g in gates}
    return {
        "study": _worst_gate_state(*by_key.values()),
        "design": by_key.get("plan", "not-assessed"),
        "evidence": _worst_gate_state(by_key.get("execution"), by_key.get("evidence")),
        "assurance": by_key.get("quality", "not-assessed"),
        "decision": _worst_gate_state(by_key.get("decision"), by_key.get("release")),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_study_detail_html(ws_root: Path, name: str, spec: dict, *, base_path: str = "") -> str:
    """Render study-detail.html via Jinja2.

    This is the implementation extracted from ``server._render_study_detail_html``.
    ``server.py`` provides a 2-arg shim ``_render_study_detail_html(name, spec)``
    that injects the module-level WORKSPACE as ``ws_root`` so ``publish.py``
    (which calls ``_render_study_detail_html(slug, spec)``) keeps working.

    ``base_path``: URL prefix for subpath hosting (e.g. ``/workbench`` behind a
    reverse proxy/ALB). Defaults to ``""`` (root hosting, unchanged behavior).
    The template's asset refs are normalized to ``/assets/<name>`` form and the
    live-server base-path shim is injected via ``lib.report``'s
    ``_normalize_asset_urls``/``_apply_live_base_path`` — the same helpers
    ``publish.py`` already uses for the static bundle.
    """
    import jinja2
    from vivarium_workbench.lib.investigations import effective_status
    from vivarium_workbench.lib.study_spec import study_dir

    spec = dict(spec)
    spec["runs"] = _enrich_runs_with_meta(study_dir(ws_root, name), spec.get("runs") or [])
    # Normalize implementation_requirements / gaps so the template iterates a
    # list of dicts — never a prose STRING.
    from vivarium_workbench.lib.spec_norm import normalize_requirements as _normalize_requirements
    if spec.get("implementation_requirements") is not None:
        spec["implementation_requirements"] = _normalize_requirements(
            spec.get("implementation_requirements"))
    if spec.get("gaps") is not None:
        spec["gaps"] = _normalize_requirements(spec.get("gaps"))
    # F1: compute a single headline status from the multi-axis fields.
    spec["_effective_status"] = effective_status(spec)
    # Deterministic study kind (biological/computational/theoretical), used by
    # the template to de-bias page chrome (Task 4). Never overwrites an
    # author's explicit `kind` — infer_study_kind() preserves it.
    from vivarium_workbench.lib.study_kind import infer_study_kind
    spec["kind"] = infer_study_kind(spec)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    env.filters["fmt_ts"] = _jinja_fmt_ts
    env.filters["fmt_duration"] = _jinja_fmt_duration
    env.filters["markdown"] = _jinja_markdown
    env.filters["outcome_label"] = outcome_label
    env.filters["outcome_class"] = outcome_class
    env.filters["outcome_glyph"] = outcome_glyph
    env.filters["gate_state"] = gate_state
    env.filters["gate_state_glyph"] = gate_state_glyph
    env.filters["actor_kind"] = actor_kind
    env.filters["actor_glyph"] = actor_glyph
    env.filters["attribution_text"] = attribution_text
    env.filters["conclusion_digest"] = conclusion_digest
    env.filters["humanize_assertion"] = humanize_assertion
    env.filters["kv"] = kv
    tpl = env.get_template("study-detail.html")
    _hn = _humanize_study_name(name)
    # W15 — open epistemic debts, computed server-side via the deterministic
    # viva_superpowers collector. Defensive: degrade to no panel if not importable.
    epistemic_debts: list = []
    try:
        from vivarium_workbench.lib.needs_attention import open_epistemic_debts
        epistemic_debts = open_epistemic_debts(spec) or []
    except Exception:
        epistemic_debts = []
    # Composite-resolution lint: flag declared composite refs that don't resolve.
    unresolved_composites: list = []
    try:
        from vivarium_workbench.lib.composite_lookup import (
            known_composite_ids, unresolved_study_composite_refs,
        )
        unresolved_composites = unresolved_study_composite_refs(
            spec, known_composite_ids(ws_root)) or []
    except Exception:
        unresolved_composites = []
    # Fable G2: the six status axes relabeled as the six gates (§13), plus
    # the act-rail dot states they feed (G1's `.act-gate-dot[data-gate=...]`
    # hooks). Pure render-time derivation from fields load_study_detail_spec
    # already attached (derived_status, computed_gate_verdict,
    # status_disagreements) — never modifies study.yaml.
    gate_ladder = build_gate_ladder(spec, ws_root=ws_root, slug=name)
    gate_states = act_gate_states(gate_ladder)
    # Study-spine reorg (spec §3.6, plan Task 3): the Tests panel must show
    # the COMPLETE set of this study's report cards — every card under
    # `viz/report_card/`, not only the ones wired to a behavior_tests /
    # expected_behavior entry. render_report_cards_section scans the
    # directory directly (same source study_spec.report_card_urls feeds), so
    # it renders cards the per-row expander below would otherwise miss.
    # Best-effort: a study with no report cards renders "" and the section
    # drops (§2 R2 absent != empty), same contract single_study_report.py
    # already uses for the static published report.
    try:
        from vivarium_workbench.lib.report_card_section import render_report_cards_section
        report_cards_html = render_report_cards_section(ws_root, name)
    except Exception:  # noqa: BLE001
        report_cards_html = ""
    # Result figure: show the study's primary dynamics SVG so the Overview shows
    # the visual result, not just prose. Prefer the closed-loop "*-dynamics.svg";
    # degrade silently to no figure. Trusted (workspace-authored) SVG.
    #
    # Rendered as an <img> data-URI, NOT inline <svg> markup — mirrors the
    # Visualizations tab's _svgImg (study-detail.js): bigraph-loom figures embed
    # their nodes as <foreignObject> HTML, and WebKit renders foreignObject at
    # intrinsic size (ignoring the viewBox->viewport scale) when the SVG is
    # inlined, so `max-width:100%` on the <svg> does NOT stop it overflowing the
    # .ctr-figure card. As an <img> the browser rasterizes the whole document
    # (foreignObject included) and scales it with plain `max-width` — correct in
    # every engine, shrink-only. quote() (not base64) keeps UTF-8 math glyphs.
    result_figure = ""
    try:
        import re as _re
        from urllib.parse import quote as _urlquote
        sdir = WorkspacePaths.load(ws_root).study_dir(name)
        sdir = sdir if sdir.is_dir() else None
        vizzes = spec.get("visualizations") or []
        addr = lambda v: (str(v.get("address", "")).split(":", 1)[-1]
                          if isinstance(v, dict) else "")
        pick = next((v for v in vizzes if addr(v).endswith("-dynamics.svg")),
                    next((v for v in vizzes if addr(v).endswith(".svg")), None))
        if sdir and pick:
            f = sdir / addr(pick)
            if f.is_file():
                svg = _re.sub(r"<\?xml.*?\?>\s*|<!DOCTYPE.*?>\s*", "",
                              f.read_text(encoding="utf-8", errors="replace"), flags=_re.S).lstrip()
                result_figure = (
                    '<img class="ctr-figure-img" alt="result figure" loading="lazy" '
                    'style="display:block;max-width:100%;height:auto" '
                    'src="data:image/svg+xml,' + _urlquote(svg) + '">'
                )
    except Exception:
        result_figure = ""
    html = tpl.render(study=spec, name=name, result_figure=result_figure,
                      display_name=spec.get("title") or _hn["title"],
                      name_chip=_hn["chip"],
                      epistemic_debts=epistemic_debts,
                      unresolved_composites=unresolved_composites,
                      gate_ladder=gate_ladder,
                      gate_states=gate_states,
                      report_cards_html=report_cards_html,
                      base_path=base_path)
    from vivarium_workbench.lib.report import _apply_live_base_path, _normalize_asset_urls
    html = _normalize_asset_urls(html)
    return _apply_live_base_path(html, base_path)


def _spec_error_card(slug: str, err: Exception) -> str:
    """Readable HTML error card for a study whose spec fails to load.

    Without this, an ``InvestigationSpecError`` propagated out of the route and
    the browser got an empty response ("localhost didn't send any data") — the
    reviewer had no idea which field was wrong.
    """
    import html as _html

    slug_e = _html.escape(slug)
    msg_e = _html.escape(str(err))
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Study spec error — {slug_e}</title>"
        "<div style='max-width:52rem;margin:3rem auto;padding:0 1rem;"
        "font-family:system-ui,-apple-system,sans-serif;line-height:1.5;color:#1a1a1a'>"
        f"<h1 style='color:#b3261e'>Study &ldquo;{slug_e}&rdquo; failed to load</h1>"
        "<p>Its <code>study.yaml</code> is structurally invalid, so the detail "
        "page can&rsquo;t be rendered. Fix the field named below and reload.</p>"
        "<pre style='background:#f6f6f6;border:1px solid #ddd;border-radius:6px;"
        f"padding:1rem;white-space:pre-wrap'>{msg_e}</pre>"
        "<p style='color:#666'>The same validation runs in <code>/viva-report</code>&rsquo;s "
        "render-guarantee lint (the <code>render_blocked</code> finding).</p>"
        "</div>"
    )


def build_study_detail_page(ws_root: Path, slug: str, *, base_path: str = "") -> tuple[str, int]:
    """Full study-detail page builder: validate → load spec → render.

    Returns ``(html, status_code)`` where status_code is 200 on success, 404
    for an invalid/unknown slug, or 400 for a study whose ``study.yaml`` is
    structurally invalid (an error card naming the offending field, instead of
    letting ``InvestigationSpecError`` propagate to an empty HTTP response).
    The 404 bodies are byte-identical to the legacy handler's ``_send_html``
    responses.

    ``base_path``: forwarded to ``render_study_detail_html`` for subpath
    hosting; see its docstring.
    """
    from vivarium_workbench.lib.investigations import InvestigationSpecError
    from vivarium_workbench.lib.study_spec import load_study_detail_spec

    if not _SLUG_RE.match(slug):
        return "<h1>Not found</h1>", 404
    try:
        spec: Optional[dict] = load_study_detail_spec(ws_root, slug)
    except InvestigationSpecError as e:
        return _spec_error_card(slug, e), 400
    if spec is None:
        return (
            f"<h1>Study not found</h1><p><code>{slug}</code> does not exist.</p>",
            404,
        )
    html = render_study_detail_html(ws_root, slug, spec, base_path=base_path)
    return html, 200
