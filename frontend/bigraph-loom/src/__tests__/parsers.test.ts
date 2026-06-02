import { describe, it, expect } from 'vitest';
import { parseListString, formatListString } from '../parsers';

describe('parseListString', () => {
  it('splits one item per line, trims, drops blanks', () => {
    expect(parseListString('a\nb\n  c  \n\nd\n')).toEqual(['a', 'b', 'c', 'd']);
  });

  it('empty or whitespace-only input → empty array', () => {
    expect(parseListString('')).toEqual([]);
    expect(parseListString('   \n\n   ')).toEqual([]);
  });

  it('single value (no newline) parses', () => {
    expect(parseListString('BIOMD0000000001')).toEqual(['BIOMD0000000001']);
  });
});

describe('formatListString', () => {
  it('joins with newlines', () => {
    expect(formatListString(['a', 'b', 'c'])).toBe('a\nb\nc');
  });

  it('empty list → empty string', () => {
    expect(formatListString([])).toBe('');
  });
});
