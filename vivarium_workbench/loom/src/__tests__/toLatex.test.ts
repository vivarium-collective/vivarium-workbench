import { describe, it, expect } from 'vitest';
import katex from 'katex';
import { toLatex } from '../mathText';

const renders = (tex: string) =>
  expect(() => katex.renderToString(tex, { throwOnError: true })).not.toThrow();

describe('toLatex', () => {
  it('merges Unicode sub + ASCII sub into one group', () => {
    expect(toLatex('nᵢ_deg')).toBe('n_{i,deg}');
    renders(toLatex('nᵢ_deg'));
  });
  it('keeps ASCII merge behavior', () => {
    expect(toLatex('n_deg_c')).toBe('n_{deg,c}');
    renders(toLatex('n_deg_c'));
  });
  it('merges adjacent Unicode subs', () => {
    expect(toLatex('aᵢⱼ')).toBe('a_{ij}');
  });
  it('renders mixed equations without error', () => {
    renders(toLatex('kcat_endo · fᵢ'));
    renders(toLatex('∑ⱼ νᵢⱼ_max H⁺^2'));
  });
  it('passes braced scripts through', () => {
    expect(toLatex('x_{ab}^{c}')).toBe('x_{ab}^{c}');
  });
  it('single-char and superscripts', () => {
    expect(toLatex('N_A H^+')).toBe('N_A H^+');
    expect(toLatex('x²^3')).toBe('x^{2,3}');
  });
});
