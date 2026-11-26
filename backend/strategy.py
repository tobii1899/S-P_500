"""Die Strategie: reine Funktionen, kein Broker, keine Uhr, kein Zufall.

LONG : Close > SMA9 und Close > Session-VWAP  (Bonus: Close > oberes BB)  Stop = unteres BB
SHORT: Close < SMA9 und Close < Session-VWAP  (Bonus: Close < unteres BB) Stop = oberes BB
Take-Profit = Einstieg +/- rr * Stop-Abstand (2:1).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .timeutil import NY

EPS = 1e-9  # Gleichstand bleibt "kein Signal" - Rundungsstaub von Gleitkommazahlen zählt nicht als Über-/Unterschreitung


@dataclass(frozen=True)
class StrategyParams:
    sma_len: int = 9
    bb_len: int = 20
    bb_std: float = 2.0
    rr: float = 2.0


def compute_indicators(bars: pd.DataFrame, p: StrategyParams = StrategyParams()) -> pd.DataFrame:
    """bars: 5-Min-Kerzen (nur reguläre Handelszeit), Index = Kerzenbeginn in UTC.

    SMA/Bollinger laufen wie im Chart durchgehend über die Vortage (Warm-up), die
    Standardabweichung ist wie bei TradingView die Populations-Abweichung (ddof=0).
    Der VWAP startet jeden Handelstag neu (Session-VWAP, Typical Price = (H+L+C)/3).
    Jede Kennzahl einer Kerze nutzt ausschließlich Daten bis einschließlich dieser Kerze.
    """
    df = bars.copy()
    close = df["close"]
    df["sma"] = close.rolling(p.sma_len).mean()
    mid = close.rolling(p.bb_len).mean()
    sd = close.rolling(p.bb_len).std(ddof=0)
    df["bb_mid"] = mid
    df["bb_upper"] = mid + p.bb_std * sd
    df["bb_lower"] = mid - p.bb_std * sd
    day = pd.Series(df.index.tz_convert(NY).date, index=df.index)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_pv = (typical * df["volume"]).groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    df["vwap"] = cum_pv / cum_v.where(cum_v > 0)
    return df


@dataclass(frozen=True)
class Signal:
    side: str | None      # "LONG" | "SHORT" | None
    bonus: bool           # Kerze schließt zusätzlich außerhalb des äußeren Bollinger-Bands
    stop: float | None
    reason: str


def _rel(a: float, b: float) -> str:
    return "darüber" if a > b else "darunter" if a < b else "gleich"


def evaluate_bar(row) -> Signal:
    """Bewertet genau eine abgeschlossene Kerze. Strikte Vergleiche: Gleichstand = kein Signal."""
    keys = ("close", "sma", "vwap", "bb_upper", "bb_lower")
    if any(pd.isna(row[k]) for k in keys):
        return Signal(None, False, None, "Indikatoren noch nicht vollständig verfügbar")
    c, sma, vwap = float(row["close"]), float(row["sma"]), float(row["vwap"])
    up, lo = float(row["bb_upper"]), float(row["bb_lower"])
    if c > sma + EPS and c > vwap + EPS:
        bonus = c > up
        txt = f"Close {c:.2f} über SMA9 {sma:.2f} und VWAP {vwap:.2f} -> LONG"
        return Signal("LONG", bonus, lo, txt + (" (zusätzlich über oberem Bollinger-Band)" if bonus else ""))
    if c < sma - EPS and c < vwap - EPS:
        bonus = c < lo
        txt = f"Close {c:.2f} unter SMA9 {sma:.2f} und VWAP {vwap:.2f} -> SHORT"
        return Signal("SHORT", bonus, up, txt + (" (zusätzlich unter unterem Bollinger-Band)" if bonus else ""))
    return Signal(
        None, False, None,
        f"Close {c:.2f}: {_rel(c, sma)} SMA9 {sma:.2f}, {_rel(c, vwap)} VWAP {vwap:.2f} "
        "-> beide Bedingungen nicht gleichzeitig erfüllt, kein Signal",
    )


@dataclass(frozen=True)
class TradePlan:
    side: str
    entry: float
    stop: float
    take_profit: float
    risk_per_share: float


def plan_trade(side: str, entry: float, stop: float, rr: float = 2.0) -> TradePlan | None:
    """Stop und Ziel auf Cent runden. None, wenn der Stop nicht sinnvoll hinter dem Einstieg liegt."""
    entry, stop = round(float(entry), 2), round(float(stop), 2)
    dist = entry - stop if side == "LONG" else stop - entry
    if not dist > 0:
        return None
    tp = entry + rr * dist if side == "LONG" else entry - rr * dist
    return TradePlan(side, entry, stop, round(tp, 2), round(dist, 2))
