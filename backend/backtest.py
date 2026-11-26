"""Backtest: dieselben Regeln wie live, auf historischen Kerzen, ohne Look-ahead.

- Signal: abgeschlossene 5-Min-Kerze (Indikatoren nutzen nur Daten bis zu dieser Kerze).
- Einstieg: Open der ersten 1-Min-Kerze nach Kerzenschluss, plus Slippage gegen den Trade.
- Stop/Ziel: Stop = Band zum Signalzeitpunkt, Ziel 2:1 ab Einstiegskurs. Verlauf nach dem Einstieg
  wird auf 1-Min-Kerzen geprüft; berührt eine Kerze beides, gilt der Stop (konservativ).
- Zwangsschluss 5 Minuten vor Börsenschluss, Slippage und Gebühren eingerechnet.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta

import pandas as pd

from .config import Settings
from .marketdata import filter_rth
from .risk import size_position
from .simulator import simulate_exit, trade_pnl
from .stats import compute_stats, max_drawdown_pct
from .strategy import StrategyParams, compute_indicators, evaluate_bar, plan_trade
from .timeutil import BAR_MIN, NY, UTC


def run_backtest(data, broker, s: Settings, start: date, end: date, start_balance: float | None = None,
                 now: datetime | None = None) -> dict:
    params = StrategyParams(s.sma_len, s.bb_len, s.bb_std, s.rr)
    sessions = list(broker.sessions(start, end))
    if not sessions:
        raise ValueError("Im gewählten Zeitraum gibt es keine Handelstage.")
    stop_dt = min(datetime.combine(end + timedelta(days=1), time(0), tzinfo=UTC), now or datetime.now(UTC))
    m5 = filter_rth(data.bars(datetime.combine(start - timedelta(days=14), time(0), tzinfo=UTC), stop_dt, 5))
    m1 = filter_rth(data.bars(datetime.combine(start - timedelta(days=1), time(0), tzinfo=UTC), stop_dt, 1))
    if m5.empty or m1.empty:
        raise ValueError("Für den Zeitraum wurden keine Kursdaten geliefert.")
    ind = compute_indicators(m5, params)
    day_of = pd.Series(ind.index.tz_convert(NY).date, index=ind.index)

    balance = float(start_balance or s.start_balance)
    initial = balance
    curve = [{"date": sessions[0].day.isoformat(), "equity": balance}]
    trades: list[dict] = []
    for sess in sessions:
        rows = ind[day_of == sess.day]
        if rows.empty:
            continue
        open_ts, close_ts = pd.Timestamp(sess.open), pd.Timestamp(sess.close)
        entry_end = pd.Timestamp(sess.entry_end(s.window_minutes))
        force_at = sess.force_close_at(s.force_close_min)
        m1_day = m1[(m1.index >= open_ts) & (m1.index < close_ts)]
        for ts, row in rows.iterrows():
            be = ts + pd.Timedelta(minutes=BAR_MIN)
            if be >= entry_end:
                break  # ab 11:00 ET (17:00 Wien) keine neuen Trades
            sig = evaluate_bar(row)
            if not sig.side:
                continue
            pos = m1.index.searchsorted(be)
            if pos >= len(m1) or m1.index[pos] - be > pd.Timedelta(minutes=2):
                continue  # Datenlücke -> kein Einstieg
            ets = m1.index[pos]
            ref = float(m1["open"].iloc[pos])
            entry = ref + s.slippage if sig.side == "LONG" else ref - s.slippage
            plan = plan_trade(sig.side, entry, sig.stop, s.rr)
            if plan is None:
                continue
            size = size_position(balance, s.risk_pct, plan, s.max_leverage, s.min_stop_distance)
            if not size.ok:
                continue
            res = simulate_exit(m1_day, sig.side, plan.entry, plan.stop, plan.take_profit, ets.to_pydatetime(),
                                force_at, s.slippage, now=None)
            if res.reason == "OPEN":
                continue
            pnl = trade_pnl(sig.side, plan.entry, res.price, size.qty, s.fee_per_share)
            balance += pnl
            trades.append({
                "date": sess.day.isoformat(), "side": sig.side, "bonus": bool(sig.bonus), "qty": size.qty,
                "entry_time": ets.isoformat(), "entry": plan.entry, "stop": plan.stop, "take_profit": plan.take_profit,
                "exit_time": res.time.isoformat(), "exit": res.price, "reason": res.reason,
                "risk_usd": size.risk_usd, "pnl_usd": pnl,
            })
            curve.append({"date": sess.day.isoformat(), "equity": round(balance, 2)})
            break  # höchstens ein Trade pro Handelstag

    stats = compute_stats([t["pnl_usd"] for t in trades], initial)
    monthly: OrderedDict[str, dict] = OrderedDict()
    for t in trades:
        m = monthly.setdefault(t["date"][:7], {"month": t["date"][:7], "pnl_usd": 0.0, "trades": 0})
        m["pnl_usd"] = round(m["pnl_usd"] + t["pnl_usd"], 2)
        m["trades"] += 1
    for m in monthly.values():
        m["return_pct"] = round(m["pnl_usd"] / initial * 100.0, 2)
    return {
        "period": {"start": sessions[0].day.isoformat(), "end": sessions[-1].day.isoformat(), "trading_days": len(sessions)},
        "settings": {
            "risk_pct": s.risk_pct, "max_leverage": s.max_leverage, "slippage": s.slippage,
            "fee_per_share": s.fee_per_share, "start_balance": initial, "feed": s.data_feed,
        },
        "metrics": {
            **stats,
            "max_drawdown_pct": round(max_drawdown_pct([c["equity"] for c in curve]), 2),
            "no_trade_days": len(sessions) - len(trades),
            "end_balance": round(balance, 2),
        },
        "monthly": list(monthly.values()),
        "equity": curve,
        "trades": trades,
    }


def months_to_range(months: int, today: date | None = None) -> tuple[date, date]:
    end = (today or date.today()) - timedelta(days=1)
    return end - timedelta(days=round(months * 30.44)), end
