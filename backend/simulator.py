"""Simulation von Stop-Loss/Take-Profit auf 1-Minuten-Kerzen (Analysemodus und Backtest).

Berührt eine einzelne 1-Minuten-Kerze sowohl Stop als auch Ziel, gilt konservativ der Stop.
Nichts nach dem Einstieg wird vor dem Einstieg verwendet (kein Look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class ExitResult:
    reason: str                       # TP | SL | FORCED | OPEN
    time: datetime | None
    price: float | None
    slip_per_share: float = 0.0
    last_price: float | None = None


def simulate_exit(
    bars: pd.DataFrame,
    side: str,
    entry: float,
    stop: float,
    tp: float,
    start: datetime,
    force_at: datetime | None,
    slippage: float = 0.0,
    now: datetime | None = None,
) -> ExitResult:
    long = side == "LONG"
    t0 = pd.Timestamp(start)
    t_force = pd.Timestamp(force_at) if force_at is not None else None
    sub = bars[bars.index >= t0]
    last_close: float | None = None
    for ts, o, h, lo, c in zip(sub.index, sub["open"], sub["high"], sub["low"], sub["close"]):
        if t_force is not None and ts >= t_force:
            break
        last_close = float(c)
        sl_hit = lo <= stop if long else h >= stop
        tp_hit = h >= tp if long else lo <= tp
        if sl_hit:  # auch wenn dieselbe Kerze das Ziel berührt: Stop zuerst (konservativ)
            raw = min(stop, o) if long else max(stop, o)  # Gap durch den Stop -> schlechterer Fill
            px = raw - slippage if long else raw + slippage
            return ExitResult("SL", ts.to_pydatetime(), round(float(px), 4), slippage, last_close)
        if tp_hit:  # Limit-Order: Fill zum Zielkurs, keine Slippage
            return ExitResult("TP", ts.to_pydatetime(), float(tp), 0.0, last_close)
    if t_force is not None and (now is None or pd.Timestamp(now) >= t_force):
        at = sub[sub.index >= t_force]
        if len(at):
            raw, ts = float(at["open"].iloc[0]), at.index[0]
        elif last_close is not None:
            raw, ts = last_close, t_force
        else:
            return ExitResult("OPEN", None, None, 0.0, None)
        px = raw - slippage if long else raw + slippage
        return ExitResult("FORCED", ts.to_pydatetime(), round(float(px), 4), slippage, last_close)
    return ExitResult("OPEN", None, None, 0.0, last_close)


def trade_pnl(side: str, entry: float, exit_price: float, qty: int, fee_per_share: float = 0.0) -> float:
    direction = 1 if side == "LONG" else -1
    return round((exit_price - entry) * qty * direction - fee_per_share * qty * 2, 2)
