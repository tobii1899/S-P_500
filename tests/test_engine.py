"""Engine: kompletter Tagesablauf mit simuliertem Markt, Broker und Uhr (Mittwoch 25.11.2026, Winterzeit)."""
import sqlite3
from datetime import date

import pandas as pd
import pytest

from backend.config import Settings
from backend.db import Store
from backend.engine import Engine
from tests.helpers import FailingData, FakeBroker, FakeClock, FakeData, build_history, ny

D = date(2026, 11, 25)
DAY = D.isoformat()
WARM = [(date(2026, 11, 23), "flat"), (date(2026, 11, 24), "flat")]


def setup(tmp_path, kind, mode="paper", start=(9, 20), warm=None):
    df, sessions = build_history((warm or WARM) + [(D, kind)])
    clock = FakeClock(ny(D, *start))
    data = FakeData(df, clock)
    broker = FakeBroker(data, clock, sessions)
    store = Store(tmp_path / "t.db")
    eng = Engine(Settings(api_key="k", secret_key="s", mode=mode, state_dir=tmp_path), store, data, broker, clock, fx=lambda: 0.9)
    return eng, store, broker, clock


# ------------------------------------------------------------------ Einstieg
def test_long_entry_right_after_first_candle_then_take_profit(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up")
    assert eng.run_session() == "entered"
    t = store.get_trade(DAY)
    assert (t["side"], t["qty"], t["status"]) == ("LONG", 294, "OPEN")
    assert (t["stop_price"], t["take_profit"]) == (679.90, 680.98)       # Stop = unteres Band, Ziel = 2 x Risiko
    assert t["entry_time_utc"].startswith("2026-11-25T14:35")             # 9:35 ET = 15:35 Wien, direkt nach Kerzenschluss
    assert store.get_day(DAY)["status"] == "ACTIVE"
    assert broker.calls == ["place_bracket"]
    clock.t = ny(D, 15, 30)
    assert eng.run_close() == "closed"
    t = store.get_trade(DAY)
    assert (t["status"], t["exit_reason"]) == ("CLOSED", "TP")
    assert t["pnl_usd"] == pytest.approx(0.73 * 294, abs=0.01)
    assert store.get_day(DAY)["status"] == "WIN"
    assert t["fx_usd_eur"] == 0.9


def test_short_entry(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "down")
    assert eng.run_session() == "entered"
    t = store.get_trade(DAY)
    assert (t["side"], t["stop_price"], t["take_profit"]) == ("SHORT", 680.10, 679.02)
    clock.t = ny(D, 15, 30)
    eng.run_close()
    assert store.get_trade(DAY)["exit_reason"] == "TP" and store.get_day(DAY)["status"] == "WIN"


def test_stop_loss_day_is_a_loss(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "reverse")
    assert eng.run_session() == "entered"
    clock.t = ny(D, 15, 30)
    eng.run_close()
    t = store.get_trade(DAY)
    assert t["exit_reason"] == "SL" and t["pnl_usd"] < 0
    assert store.get_day(DAY)["status"] == "LOSS"


def test_forced_close_at_2155_vienna(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up_then_flat")
    assert eng.run_session() == "entered"
    clock.t = ny(D, 15, 30)
    assert eng.run_close() == "closed"
    t = store.get_trade(DAY)
    assert t["exit_reason"] == "FORCED"
    assert t["exit_time_utc"].startswith("2026-11-25T20:55")               # 15:55 ET = 21:55 Wien
    assert "cancel_legs" in broker.calls and "close_position" in broker.calls
    assert store.get_trade(DAY)["status"] == "CLOSED"


def test_early_close_day_forces_close_at_1855_vienna(tmp_path):
    day = date(2026, 11, 27)                                                # Tag nach Thanksgiving: Börsenschluss 13:00 ET
    df1, s1 = build_history([(date(2026, 11, 24), "flat"), (date(2026, 11, 25), "flat")])
    df2, s2 = build_history([(day, "up_then_flat")], close_h=13)
    clock = FakeClock(ny(day, 9, 20))
    data = FakeData(pd.concat([df1, df2]), clock)
    broker = FakeBroker(data, clock, {**s1, **s2})
    store = Store(tmp_path / "h.db")
    eng = Engine(Settings(api_key="k", secret_key="s", mode="paper"), store, data, broker, clock, fx=lambda: 0.9)
    assert eng.run_session() == "entered"
    clock.t = ny(day, 12, 25)
    assert eng.run_close() == "closed"
    t = store.get_trade(day.isoformat())
    assert t["exit_reason"] == "FORCED" and t["exit_time_utc"].startswith("2026-11-27T17:55")   # 12:55 ET = 18:55 Wien


def test_close_job_too_early_leaves_trade_open(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up_then_flat")
    eng.run_session()
    clock.t = ny(D, 14, 0)
    assert eng.run_close() == "too_early"
    assert store.get_trade(DAY)["status"] == "OPEN"


# ------------------------------------------------------------------ Kein Trade
def test_flat_market_gives_no_trade_and_stops_at_window_end(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "flat")
    assert eng.run_session() == "no_trade"
    assert store.get_trade(DAY) is None and broker.calls == []
    assert store.get_day(DAY)["status"] == "NO_TRADE"
    sigs = store._all("SELECT * FROM signals ORDER BY bar_start_utc")
    assert len(sigs) == 17 and {x["side"] for x in sigs} == {"NONE"}      # Kerzen mit Schluss 9:35 ... 10:55 ET
    assert sigs[0]["bar_end_utc"].startswith("2026-11-25T14:35") and sigs[-1]["bar_end_utc"].startswith("2026-11-25T15:55")
    assert clock.t < ny(D, 11, 30)


def test_no_entry_when_started_after_window(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise", start=(11, 10))
    assert eng.run_session() == "no_trade"
    assert store.get_trade(DAY) is None and broker.calls == []


def test_candle_closing_at_1100_never_triggers_entry(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise", start=(10, 59, 55))
    assert eng.run_session() == "no_trade"                                  # Signal der 10:55-Kerze ist zu alt, 11:00-Kerze zählt nicht mehr
    assert broker.calls == []


def test_last_candle_in_window_may_still_trade(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise", start=(10, 55, 30))
    assert eng.run_session() == "entered"


def test_too_early_start_does_nothing(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise", start=(8, 0))
    assert eng.run_session() == "too_early"
    assert broker.calls == []


def test_holiday_is_stored_as_closed(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise")
    broker._sessions[D] = None
    assert eng.run_session() == "closed"
    assert store.get_day(DAY)["status"] == "CLOSED"


def test_close_job_without_any_session_run_marks_no_trade(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "flat", start=(15, 30))
    assert eng.run_close() == "no_trade"
    assert store.get_day(DAY)["status"] == "NO_TRADE"


# ------------------------------------------------------------------ Verspätete Starts, Neustarts, Duplikate
def test_late_start_only_latest_candle_may_trigger(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise", start=(10, 30, 30))
    assert eng.run_session() == "entered"
    t = store.get_trade(DAY)
    assert t["signal_bar_utc"].startswith("2026-11-25T15:25")               # Kerze 10:25-10:30 ET
    actions = [x["action"] for x in store._all("SELECT action FROM signals ORDER BY bar_start_utc")]
    assert actions.count("STALE") == 11 and actions[-1] == "ENTERED"        # ältere Signale nur protokolliert


def test_second_run_never_opens_second_trade(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise")
    assert eng.run_session() == "entered"
    assert eng.run_session() == "already_traded"
    assert broker.calls.count("place_bracket") == 1
    assert len(store._all("SELECT * FROM trades")) == 1


def test_restart_with_lost_database_does_not_duplicate_order(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise")
    assert eng.run_session() == "entered"
    store2 = Store(tmp_path / "neu.db")                                      # Zustand verloren, Order liegt beim Broker
    eng2 = Engine(eng.s, store2, eng.data, broker, clock, fx=lambda: 0.9)
    assert eng2.run_session() == "entered"
    assert broker.calls.count("place_bracket") == 1                          # client_order_id verhindert die zweite Order
    assert store2.get_trade(DAY)["broker_parent_id"] == "ord1"


def test_orphan_order_is_adopted_by_close_job(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up_then_flat")
    clock.t = ny(D, 9, 35, 8)
    broker.place_bracket("LONG", 100, 679.90, 680.98, f"spybot-{DAY}")       # beim Broker, aber nicht in der DB
    clock.t = ny(D, 15, 30)
    assert eng.run_close() == "closed"
    t = store.get_trade(DAY)
    assert t["qty"] == 100 and t["exit_reason"] == "FORCED"
    assert any("übernommen" in e["message"] for e in store.recent_events(30))


def test_one_trade_per_day_enforced_by_database(tmp_path):
    store = Store(tmp_path / "x.db")
    row = dict(date=DAY, mode="paper", symbol="SPY", side="LONG", qty=1, status="OPEN", stop_price=1.0, take_profit=2.0)
    store.create_trade(**row)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_trade(**row)


def test_stale_trade_from_yesterday_is_settled_first(tmp_path):
    warm = [(date(2026, 11, 23), "flat"), (date(2026, 11, 24), "up_then_flat")]
    eng, store, broker, clock = setup(tmp_path, "flat", mode="analysis", warm=warm)
    store.create_trade(date="2026-11-24", mode="analysis", symbol="SPY", side="LONG", qty=100, status="OPEN",
                       planned_entry=680.26, entry_time_utc=ny(date(2026, 11, 24), 9, 35, 8).isoformat(), entry_price=680.26,
                       stop_price=679.90, take_profit=680.98, client_order_id="spybot-2026-11-24")
    store.set_day("2026-11-24", "ACTIVE", "offen")
    eng.run_session()
    old = store.get_trade("2026-11-24")
    assert old["status"] == "CLOSED" and old["exit_reason"] == "FORCED"
    assert store.get_day("2026-11-24")["status"] == "LOSS"                    # 680,24 Ausstieg gegen 680,26 Einstieg


# ------------------------------------------------------------------ Verbindungsprobleme
def test_data_outage_means_no_trade(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "rise")
    eng.data = FailingData(eng.data.df1, clock)
    assert eng.run_session() == "no_trade"
    assert store.get_trade(DAY) is None and broker.calls == []
    assert any(e["level"] == "error" for e in store.recent_events(50))


def test_broker_down_falls_back_to_analysis_mode(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up")
    broker.account_fails = True
    assert eng.run_session() == "entered"
    assert store.get_trade(DAY)["mode"] == "analysis" and broker.calls == []


# ------------------------------------------------------------------ Analysemodus
def test_analysis_mode_simulates_on_real_price_path(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up", mode="analysis")
    assert eng.run_session() == "entered"
    t = store.get_trade(DAY)
    assert (t["mode"], t["entry_price"], t["qty"]) == ("analysis", 680.26, 294) and broker.calls == []
    clock.t = ny(D, 15, 30)
    assert eng.run_close() == "closed"
    t = store.get_trade(DAY)
    assert t["exit_reason"] == "TP" and t["pnl_usd"] == pytest.approx(round(0.72 * 294, 2))
    assert store.get_day(DAY)["status"] == "WIN"


def test_analysis_mode_forced_close_includes_slippage(tmp_path):
    eng, store, broker, clock = setup(tmp_path, "up_then_flat", mode="analysis")
    eng.run_session()
    clock.t = ny(D, 15, 30)
    assert eng.run_close() == "closed"
    t = store.get_trade(DAY)
    assert t["exit_reason"] == "FORCED" and t["pnl_usd"] == pytest.approx(-0.02 * 294)   # Einstieg 680,26 / Ausstieg 680,24
    assert t["slippage_usd"] == pytest.approx(0.02 * 294)
    assert store.get_day(DAY)["status"] == "LOSS"
