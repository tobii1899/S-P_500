"""Kommandozeile:  python -m backend.cli <befehl>

  run-session   Einstiegssuche (läuft auf GitHub)           run-close   Zwangsschluss + Abrechnung (GitHub)
  check         Verbindung, Konto, Zeiten, Daten prüfen      backtest    Strategie auf Historie testen
  serve         lokales Dashboard starten                     sync        Bot-Zustand von GitHub holen
  status        aktuellen Zustand im Terminal zeigen
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from .backtest import months_to_range, run_backtest
from .broker import AlpacaBroker
from .config import Settings, load_settings
from .db import Store, utcnow_iso
from .engine import Engine
from .export import build_state, job_summary, write_status
from .fx import get_usd_eur
from .marketdata import AlpacaData, closed_only, filter_rth
from .state_sync import pull_state
from .timeutil import UTC, fmt_vie, ny_date


def _setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Umlaute auch in der Windows-Konsole
        except Exception:
            pass
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)-7s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _settings(args) -> Settings:
    s = load_settings()
    if getattr(args, "state_dir", None):
        s = replace(s, state_dir=Path(args.state_dir))
    return s


def _need_keys(s: Settings) -> None:
    if not s.has_keys:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY fehlen (lokal in .env, auf GitHub als Secrets).")


# ------------------------------------------------------------------ Läufe
def cmd_run(args) -> int:
    s = _settings(args)
    _need_keys(s)
    store = Store(s.state_dir / "trading.db")
    engine = Engine(s, store, AlpacaData(s), AlpacaBroker(s))
    outcome, failure = "fehler", None
    try:
        outcome = engine.run_session() if args.cmd == "run-session" else engine.run_close()
        logging.getLogger("bot").info("Lauf beendet: %s", outcome)
    except BaseException as exc:  # auch Abbruch/Timeout soll im Protokoll landen
        failure = exc
        store.event("error", "cli", f"{args.cmd} abgebrochen: {type(exc).__name__}: {exc}")
        logging.getLogger("bot").exception("Lauf abgebrochen")
    finally:
        store.meta_set("last_run_utc", utcnow_iso())
        store.meta_set("last_outcome", outcome)
        now, fx = engine.clock.now(), get_usd_eur(s)  # dieselbe Uhr wie die Engine
        write_status(store, s, s.state_dir / "status.json", now, fx)
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(job_summary(store, s, outcome, now, fx))
        store.close()
    return 1 if failure else 0


# ------------------------------------------------------------------ Prüfung
def cmd_check(args) -> int:
    s = _settings(args)
    ok = True

    def line(flag: bool | None, text: str) -> None:
        print(("[OK]     " if flag else "[FEHLER] " if flag is False else "[INFO]   ") + text)

    print(f"Modus {s.mode} | Symbol {s.symbol} | Feed {s.data_feed} | Risiko {s.risk_pct} % pro Trade | max. Hebel {s.max_leverage}x")
    if not s.has_keys:
        line(False, "Keine Alpaca-Keys gefunden (ALPACA_API_KEY / ALPACA_SECRET_KEY)")
        return 1
    line(True, f"Keys vorhanden (Key-ID beginnt mit {s.api_key[:4]}…, Secret wird nie angezeigt)")
    broker, data = AlpacaBroker(s), AlpacaData(s)
    now = datetime.now(UTC)
    try:
        a = broker.account()
        good = a["status"] == "ACTIVE" and not a["trading_blocked"] and not a["account_blocked"]
        line(good, f"Konto {a['status']} | Eigenkapital {a['equity']:,.2f} {a['currency']} | Short erlaubt: {a['shorting_enabled']} | Handel gesperrt: {a['trading_blocked']}")
        ok &= good
    except Exception as exc:
        line(False, f"Konto nicht abrufbar - stimmen Key und Secret? ({exc})")
        return 1
    try:
        f = broker.asset_flags()
        line(f["tradable"], f"{s.symbol} ist handelbar: {f['tradable']}")
        ok &= f["tradable"]
        short_ok = f["shortable"] and f["easy_to_borrow"] and a["shorting_enabled"]
        line(short_ok, f"Leerverkäufe (SHORT) möglich: {short_ok}" + ("" if short_ok else " - SHORT-Signale würden übersprungen, LONG läuft normal"))
    except Exception as exc:
        line(False, f"Asset {s.symbol} nicht abrufbar: {exc}")
        ok = False
    try:
        sessions = broker.sessions(ny_date(now), ny_date(now) + timedelta(days=10))[:4]
        line(bool(sessions), "Nächste Handelstage (Wiener Zeit):")
        for x in sessions:
            note = "  <- verkürzter Handelstag" if (x.close - x.open) < timedelta(hours=6, minutes=30) else ""
            print(f"           {x.day:%a %d.%m.%Y}: Open {fmt_vie(x.open)} | Einstiegsfenster bis {fmt_vie(x.entry_end(s.window_minutes))} "
                  f"| Zwangsschluss {fmt_vie(x.force_close_at(s.force_close_min))} | Börsenschluss {fmt_vie(x.close)}{note}")
    except Exception as exc:
        line(False, f"Börsenkalender nicht abrufbar: {exc}")
        ok = False
    try:
        bars = closed_only(filter_rth(data.bars(now - timedelta(days=6), now, 5)), now, 5)
        line(len(bars) >= 30, f"Marktdaten ({s.data_feed}): {len(bars)} abgeschlossene 5-Min-Kerzen der letzten Tage, letzte Kerze {bars.index[-1]:%d.%m. %H:%M} UTC" if len(bars) else "keine Kerzen erhalten")
        ok &= len(bars) >= 30
        line(True, f"Letzter Kurs {data.latest_price():.2f}")
    except Exception as exc:
        line(False, f"Marktdaten nicht abrufbar: {exc}  (Feed 'sip' braucht ein Alpaca-Abo, 'iex' ist gratis)")
        ok = False
    line(None, f"USD->EUR: {get_usd_eur(s):.4f}")
    Store(s.state_dir / "trading.db").close()
    line(True, "Datenbank kann angelegt werden")
    print("\nAlles bereit." if ok else "\nEs gibt Probleme - bitte oben die [FEHLER]-Zeilen beheben.")
    return 0 if ok else 1


# ------------------------------------------------------------------ Backtest
def cmd_backtest(args) -> int:
    s = _settings(args)
    _need_keys(s)
    start, end = months_to_range(args.months)
    print(f"Backtest {start} bis {end} | Risiko {s.risk_pct} % | Slippage {s.slippage} USD/Aktie | Feed {s.data_feed}")
    res = run_backtest(AlpacaData(s), AlpacaBroker(s), s, start, end)
    m = res["metrics"]
    pf = "-" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}"
    print(f"\nHandelstage {res['period']['trading_days']} | Trades {m['trades']} | ohne Trade {m['no_trade_days']}")
    print(f"Gesamtrendite {m['total_return_pct']:.2f} % | Winrate {m['winrate']:.1f} % | Profit Factor {pf} | Max. Drawdown {m['max_drawdown_pct']:.2f} %")
    print(f"Ø Gewinn {m['avg_win']:.2f} USD | Ø Verlust {m['avg_loss']:.2f} USD | Endkapital {m['end_balance']:.2f} USD")
    print("\nPerformance pro Monat:")
    for row in res["monthly"]:
        print(f"  {row['month']}: {row['pnl_usd']:>10.2f} USD  ({row['return_pct']:+.2f} %)  {row['trades']} Trades")
    out = s.state_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if res["trades"]:
        with open(out / "backtest_trades.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(res["trades"][0]))
            w.writeheader()
            w.writerows(res["trades"])
    print(f"\nDetails: {out / 'backtest.json'} und backtest_trades.csv")
    return 0


# ------------------------------------------------------------------ Sonstiges
def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("backend.api:app", host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_sync(args) -> int:
    s = _settings(args)
    try:
        print(pull_state(s.state_dir))
        return 0
    except RuntimeError as exc:
        print(f"Sync nicht möglich: {exc}")
        return 1


def cmd_status(args) -> int:
    s = _settings(args)
    store = Store(s.state_dir / "trading.db")
    st = build_state(store, s, datetime.now(UTC), get_usd_eur(s))
    store.close()
    print(f"{st['today']['date']} {st['mode_label']}: {st['today']['status']} - {st['today']['reason']}")
    t, k = st["trade"], st["stats"]
    if t:
        print(f"Trade: {t['side']} {t['qty']} x Einstieg {t['entry_price']} Stop {t['stop_price']} Ziel {t['take_profit']} ({t['status']})")
    print(f"Gesamt: {k['trades']} Trades, Winrate {k['winrate']:.0f} %, P/L {k['total_pnl']:.2f} EUR")
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup()
    p = argparse.ArgumentParser(prog="backend.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run-session", "run-close", "check", "sync", "status"):
        sp = sub.add_parser(name)
        sp.add_argument("--state-dir", help="Ordner mit trading.db (Standard: data/)")
    bt = sub.add_parser("backtest")
    bt.add_argument("--months", type=int, default=3, choices=(1, 3, 6, 12))
    bt.add_argument("--state-dir")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    handlers = {"run-session": cmd_run, "run-close": cmd_run, "check": cmd_check, "backtest": cmd_backtest,
                "serve": cmd_serve, "sync": cmd_sync, "status": cmd_status}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
