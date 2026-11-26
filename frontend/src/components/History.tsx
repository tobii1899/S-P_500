import { useState } from "react";
import type { State } from "../types";
import { dateVie, eur, num, tone } from "../format";
import { Pill } from "./ui";

export default function History({ s }: { s: State }) {
  const [all, setAll] = useState(false);
  const rows = all ? s.history : s.history.slice(0, 10);
  return (
    <section className="card p-6 sm:p-8">
      <h2 className="h2">Handelshistorie</h2>
      {s.history.length === 0 ? (
        <p className="mt-4 text-[15px] text-muted">Noch keine Handelstage gespeichert.</p>
      ) : (
        <div className="-mx-2 mt-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-[14px]">
            <thead>
              <tr className="label border-b border-line">
                {["Datum", "Signal", "Einstieg", "Ausstieg", "Ergebnis", "Status"].map((h, i) => (
                  <th key={h} className={`px-2 py-2.5 font-normal ${i >= 2 && i <= 4 ? "text-right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.date} className="border-b border-line/60 last:border-0" title={r.reason ?? undefined}>
                  <td className="px-2 py-3">{dateVie(r.date)}</td>
                  <td className={`px-2 py-3 font-medium ${r.side === "LONG" ? "text-win" : r.side === "SHORT" ? "text-loss" : "text-muted"}`}>{r.side ?? "–"}</td>
                  <td className="px-2 py-3 text-right">{num(r.entry)}</td>
                  <td className="px-2 py-3 text-right">{num(r.exit)}</td>
                  <td className={`px-2 py-3 text-right font-medium ${tone(r.pnl_eur)}`}>{r.pnl_eur == null ? "0,00 €" : eur(r.pnl_eur, true)}</td>
                  <td className="px-2 py-3"><Pill status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {s.history.length > 10 && (
        <button className="btn mt-4" onClick={() => setAll(!all)}>{all ? "Weniger anzeigen" : `Alle ${s.history.length} Tage anzeigen`}</button>
      )}
    </section>
  );
}
