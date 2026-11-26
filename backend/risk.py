"""Risikomanagement: Positionsgröße aus Risiko und Stop-Abstand, mit harten Obergrenzen."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .strategy import TradePlan


@dataclass(frozen=True)
class Sizing:
    ok: bool
    qty: int = 0
    risk_usd: float = 0.0
    potential_usd: float = 0.0
    notional: float = 0.0
    reason: str = ""


def size_position(equity: float, risk_pct: float, plan: TradePlan, max_leverage: float, min_stop_distance: float) -> Sizing:
    if equity is None or not math.isfinite(equity) or equity <= 0:
        return Sizing(False, reason="Kontostand nicht verfügbar")
    dist = plan.risk_per_share
    if dist < min_stop_distance:
        return Sizing(False, reason=f"Stop-Abstand {dist:.2f} USD kleiner als Minimum {min_stop_distance:.2f}")
    by_risk = math.floor(equity * risk_pct / 100.0 / dist)
    by_leverage = math.floor(equity * max_leverage / plan.entry)
    qty = min(by_risk, by_leverage)
    if qty < 1:
        return Sizing(False, reason="Positionsgröße unter 1 Aktie")
    return Sizing(
        True, qty,
        risk_usd=round(qty * dist, 2),
        potential_usd=round(qty * abs(plan.take_profit - plan.entry), 2),
        notional=round(qty * plan.entry, 2),
    )
