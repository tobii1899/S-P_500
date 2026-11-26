"""Aufbereitung des Zustands für Dashboard, status.json und die Zusammenfassung des GitHub-Laufs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Settings
from .db import Store, iso
from .stats import compute_stats
from .timeutil import NY, Session, default_session, fmt_vie, ny_date

MODE_LABEL = {"paper": "Paper Trading", "live": "Live Trading", "analysis": "Analysemodus"}


def _session_for_day(store: Store, d) -> Session | None:
    row = store.get_day(d.isoformat())
    if row and row.get("session_open_utc") and row.get("session_close_utc"):
        return Session(
            d,
            datetime.fromisoformat(row["session_open_utc"]).astimezone(NY),
            datetime.fromisoformat(row["session_close_utc"]).astimezone(NY),
        )
    return default_session(d)


def _today(day: dict | None, sess: Session | None, now: datetime) -> tuple[str, str]:
    if day:
        return day["status"], day.get("reason") or ""
    if sess is None:
        return "CLOSED", "Börse geschlossen (Wochenende/Feiertag)"
    if now < sess.open:
        return "PENDING", f"Handelsbeginn um {fmt_vie(sess.open)} Uhr (Wien)"
    if now < sess.close:
        return "PENDING", "Noch keine Daten vom Bot für heute - 'Von GitHub laden' drücken oder die Actions prüfen"
    return "CLOSED", "Keine Bot-Daten für diesen Tag"


def _market(sess: Session | None, now: datetime, s: Settings) -> dict:
    empty = {"open": None, "close": None, "entry_end": None, "force_close_at": None}
    if sess is None:
        return {"status": "closed", "label": "Börse geschlossen (Wochenende/Feiertag)", **empty}
    times = {
        "open": iso(sess.open), "close": iso(sess.close),
        "entry_end": iso(sess.entry_end(s.window_minutes)),
        "force_close_at": iso(sess.force_close_at(s.force_close_min)),
    }
    if now < sess.open:
        return {"status": "pre", "label": f"Handel beginnt um {fmt_vie(sess.open)} Uhr", **times}
    if now < sess.close:
        return {"status": "open", "label": f"Börse geöffnet bis {fmt_vie(sess.close)} Uhr", **times}
    return {"status": "closed", "label": "Börse geschlossen", **times}


def _trade_payload(t: dict | None, fx: float, live_price: float | None) -> dict | None:
    if not t:
        return None
    rate = t.get("fx_usd_eur") or fx
    direction = 1 if t["side"] == "LONG" else -1
    out = {k: t.get(k) for k in (
        "date", "mode", "side", "qty", "status", "entry_price", "stop_price", "take_profit",
        "entry_time_utc", "exit_time_utc", "exit_price", "exit_reason", "bonus",
    )}
    out["risk_eur"] = round((t.get("risk_usd") or 0.0) * rate, 2)
    out["potential_eur"] = round((t.get("potential_usd") or 0.0) * rate, 2)
    out["current_price"] = None
    if t["status"] == "CLOSED":
        out["pnl_eur"] = round((t.get("pnl_usd") or 0.0) * rate, 2)
        out["current_price"] = t.get("exit_price")
    elif live_price and t.get("entry_price"):
        out["current_price"] = live_price
        out["pnl_eur"] = round((live_price - t["entry_price"]) * t["qty"] * direction * rate, 2)
    else:
        out["pnl_eur"] = None
    return out


def build_state(store: Store, s: Settings, now: datetime, fx: float, live: dict | None = None) -> dict:
    d = ny_date(now)
    day_iso = d.isoformat()
    day = store.get_day(day_iso)
    sess = _session_for_day(store, d)
    status, reason = _today(day, sess, now)
    open_trades = store.open_trades()
    trade = store.get_trade(day_iso) or (open_trades[-1] if open_trades else None)
    live_price = live.get("price") if live else None
    mode = (day or {}).get("mode") or (trade or {}).get("mode") or s.mode

    closed = [t for t in store.closed_trades() if t.get("exit_reason") != "NOT_FILLED"]
    pnls = [(t.get("pnl_usd") or 0.0) * (t.get("fx_usd_eur") or fx) for t in closed]
    start_usd = float(store.meta_get("start_equity_usd") or s.start_balance)
    stats = compute_stats(pnls, start_usd * fx)
    curve, run = [], 0.0
    for p in pnls:
        run += p
        curve.append(round(run, 2))

    if live and live.get("account"):
        equity_usd, source = float(live["account"]["equity"]), "alpaca"
    else:
        last = store.meta_get("last_equity_usd")
        equity_usd = float(last) if last else s.start_balance + store.realized_pnl_usd()
        source = "bot"

    if live and live.get("broker_ok"):
        broker = {"connected": True, "detail": "Alpaca (soeben geprüft)"}
    elif mode == "analysis":
        broker = {"connected": False, "detail": "Analysemodus - kein Broker nötig"}
    else:
        ok = store.meta_get("last_broker_ok_utc")
        broker = {
            "connected": bool(ok),
            "detail": f"Letzter Bot-Lauf mit Broker: {fmt_vie(datetime.fromisoformat(ok))} Uhr" if ok else "Noch kein Bot-Lauf",
        }

    ev = store.last_signal(day_iso)
    evaluation = None
    if ev:
        evaluation = {k: ev.get(k) for k in ("bar_end_utc", "close", "sma", "vwap", "bb_upper", "bb_lower", "side", "note", "action")}

    history = []
    for r in store.history(60):
        rate = r.get("fx_usd_eur") or fx
        history.append({
            "date": r["date"], "status": r["status"], "side": r["side"], "entry": r["entry_price"], "exit": r["exit_price"],
            "pnl_eur": round(r["pnl_usd"] * rate, 2) if r["pnl_usd"] is not None else None,
            "reason": r["reason"], "exit_reason": r["exit_reason"],
        })

    return {
        "server_time": iso(now),
        "mode": mode,
        "mode_label": MODE_LABEL.get(mode, mode),
        "market": _market(sess, now, s),
        "broker": broker,
        "account": {"equity_usd": round(equity_usd, 2), "equity_eur": round(equity_usd * fx, 2), "fx": round(fx, 4), "source": source},
        "today": {"date": day_iso, "status": status, "reason": reason},
        "trade": _trade_payload(trade, fx, live_price),
        "evaluation": evaluation,
        "stats": stats,
        "curve": curve,
        "history": history,
        "events": store.recent_events(8),
        "live": bool(live and live.get("broker_ok")),
        "risk": {"risk_pct": s.risk_pct, "max_leverage": s.max_leverage},
    }


def write_status(store: Store, s: Settings, path: Path, now: datetime, fx: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_state(store, s, now, fx), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def job_summary(store: Store, s: Settings, outcome: str, now: datetime, fx: float) -> str:
    st = build_state(store, s, now, fx)
    t, stats = st["trade"], st["stats"]
    lines = [
        f"## SPY-Bot · {st['today']['date']} · {st['mode_label']}",
        "",
        f"**Lauf:** `{outcome}`  ",
        f"**Tagesstatus:** {st['today']['status']} - {st['today']['reason']}",
        "",
    ]
    if t:
        lines += [
            "| Seite | Stück | Einstieg | Stop-Loss | Take-Profit | Status | Ergebnis |",
            "|---|---|---|---|---|---|---|",
            f"| {t['side']} | {t['qty']} | {t['entry_price']} | {t['stop_price']} | {t['take_profit']} | {t['status']} "
            f"| {'' if t['pnl_eur'] is None else str(t['pnl_eur']) + ' €'} |",
            "",
        ]
    pf = "-" if stats["profit_factor"] is None else f"{stats['profit_factor']:.2f}"
    lines.append(f"**Gesamt:** {stats['trades']} Trades · Winrate {stats['winrate']:.0f} % · P/L {stats['total_pnl']:.2f} € · Profit Factor {pf}")
    errors = [e for e in st["events"] if e["level"] in ("error", "warn")]
    if errors:
        lines += ["", "**Letzte Warnungen:**"] + [f"- {e['ts_utc']} {e['message']}" for e in errors[:4]]
    return "\n".join(lines) + "\n"
