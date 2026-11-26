import { useState } from "react";
import { LoaderCircle, Play } from "lucide-react";
import { runBacktest } from "../api";
import type { Backtest } from "../types";
import { eur, monthLabel, pct, num, tone } from "../format";
import { Metric } from "./ui";

export default function BacktestPanel() {
  const [months, setMonths] = useState(3);
  const [res, setRes] = useState<Backtest | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await runBacktest(months));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const m = res?.metrics;
  const fx = res?.fx ?? 1;
  const maxAbs = Math.max(1, ...(res?.monthly.map((x) => Math.abs(x.pnl_usd)) ?? [1]));

  return (
    <section className="card p-6 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="h2">Backtest</h2>
          <p className="label mt-0.5">Gleiche Regeln auf historischen Kerzen, inkl. Slippage und Gebühren</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="seg" role="group" aria-label="Zeitraum">
            {[3, 6, 12].map((n) => (
              <button key={n} data-on={months === n} onClick={() => setMonths(n)}>{n} Monate</button>
            ))}
          </div>
          <button className="btn btn-primary" onClick={run} disabled={busy}>
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {busy ? "Läuft …" : "Starten"}
          </button>
        </div>
      </div>

      {err && <p className="mt-5 rounded-xl bg-soft p-3 text-[14px] text-loss">{err}</p>}

      {m && res && (
        <>
          <p className="label mt-6">{res.period.start} bis {res.period.end} · {res.period.trading_days} Handelstage · Risiko {res.settings.risk_pct} % pro Trade</p>
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-4">
            <Metric label="Gesamtrendite" value={pct(m.total_return_pct, 2, true)} className={tone(m.total_return_pct)} />
            <Metric label="Winrate" value={pct(m.winrate, 0)} />
            <Metric label="Profit Factor" value={m.profit_factor == null ? "–" : num(m.profit_factor)} />
            <Metric label="Max. Drawdown" value={pct(-m.max_drawdown_pct, 2)} className="text-loss" />
            <Metric label="Trades" value={String(m.trades)} />
            <Metric label="Ø Gewinn" value={m.wins ? eur(m.avg_win * fx) : "–"} />
            <Metric label="Ø Verlust" value={m.losses ? eur(m.avg_loss * fx) : "–"} />
            <Metric label="Tage ohne Trade" value={String(m.no_trade_days)} />
          </dl>
          <h3 className="label mt-8">Performance pro Monat</h3>
          <ul className="mt-3 space-y-2.5">
            {res.monthly.map((x) => (
              <li key={x.month} className="grid grid-cols-[72px_1fr_110px] items-center gap-3 text-[14px]">
                <span className="text-muted">{monthLabel(x.month)}</span>
                <span className="h-2 overflow-hidden rounded-full bg-soft">
                  <span className={`block h-full rounded-full ${x.pnl_usd >= 0 ? "bg-win" : "bg-loss"}`} style={{ width: `${(Math.abs(x.pnl_usd) / maxAbs) * 100}%` }} />
                </span>
                <span className={`text-right font-medium ${tone(x.pnl_usd)}`}>{eur(x.pnl_usd * fx, true)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
