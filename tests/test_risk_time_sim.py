"""Risiko, Zeitlogik (Sommer-/Winterzeit, verkürzte Tage) und Stop/Ziel-Simulation."""
from datetime import date, datetime

import pandas as pd
import pytest

from backend.risk import size_position
from backend.simulator import simulate_exit, trade_pnl
from backend.stats import compute_stats, max_drawdown_pct
from backend.strategy import plan_trade
from backend.timeutil import NY, UTC, default_session, floor_boundary, fmt_vie, session_from_calendar
from tests.helpers import ny


# ---------------------------------------------------------------- Risiko
def _plan(entry=680.26, stop=679.90):
    return plan_trade("LONG", entry, stop, 2.0)


def test_size_limited_by_risk():
    s = size_position(100_000, 0.5, plan_trade("LONG", 100.0, 90.0), max_leverage=10, min_stop_distance=0.1)
    assert s.ok and s.qty == 50 and s.risk_usd == 500.0 and s.potential_usd == 1000.0


def test_size_limited_by_leverage_cap():
    s = size_position(100_000, 0.5, _plan(), max_leverage=2.0, min_stop_distance=0.1)
    assert s.ok and s.qty == 294                      # 200.000 / 680,26, nicht 1388 aus dem Risiko
    assert s.risk_usd == pytest.approx(294 * 0.36)


@pytest.mark.parametrize("equity", [0, -5, float("nan"), None])
def test_no_size_without_valid_equity(equity):
    assert not size_position(equity, 0.5, _plan(), 2, 0.1).ok


def test_no_size_when_stop_too_tight_or_qty_below_one():
    assert not size_position(100_000, 0.5, plan_trade("LONG", 100.0, 99.95), 2, 0.10).ok
    assert not size_position(100, 0.5, plan_trade("LONG", 100.0, 90.0), 2, 0.10).ok


# ---------------------------------------------------------------- Zeit
def test_winter_times_vienna():
    s = default_session(date(2026, 11, 25))
    assert fmt_vie(s.open) == "15:30"
    assert fmt_vie(s.entry_end(90)) == "17:00"
    assert fmt_vie(s.force_close_at(5)) == "21:55"


def test_summer_times_vienna():
    s = default_session(date(2026, 7, 15))
    assert (fmt_vie(s.open), fmt_vie(s.entry_end(90)), fmt_vie(s.force_close_at(5))) == ("15:30", "17:00", "21:55")


def test_us_dst_but_eu_not_yet_shifts_vienna_clock():
    """Zwischen US- und EU-Zeitumstellung liegt der NY-Open real bei 14:30 Wiener Zeit, nicht bei 15:30."""
    s = default_session(date(2026, 3, 16))
    assert fmt_vie(s.open) == "14:30" and fmt_vie(s.force_close_at(5)) == "20:55"
    assert s.open.astimezone(UTC).hour == 13          # UTC-Zeit ist nicht fix (Winter: 14)


def test_early_close_day_from_calendar():
    s = session_from_calendar(date(2026, 11, 27), datetime(2026, 11, 27, 9, 30), datetime(2026, 11, 27, 13, 0))
    assert fmt_vie(s.close) == "19:00" and fmt_vie(s.force_close_at(5)) == "18:55"


def test_weekend_has_no_default_session():
    assert default_session(date(2026, 11, 28)) is None


def test_floor_boundary():
    t = datetime(2026, 11, 25, 14, 37, 59, tzinfo=UTC)
    assert floor_boundary(t) == datetime(2026, 11, 25, 14, 35, tzinfo=UTC)


# ---------------------------------------------------------------- Simulation
def _m1(rows, start=None):
    start = start or ny(date(2026, 11, 25), 9, 35)
    idx = pd.date_range(start, periods=len(rows), freq="1min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


FORCE = ny(date(2026, 11, 25), 15, 55)
START = ny(date(2026, 11, 25), 9, 35)


def test_take_profit_hit():
    bars = _m1([(100, 100.5, 99.8, 100.2), (100.2, 101.2, 100.1, 101.0)])
    r = simulate_exit(bars, "LONG", 100, 99, 101, START, FORCE)
    assert (r.reason, r.price) == ("TP", 101.0)


def test_stop_wins_when_one_bar_touches_both():
    bars = _m1([(100, 101.5, 98.5, 100)])
    r = simulate_exit(bars, "LONG", 100, 99, 101, START, FORCE, slippage=0.01)
    assert r.reason == "SL" and r.price == pytest.approx(98.99)


def test_stop_wins_for_short_too():
    bars = _m1([(100, 101.5, 98.5, 100)])
    r = simulate_exit(bars, "SHORT", 100, 101, 99, START, FORCE, slippage=0.01)
    assert r.reason == "SL" and r.price == pytest.approx(101.01)


def test_gap_through_stop_fills_at_open():
    bars = _m1([(98.0, 98.2, 97.5, 98.0)])
    r = simulate_exit(bars, "LONG", 100, 99, 102, START, FORCE)
    assert r.reason == "SL" and r.price == pytest.approx(98.0)


def test_bars_before_entry_are_ignored():
    bars = _m1([(100, 100.2, 90.0, 100), (100, 100.2, 99.9, 100)], start=ny(date(2026, 11, 25), 9, 33))
    r = simulate_exit(bars, "LONG", 100, 99, 105, START, FORCE, now=ny(date(2026, 11, 25), 12))
    assert r.reason == "OPEN"                          # die Kerze mit Tief 90 lag vor dem Einstieg


def test_forced_close_uses_open_of_force_minute():
    idx = [ny(date(2026, 11, 25), 15, 54), ny(date(2026, 11, 25), 15, 55)]
    bars = pd.DataFrame([(100, 100.1, 99.9, 100.0), (100.4, 100.5, 100.3, 100.4)], columns=["open", "high", "low", "close"],
                        index=pd.DatetimeIndex(idx)).assign(volume=1.0)
    r = simulate_exit(bars, "LONG", 100, 99, 105, START, FORCE, slippage=0.01, now=FORCE)
    assert r.reason == "FORCED" and r.price == pytest.approx(100.39)


def test_open_while_before_forced_time():
    bars = _m1([(100, 100.1, 99.9, 100)])
    assert simulate_exit(bars, "LONG", 100, 99, 105, START, FORCE, now=ny(date(2026, 11, 25), 10)).reason == "OPEN"


def test_pnl_signs_and_fees():
    assert trade_pnl("LONG", 100, 102, 10) == 20.0
    assert trade_pnl("SHORT", 100, 98, 10) == 20.0
    assert trade_pnl("LONG", 100, 99, 10, fee_per_share=0.005) == -10.1


# ---------------------------------------------------------------- Kennzahlen
def test_stats():
    st = compute_stats([100, -50, 50, -25], 10_000)
    assert st["trades"] == 4 and st["wins"] == 2 and st["losses"] == 2
    assert st["winrate"] == 50.0 and st["total_pnl"] == 75 and st["total_return_pct"] == pytest.approx(0.75)
    assert st["avg_win"] == 75 and st["avg_loss"] == -37.5 and st["profit_factor"] == pytest.approx(2.0)
    assert compute_stats([10, 20])["profit_factor"] is None


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130, 117]) == pytest.approx(25.0)
