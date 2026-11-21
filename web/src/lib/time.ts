// Relative-time formatting for the status strip and stat cards.
// Extra file beyond the spec list: shared by StatusStrip and Overview and
// unit-tested; kept dependency-free.

export function timeAgo(iso: string | null, now: Date): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.floor((now.getTime() - Date.parse(iso)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function countdown(iso: string | null, now: Date): string {
  if (!iso) return "—";
  const seconds = Math.floor((Date.parse(iso) - now.getTime()) / 1000);
  if (seconds <= 0) return "due now";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  if (minutes > 0) return `in ${minutes}m`;
  return `in ${seconds}s`;
}

export function usd(value: string | number | null | undefined): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}
