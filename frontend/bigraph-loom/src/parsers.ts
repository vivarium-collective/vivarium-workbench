// src/parsers.ts — pure parsers for Configure tab parameter inputs.

/**
 * Parse a textarea-form `list[string]` parameter: one item per line, trimmed,
 * blanks dropped. The inverse of {@link formatListString}.
 */
export function parseListString(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/**
 * Format a `list[string]` value for a textarea: one item per line, no
 * trailing newline.
 */
export function formatListString(items: string[]): string {
  return items.join('\n');
}
