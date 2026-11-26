import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { State } from "../types";
import { countdown, eur, num, timeVie, tone } from "../format";

function Cell({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className={`mt-1 text-[17px] font-medium ${className}`}>{value}</dd>
    </div>
  );
}

export default function TradePanel({ s, now }: { s: State; now: number }) {
  const t = s.trade;
  const open = t?.status === "OPEN";
  const left = s.market.force_close_at ? new Date(s.market.force_close_at).getTime() - now : null;
  const ev = s.evaluation;

  return (
    <section className="card p-6 sm:p-8">
      <header className="flex items-center justify-between">
        <h2 className="h2">{t ? (open ? "Offener Trade" : "Trade von heute") : "Kein Trade aktiv"}</h2>
        {t && (
          <span className={`inline-flex items-center gap-1 rounded-full bg-soft px-3 py-1 text-[13px] font-semibold ${t.side === "LONG" ? "text-win" : "text-loss"}`}>
            {t.side === "LONG" ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}
            {t.side}
          </span>
        )}
      </header>

      {t ? (
        <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-4">
          <Cell label="Einstieg" value={num(t.entry_price)} />
          <Cell label={open ? "Aktueller Kurs" : "Ausstieg"} value={num(t.current_price)} />
          <Cell label="Stop-Loss" value={num(t.stop_price)} className="text-loss" />
          <Cell label="Take-Profit" value={num(t.take_profit)} className="text-win" />
          <Cell label="Risiko" value={eur(t.risk_eur)} />
          <Cell label="Potenzieller Gewinn" value={eur(t.potential_eur)} />
          <Cell label={open ? "Aktuell" : "Ergebnis"} value={eur(t.pnl_eur, true)} className={tone(t.pnl_eur)} />
          <Cell label="Einstieg um" value={`${timeVie(t.entry_time_utc)} Uhr`} />
          {open && left != null && <Cell label="Bis zum Zwangsschluss" value={countdown(left)} />}
          {!open && t.exit_reason && <Cell label="Ausstieg durch" value={{ TP: "Take-Profit", SL: "Stop-Loss", FORCED: "Zwangsschluss", OVERNIGHT: "Über Nacht" }[t.exit_reason] ?? t.exit_reason} />}
          <Cell label="Stückzahl" value={`${t.qty} × SPY`} />
          {t.bonus != null && <Cell label="Bollinger-Bestätigung" value={t.bonus ? "Ja" : "Nein"} />}
        </dl>
      ) : (
        <div className="mt-5">
          <p className="label">Warum bisher kein Signal?</p>
          <p className="mt-1.5 text-[15px] leading-relaxed">{ev?.note ?? s.today.reason ?? "Noch keine Kerze ausgewertet."}</p>
          {ev && (
            <dl className="mt-5 grid grid-cols-3 gap-x-6 gap-y-4 sm:grid-cols-6">
              <Cell label="Kerze bis" value={`${timeVie(ev.bar_end_utc)} Uhr`} />
              <Cell label="Close" value={num(ev.close)} />
              <Cell label="SMA 9" value={num(ev.sma)} />
              <Cell label="VWAP" value={num(ev.vwap)} />
              <Cell label="BB oben" value={num(ev.bb_upper)} />
              <Cell label="BB unten" value={num(ev.bb_lower)} />
            </dl>
          )}
        </div>
      )}
    </section>
  );
}
