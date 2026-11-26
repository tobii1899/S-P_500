"""Strategie: Indikatoren, LONG-, SHORT- und NO-TRADE-Bedingungen, Stop und 2:1-Ziel."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.strategy import StrategyParams, compute_indicators, evaluate_bar, plan_trade
from tests.helpers import build_history, ny


def row(**kw):
    base = dict(close=100.0, sma=99.0, vwap=99.0, bb_upper=101.0, bb_lower=97.0)
    base.update(kw)
    return pd.Series(base)


# ---------------------------------------------------------------- LONG
def test_long_when_close_above_sma_and_vwap():
    sig = evaluate_bar(row(close=100, sma=99, vwap=99.5))
    assert sig.side == "LONG"
    assert sig.stop == 97.0                      # Stop = unteres Bollinger-Band


def test_long_bonus_only_when_close_above_upper_band():
    assert evaluate_bar(row(close=101.5, bb_upper=101.0)).bonus is True
    assert evaluate_bar(row(close=100.5, bb_upper=101.0)).bonus is False


def test_long_does_not_require_bollinger_confirmation():
    sig = evaluate_bar(row(close=100, bb_upper=105))
    assert sig.side == "LONG" and sig.bonus is False


# ---------------------------------------------------------------- SHORT
def test_short_when_close_below_sma_and_vwap():
    sig = evaluate_bar(row(close=98, sma=99, vwap=99.5))
    assert sig.side == "SHORT"
    assert sig.stop == 101.0                     # Stop = oberes Bollinger-Band


def test_short_bonus_only_when_close_below_lower_band():
    assert evaluate_bar(row(close=96.5, bb_lower=97.0)).bonus is True
    assert evaluate_bar(row(close=98.0, bb_lower=97.0)).bonus is False


# ---------------------------------------------------------------- NO TRADE
@pytest.mark.parametrize("kw", [
    dict(close=100, sma=99, vwap=101),           # über SMA, unter VWAP
    dict(close=100, sma=101, vwap=99),           # unter SMA, über VWAP
    dict(close=100, sma=100, vwap=99),           # Gleichstand mit SMA
    dict(close=100, sma=99, vwap=100),           # Gleichstand mit VWAP
    dict(close=100, sma=100, vwap=100),          # alles gleich
])
def test_no_signal_when_conditions_not_both_met(kw):
    sig = evaluate_bar(row(**kw))
    assert sig.side is None and sig.stop is None


@pytest.mark.parametrize("missing", ["sma", "vwap", "bb_upper", "bb_lower", "close"])
def test_no_signal_when_indicator_missing(missing):
    assert evaluate_bar(row(**{missing: np.nan})).side is None


# ---------------------------------------------------------------- Handelsplan 2:1
def test_long_plan_two_to_one():
    p = plan_trade("LONG", 100.0, 90.0, 2.0)
    assert (p.stop, p.take_profit, p.risk_per_share) == (90.0, 120.0, 10.0)   # 10 Punkte Risiko -> 20 Punkte Ziel


def test_short_plan_two_to_one():
    p = plan_trade("SHORT", 100.0, 110.0, 2.0)
    assert (p.stop, p.take_profit) == (110.0, 80.0)


@pytest.mark.parametrize("side,stop", [("LONG", 100.0), ("LONG", 101.0), ("SHORT", 100.0), ("SHORT", 99.0)])
def test_plan_rejected_when_stop_on_wrong_side(side, stop):
    assert plan_trade(side, 100.0, stop) is None


# ---------------------------------------------------------------- Indikatoren
def _bars(closes, day=date(2026, 11, 25)):
    idx = pd.date_range(ny(day, 9, 30), periods=len(closes), freq="5min")
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 100.0})


def test_sma_and_bollinger_match_manual_calculation():
    closes = list(np.linspace(100, 110, 30) + np.sin(np.arange(30)))
    ind = compute_indicators(_bars(closes), StrategyParams())
    last20 = np.array(closes[-20:])
    assert ind["sma"].iloc[-1] == pytest.approx(np.mean(closes[-9:]))
    assert ind["bb_mid"].iloc[-1] == pytest.approx(last20.mean())
    assert ind["bb_upper"].iloc[-1] == pytest.approx(last20.mean() + 2 * last20.std(ddof=0))   # Populations-Abweichung
    assert ind["bb_lower"].iloc[-1] == pytest.approx(last20.mean() - 2 * last20.std(ddof=0))


def test_vwap_is_session_vwap_and_resets_each_day():
    d1 = _bars([100, 101, 102], day=date(2026, 11, 24))
    d2 = _bars([200, 201, 202], day=date(2026, 11, 25))
    ind = compute_indicators(pd.concat([d1, d2]), StrategyParams())
    first_new_day = ind.iloc[3]
    typical = (first_new_day["high"] + first_new_day["low"] + first_new_day["close"]) / 3
    assert first_new_day["vwap"] == pytest.approx(typical)          # kein Einfluss des Vortags
    day1_vwap = ((d1["high"] + d1["low"] + d1["close"]) / 3).mean()  # gleiche Volumina
    assert ind.iloc[2]["vwap"] == pytest.approx(day1_vwap)


def test_indicators_use_no_future_data():
    """Kennzahl der Kerze k ist identisch, egal ob spätere Kerzen schon vorliegen (kein Look-ahead)."""
    df1, _ = build_history([(date(2026, 11, 23), "up"), (date(2026, 11, 24), "reverse"), (date(2026, 11, 25), "down")])
    from tests.helpers import resample
    m5 = resample(df1, 5)
    full = compute_indicators(m5)
    for k in (30, 100, 170, len(m5) - 1):
        part = compute_indicators(m5.iloc[: k + 1]).iloc[-1]
        for col in ("sma", "vwap", "bb_upper", "bb_lower"):
            assert full.iloc[k][col] == pytest.approx(part[col])
