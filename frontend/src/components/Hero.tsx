import { Activity, Clock3, PlugZap, ShieldCheck, Wallet } from "lucide-react";
import type { State } from "../types";
import { dateVie, eur, num, timeVie } from "../format";
import { Fact, STATUS_LABEL, STATUS_TEXT } from "./ui";

export default function Hero({ s, now }: { s: State; now: number }) {
  const { today, market, broker, account } = s;
  return (
    <section className="card p-6 sm:p-8">
      <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="label">Heute · {dateVie(today.date)}</p>
          <h1 className={`mt-2 text-5xl font-semibold tracking-tight sm:text-6xl ${STATUS_TEXT[today.status]}`}>{STATUS_LABEL[today.status]}</h1>
          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-muted">{today.reason || "–"}</p>
        </div>
        <dl className="grid grid-cols-2 gap-x-10 gap-y-5 sm:grid-cols-3">
          <Fact icon={Clock3} label="Uhrzeit (Wien)" value={timeVie(now, true)} />
          <Fact icon={Activity} label="Markt" value={market.status === "open" ? "Geöffnet" : market.status === "pre" ? "Vor Handelsbeginn" : "Geschlossen"} sub={market.label} />
          <Fact icon={PlugZap} label="Broker" value={broker.connected ? "Verbunden" : "Keine Verbindung"} sub={broker.detail} />
          <Fact icon={ShieldCheck} label="Modus" value={s.mode_label} sub={s.mode === "live" ? "Echtes Geld" : s.mode === "paper" ? "Kein echtes Geld" : undefined} />
          <Fact icon={Wallet} label="Kontostand" value={eur(account.equity_eur)} sub={`${num(account.equity_usd)} USD`} />
        </dl>
      </div>
    </section>
  );
}
