const TZ = "Europe/Vienna";
const nf = (d: number, sign = false) =>
  new Intl.NumberFormat("de-AT", { minimumFractionDigits: d, maximumFractionDigits: d, signDisplay: sign ? "exceptZero" : "auto" });

export const num = (n: number | null | undefined, d = 2) => (n == null || Number.isNaN(n) ? "–" : nf(d).format(n));
export const eur = (n: number | null | undefined, sign = false) =>
  n == null || Number.isNaN(n) ? "–" : `${nf(2, sign).format(n)} €`;
export const pct = (n: number | null | undefined, d = 1, sign = false) =>
  n == null || Number.isNaN(n) ? "–" : `${nf(d, sign).format(n)} %`;

export const timeVie = (iso: string | number | null | undefined, seconds = false) =>
  iso == null
    ? "–"
    : new Date(iso).toLocaleTimeString("de-AT", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined });

export const dateVie = (ymd: string) => {
  const [y, m, d] = ymd.split("-");
  return `${d}.${m}.${y}`;
};

export const monthLabel = (ym: string) => {
  const [y, m] = ym.split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("de-AT", { month: "short", year: "numeric" });
};

export const countdown = (ms: number) => {
  if (ms <= 0) return "0:00:00";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

export const tone = (n: number | null | undefined) => (n == null || n === 0 ? "" : n > 0 ? "text-win" : "text-loss");
