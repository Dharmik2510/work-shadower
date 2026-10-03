const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2629800],
    ["year", 31557600],
  ];
  let unit: Intl.RelativeTimeFormatUnit = "minute";
  let size = 60;
  for (const [u, s] of units) if (abs >= s) [unit, size] = [u, s];
  return rtf.format(Math.round(diff / size), unit);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`;
}

export function num(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  return new Intl.NumberFormat(undefined, { notation: v >= 100_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(v);
}

export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(v);
}

export function duration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

export function plural(n: number, one: string, many = one + "s"): string {
  return `${n} ${n === 1 ? one : many}`;
}
