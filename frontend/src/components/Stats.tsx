import type { State } from "../types";
import { eur, num, pct, tone } from "../format";
import { Metric } from "./ui";

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <p className="mt-2 text-[13px] text-muted">Verlauf ab zwei Trades</p>;
  const w = 160, h = 44, min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 4 - ((v - min) / span) * (h - 8)}`).join(" ");
  const up = values[values.length - 1] >= 0;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={`mt-2 h-11 w-full ${up ? "text-win" : "text-loss"}`} preserveAspectRatio="none" aria-label="Kumulierter Gewinn">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export default function Stats({ s }: { s: State }) {
  const k = s.stats;
  return (
    <section className="card p-6 sm:p-8">
      <h2 className="h2">Statistik</h2>
      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-3 lg:grid-cols-5">
        <Metric label="Gewinn / Verlust" value={eur(k.total_pnl, true)} className={tone(k.total_pnl)} />
        <Metric label="Gesamtrendite" value={pct(k.total_return_pct, 2, true)} className={tone(k.total_return_pct)} />
        <Metric label="Trades" value={String(k.trades)} />
        <Metric label="Gewinntrades" value={String(k.wins)} />
        <Metric label="Verlusttrades" value={String(k.losses)} />
        <Metric label="Winrate" value={k.trades ? pct(k.winrate, 0) : "–"} />
        <Metric label="Ø Gewinn" value={k.wins ? eur(k.avg_win) : "–"} />
        <Metric label="Ø Verlust" value={k.losses ? eur(k.avg_loss) : "–"} />
        <Metric label="Profit Factor" value={k.profit_factor == null ? (k.wins ? "∞" : "–") : num(k.profit_factor)} />
        <div className="col-span-2 sm:col-span-1">
          <dt className="label">Verlauf</dt>
          <dd><Sparkline values={s.curve} /></dd>
        </div>
      </dl>
    </section>
  );
}
