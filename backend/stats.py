"""Kennzahlen: einmal definiert, von Dashboard und Backtest gemeinsam genutzt."""
from __future__ import annotations


def compute_stats(pnls: list[float], start_equity: float | None = None) -> dict:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    gross_win, gross_loss = sum(wins), -sum(losses)
    total = sum(pnls)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "flat": n - len(wins) - len(losses),
        "winrate": (len(wins) / n * 100.0) if n else 0.0,
        "total_pnl": total,
        "total_return_pct": (total / start_equity * 100.0) if start_equity else None,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def max_drawdown_pct(equity_curve: list[float]) -> float:
    peak, worst = None, 0.0
    for v in equity_curve:
        peak = v if peak is None or v > peak else peak
        if peak and peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return worst
