// Strip redundant fractional trailing zeros and any dangling decimal
// point from decimal strings/numbers ("0.004302990000000000" ->
// "0.00430299", "10.000000000000000000" -> "10", "0.000000" -> "0").
export function fmtQty(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const s = String(value);
  if (!s.includes(".")) return s;
  return s.replace(/\.(\d*?)0+$/, ".$1").replace(/\.$/, "");
}

// Same trim applied to every decimal literal inside a sentence
// (e.g. an Activity feed line built server-side).
export function fmtText(text: string): string {
  return text.replace(/-?\d+\.\d+/g, (m) => fmtQty(m));
}
