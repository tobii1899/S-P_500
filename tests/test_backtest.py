"""Backtest: Regeln wie live, ohne Look-ahead, mit Slippage; höchstens ein Trade pro Tag."""
from datetime import date, datetime

import pytest

from backend.backtest import months_to_range, run_backtest
from backend.config import Settings
from backend.timeutil import UTC
from tests.helpers import FakeBroker, FakeClock, FakeData, build_history, ny

SPECS = [
    (date(2026, 11, 9), "flat"), (date(2026, 11, 10), "flat"),          # Warm-up
    (date(2026, 11, 11), "up"), (date(2026, 11, 12), "up"), (date(2026, 11, 13), "flat"),
    (date(2026, 11, 16), "reverse"), (date(2026, 11, 17), "down"),
]


def run(**overrides):
    df, sessions = build_history(SPECS)
    clock = FakeClock(datetime(2026, 12, 1, tzinfo=UTC))
    data = FakeData(df, clock)
    broker = FakeBroker(data, clock, sessions)
    s = Settings(api_key="k", secret_key="s", **overrides)
    return run_backtest(data, broker, s, date(2026, 11, 11), date(2026, 11, 17), now=clock.now())


def test_backtest_counts_wins_losses_and_no_trade_days():
    r = run()
    m = r["metrics"]
    assert r["period"]["trading_days"] == 5
    assert (m["trades"], m["wins"], m["losses"]) == (4, 3, 1)
    assert m["no_trade_days"] == 1                                       # der Seitwärtstag
    assert [t["reason"] for t in r["trades"]] == ["TP", "TP", "SL", "TP"]
    assert [t["side"] for t in r["trades"]] == ["LONG", "LONG", "LONG", "SHORT"]
    assert m["winrate"] == 75.0
    assert m["profit_factor"] > 1 and m["max_drawdown_pct"] > 0
    assert m["total_pnl"] == pytest.approx(sum(t["pnl_usd"] for t in r["trades"]))
    assert m["end_balance"] == pytest.approx(100_000 + m["total_pnl"])
    assert len(r["monthly"]) == 1 and r["monthly"][0]["month"] == "2026-11" and r["monthly"][0]["trades"] == 4


def test_backtest_entry_is_after_signal_candle_close_and_at_most_one_per_day():
    r = run()
    days = [t["date"] for t in r["trades"]]
    assert len(days) == len(set(days))
    for t in r["trades"]:
        assert t["entry_time"].startswith(t["date"] + "T14:35")           # Open der Minute nach Schluss der 9:35-Kerze
        assert t["exit_time"] > t["entry_time"]


def test_backtest_applies_fees_and_slippage():
    base = run(slippage=0.0, fee_per_share=0.0)
    assert run(slippage=0.0, fee_per_share=0.05)["metrics"]["total_pnl"] < base["metrics"]["total_pnl"]   # Gebühren drücken das Ergebnis
    assert base["trades"][0]["entry"] == pytest.approx(680.25)                                           # Open der Folgeminute
    assert run(slippage=0.02)["trades"][0]["entry"] == pytest.approx(680.27)                             # Slippage gegen den LONG-Trade
    short = run(slippage=0.02)["trades"][-1]
    assert short["side"] == "SHORT" and short["entry"] < run(slippage=0.0)["trades"][-1]["entry"]       # beim SHORT: schlechterer = niedrigerer Preis


def test_backtest_sizing_uses_running_balance_and_risk_cap():
    r = run(risk_pct=0.5, max_leverage=2.0)
    for t in r["trades"]:
        assert t["risk_usd"] <= 100_000 * 0.005 * 1.02                      # nie mehr als das konfigurierte Risiko


def test_months_to_range():
    start, end = months_to_range(3, today=date(2026, 11, 24))
    assert end == date(2026, 11, 23) and 90 <= (end - start).days <= 93
