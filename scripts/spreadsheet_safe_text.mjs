// Match stage6e's string-cell policy; numbers/dates keep their types.
export function spreadsheetSafeValue(value) {
  if (typeof value !== 'string') return value;
  const clean = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, '');
  return /^[=+@\-\t\r]/.test(clean) ? "'" + clean : clean;
}
