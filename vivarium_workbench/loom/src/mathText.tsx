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

/** Normalize a math string to LaTeX for KaTeX: Unicode sub/superscript runs →
 * `_{…}`/`^{…}`, Unicode operators → macros, ASCII `_x`/`^x` → braced groups
 * (consecutive `_a_b` merged to `_{a,b}` so KaTeX doesn't see an illegal double
 * subscript). Plain letters/words/brackets pass through unchanged. */
export function toLatex(s: string): string {
  if (!s) return s;
  let out = '';
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (isSub(c)) {
      let r = '';
      while (i < n && isSub(s[i])) { r += SUB[s[i]]; i++; }
      out += r.length === 1 ? `_${r}` : `_{${r}}`;
      continue;
    }
    if (isSup(c)) {
      let r = '';
      while (i < n && isSup(s[i])) { r += SUP[s[i]]; i++; }
      out += r.length === 1 ? `^${r}` : `^{${r}}`;
      continue;
    }
    if (Object.prototype.hasOwnProperty.call(SYM, c)) { out += SYM[c] + ' '; i++; continue; }
    if (c === '_') {
      i++;
      if (s[i] === '{') { out += '_'; continue; } // already braced — hand to KaTeX
      // Consume the subscript run PLUS any further `_`-joined segments so
      // `n_deg_c` → `_{deg,c}` (one subscript), never `_{deg}_c` (illegal).
      let r = '';
      while (i < n && /[A-Za-z0-9]/.test(s[i])) {
        r += s[i]; i++;
        if (s[i] === '_' && /[A-Za-z0-9]/.test(s[i + 1] ?? '')) { r += ','; i++; }
      }
      out += r ? (r.length === 1 ? `_${r}` : `_{${r}}`) : '_';
      continue;
    }
    if (c === '^') {
      i++;
      if (s[i] === '{') { out += '^'; continue; }
      let r = '';
      while (i < n && /[A-Za-z0-9+\-]/.test(s[i])) { r += s[i]; i++; }
      out += r ? (r.length === 1 ? `^${r}` : `^{${r}}`) : '^';
      continue;
    }
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
