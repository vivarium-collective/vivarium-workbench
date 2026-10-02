// src/mathText.tsx — render contract "formal text" (equations, symbol legends,
// prose) with correct sub/superscripts.
//
// Process contracts author math with a MIX of raw Unicode sub/superscripts
// (fᵢ, ∑ⱼ, H⁺) and ASCII underscore/caret (kcat_endo, N_A, H^+). Fed straight to
// KaTeX or dropped into sans-serif prose, both render "off": KaTeX prints a
// Unicode ᵢ as a literal glyph (not a real subscript), and prose fonts cover the
// Unicode sub/sup block unevenly (wrong size/baseline, sometimes tofu).
//
// This module normalizes both surfaces:
//   - toLatex()   — Unicode + _/^ + Unicode operators → LaTeX, for the KaTeX
//                   equation block.
//   - <MathText>  — Unicode + ^ (+ _ ONLY for symbol keys) → <sub>/<sup> React
//                   nodes, for prose + symbol legends. Never touches ASCII `_`
//                   in prose, so snake_case identifiers (rna_degradation_listener,
//                   cell_mass, TU_index) stay literal.
//
// Unicode sub/superscripts are unambiguously intended as such, so they convert
// everywhere. ASCII `_` is ambiguous (subscript vs snake_case), so it only
// subscripts inside a symbol KEY (a known short math ident), never in prose.

import { Fragment, type ReactNode } from 'react';

const SUB: Record<string, string> = {
  '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5', '₆': '6',
  '₇': '7', '₈': '8', '₉': '9', '₊': '+', '₋': '-', '₌': '=', '₍': '(', '₎': ')',
  'ₐ': 'a', 'ₑ': 'e', 'ₒ': 'o', 'ₓ': 'x', 'ₕ': 'h', 'ₖ': 'k', 'ₗ': 'l', 'ₘ': 'm',
  'ₙ': 'n', 'ₚ': 'p', 'ₛ': 's', 'ₜ': 't', 'ᵢ': 'i', 'ⱼ': 'j', 'ᵣ': 'r', 'ᵤ': 'u',
  'ᵥ': 'v', 'ₖ ': 'k',
};
const SUP: Record<string, string> = {
  '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7',
  '⁸': '8', '⁹': '9', '⁺': '+', '⁻': '-', '⁼': '=', '⁽': '(', '⁾': ')', 'ⁿ': 'n',
  'ⁱ': 'i',
};
// Unicode math operators/greek → LaTeX macros (KaTeX contexts only). Prose keeps
// the Unicode glyphs, which render fine in body fonts — only sub/sup are broken.
const SYM: Record<string, string> = {
  '∑': '\\sum', '∏': '\\prod', '∫': '\\int', '∂': '\\partial', '∇': '\\nabla',
  'Δ': '\\Delta', 'δ': '\\delta', 'ν': '\\nu', 'ρ': '\\rho', 'μ': '\\mu',
  'σ': '\\sigma', 'Σ': '\\Sigma', 'λ': '\\lambda', 'α': '\\alpha', 'β': '\\beta',
  'γ': '\\gamma', 'Γ': '\\Gamma', 'θ': '\\theta', 'π': '\\pi', 'φ': '\\phi',
  'ω': '\\omega', 'Ω': '\\Omega', 'ε': '\\varepsilon', 'τ': '\\tau', 'η': '\\eta',
  '·': '\\cdot', '×': '\\times', '÷': '\\div', '∝': '\\propto', '≤': '\\leq',
  '≥': '\\geq', '≠': '\\neq', '≈': '\\approx', '≡': '\\equiv', '→': '\\to',
  '←': '\\leftarrow', '↔': '\\leftrightarrow', '⇒': '\\Rightarrow', '∈': '\\in',
  '∉': '\\notin', '∞': '\\infty', '±': '\\pm', '∓': '\\mp', '√': '\\surd',
  '∀': '\\forall', '∃': '\\exists', '∅': '\\varnothing', '∩': '\\cap',
  '∪': '\\cup', '⊂': '\\subset', '⊆': '\\subseteq', '°': '^{\\circ}',
};

const isSub = (c: string) => Object.prototype.hasOwnProperty.call(SUB, c);
const isSup = (c: string) => Object.prototype.hasOwnProperty.call(SUP, c);

const ALNUM = /[A-Za-z0-9]/;

/** Read one subscript segment at `i`: a Unicode-sub run (`ᵢⱼ` → "ij") or an
 * ASCII `_word` (`_deg_c` → "deg,c"). Returns [text, next] or null. */
function readSub(s: string, i: number): [string, number] | null {
  if (isSub(s[i])) {
    let r = '';
    while (i < s.length && isSub(s[i])) { r += SUB[s[i]]; i++; }
    return [r, i];
  }
  if (s[i] === '_' && ALNUM.test(s[i + 1] ?? '')) {
    i++;
    let r = '';
    while (i < s.length && ALNUM.test(s[i])) {
      r += s[i]; i++;
      if (s[i] === '_' && ALNUM.test(s[i + 1] ?? '')) { r += ','; i++; }
    }
    return [r, i];
  }
  return null;
}

/** Superscript analogue of readSub: Unicode-sup run or ASCII `^x`. */
function readSup(s: string, i: number): [string, number] | null {
  if (isSup(s[i])) {
    let r = '';
    while (i < s.length && isSup(s[i])) { r += SUP[s[i]]; i++; }
    return [r, i];
  }
  if (s[i] === '^' && /[A-Za-z0-9+\-]/.test(s[i + 1] ?? '')) {
    i++;
    let r = '';
    while (i < s.length && /[A-Za-z0-9+\-]/.test(s[i])) { r += s[i]; i++; }
    return [r, i];
  }
  return null;
}

/** Consume ALL adjacent segments (Unicode and/or ASCII) into ONE group, so we
 * never emit two adjacent `_{…}` (illegal double subscript in KaTeX). */
function mergeScripts(
  s: string, i: number, mark: '_' | '^',
  read: (s: string, i: number) => [string, number] | null,
): [string, number] | null {
  const parts: string[] = [];
  let seg = read(s, i);
  while (seg) {
    parts.push(seg[0]);
    i = seg[1];
    seg = read(s, i);
  }
  if (!parts.length) return null;
  const r = parts.join(',');
  return [r.length === 1 ? `${mark}${r}` : `${mark}{${r}}`, i];
}

/** Normalize a math string to LaTeX for KaTeX: Unicode sub/superscript runs →
 * `_{…}`/`^{…}`, Unicode operators → macros, ASCII `_x`/`^x` → braced groups.
 * Adjacent sub (or sup) segments, whether Unicode or ASCII, are merged into a
 * single comma-joined group (`nᵢ_deg` → `n_{i,deg}`, `n_deg_c` → `n_{deg,c}`,
 * `aᵢⱼ` → `a_{ij}`) so KaTeX never sees an illegal double subscript. Plain
 * letters/words/brackets pass through unchanged. */
export function toLatex(s: string): string {
  if (!s) return s;
  let out = '';
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    const sub = mergeScripts(s, i, '_', readSub);
    if (sub) { out += sub[0]; i = sub[1]; continue; }
    const sup = mergeScripts(s, i, '^', readSup);
    if (sup) { out += sup[0]; i = sup[1]; continue; }
    if (Object.prototype.hasOwnProperty.call(SYM, c)) { out += SYM[c] + ' '; i++; continue; }
    // Bare `_`/`^` (already-braced `_{…}`, or no alnum follows): pass through.
    out += c; i++;
  }
  return out;
}

/** Render a run of ASCII word chars as a subscript (symbol-key context), keeping
 * `_`-joined segments visible (n_deg_c → deg_c small). */
function subKeyChars(s: string, start: number): [ReactNode, number] {
  let i = start + 1;
  if (i >= s.length || !/[A-Za-z0-9]/.test(s[i])) return ['_', start + 1];
  let r = '';
  while (i < s.length && /[A-Za-z0-9_]/.test(s[i]) && !(s[i] === '_' && !/[A-Za-z0-9]/.test(s[i + 1] ?? ''))) {
    r += s[i]; i++;
  }
  return [<sub>{r}</sub>, i];
}

/**
 * Render contract prose / symbol legends with correct sub/superscripts, as React
 * nodes (no dangerouslySetInnerHTML). Unicode sub/superscript runs and ASCII
 * `^…` become <sub>/<sup> everywhere; ASCII `_…` becomes a subscript ONLY when
 * `symbolKey` is set (so snake_case in prose is left alone). Unicode operators
 * (∑, Δ, ·, →, ≤, …) are left as-is — body fonts render them fine.
 */
export function MathText({ text, symbolKey = false }: { text: string; symbolKey?: boolean }): ReactNode {
  if (!text) return text ?? null;
  const parts: ReactNode[] = [];
  let buf = '';
  let i = 0;
  const n = text.length;
  const flush = () => { if (buf) { parts.push(buf); buf = ''; } };
  while (i < n) {
    const c = text[i];
    if (isSub(c)) {
      flush();
      let r = '';
      while (i < n && isSub(text[i])) { r += SUB[text[i]]; i++; }
      parts.push(<sub key={parts.length}>{r}</sub>);
      continue;
    }
    if (isSup(c)) {
      flush();
      let r = '';
      while (i < n && isSup(text[i])) { r += SUP[text[i]]; i++; }
      parts.push(<sup key={parts.length}>{r}</sup>);
      continue;
    }
    if (c === '^' && /[A-Za-z0-9+\-(]/.test(text[i + 1] ?? '')) {
      flush(); i++;
      let r = '';
      while (i < n && /[A-Za-z0-9+\-]/.test(text[i])) { r += text[i]; i++; }
      parts.push(<sup key={parts.length}>{r}</sup>);
      continue;
    }
    if (symbolKey && c === '_' && /[A-Za-z0-9]/.test(text[i + 1] ?? '')) {
      flush();
      const [node, next] = subKeyChars(text, i);
      parts.push(<Fragment key={parts.length}>{node}</Fragment>);
      i = next;
      continue;
    }
    buf += c; i++;
  }
  flush();
  return <>{parts}</>;
}
