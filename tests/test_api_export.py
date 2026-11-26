"""Zustandsaufbereitung und lokale API (ohne Netzwerk)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend import api
from backend.config import Settings
from backend.db import Store
from backend.export import build_state, job_summary
from backend.timeutil import UTC
from tests.helpers import ny
from datetime import date

D = date(2026, 11, 25)
DAY = D.isoformat()


def filled_store(tmp_path):
    store = Store(tmp_path / "trading.db")
    store.meta_set("start_equity_usd", 100_000)
    store.meta_set("last_equity_usd", 100_300)
    store.set_day("2026-11-24", "NO_TRADE", "Kein Signal", mode="paper")
    store.create_trade(date="2026-11-23", mode="paper", symbol="SPY", side="LONG", qty=100, status="CLOSED", entry_price=680.0,
                       stop_price=679.0, take_profit=682.0, risk_usd=100, potential_usd=200, exit_price=682.0, exit_reason="TP",
                       pnl_usd=200.0, fx_usd_eur=0.9)
    store.set_day("2026-11-23", "WIN", "Take-Profit erreicht: +200.00 USD", mode="paper", force=True)
    store.create_trade(date=DAY, mode="paper", symbol="SPY", side="SHORT", qty=50, status="OPEN", entry_price=680.0,
                       stop_price=681.0, take_profit=678.0, risk_usd=50, potential_usd=100,
                       entry_time_utc=ny(D, 9, 35, 8).isoformat())
    store.set_day(DAY, "ACTIVE", "SHORT eröffnet", mode="paper")
    return store


def test_build_state_for_active_short(tmp_path):
    store = filled_store(tmp_path)
    s = Settings(state_dir=tmp_path)
    st = build_state(store, s, ny(D, 11, 0), fx=0.9, live={"broker_ok": True, "price": 679.0, "account": {"equity": 100_300.0}, "market_open": True})
    assert st["today"]["status"] == "ACTIVE" and st["market"]["status"] == "open"
    t = st["trade"]
    assert t["side"] == "SHORT" and t["current_price"] == 679.0
    assert t["pnl_eur"] == pytest.approx((680.0 - 679.0) * 50 * 0.9)         # Short gewinnt bei fallendem Kurs
    assert t["risk_eur"] == pytest.approx(45.0) and t["potential_eur"] == pytest.approx(90.0)
    assert st["account"]["equity_eur"] == pytest.approx(100_300 * 0.9) and st["broker"]["connected"] is True
    k = st["stats"]
    assert k["trades"] == 1 and k["wins"] == 1 and k["total_pnl"] == pytest.approx(180.0)    # nur abgeschlossene Trades
    assert [h["status"] for h in st["history"]] == ["ACTIVE", "NO_TRADE", "WIN"]           # auch Tage ohne Trade
    assert st["market"]["force_close_at"].startswith("2026-11-25T20:55")


def test_state_without_bot_data_is_explained(tmp_path):
    store = Store(tmp_path / "leer.db")
    st = build_state(store, Settings(state_dir=tmp_path), ny(D, 12, 0), fx=0.9)
    assert st["today"]["status"] == "PENDING" and "Keine Daten" in st["today"]["reason"] or "Noch keine Daten" in st["today"]["reason"]
    weekend = build_state(store, Settings(state_dir=tmp_path), ny(date(2026, 11, 28), 12, 0), fx=0.9)
    assert weekend["today"]["status"] == "CLOSED" and weekend["market"]["status"] == "closed"


def test_job_summary_mentions_trade(tmp_path):
    store = filled_store(tmp_path)
    text = job_summary(store, Settings(state_dir=tmp_path), "entered", ny(D, 11, 0), 0.9)
    assert "entered" in text and "SHORT" in text and "ACTIVE" in text


def test_api_state_endpoint(tmp_path, monkeypatch):
    filled_store(tmp_path).close()
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")
    monkeypatch.setattr(api, "get_usd_eur", lambda s: 0.9)
    r = TestClient(api.app).get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "paper" and body["trade"]["side"] == "SHORT" and body["live"] is False
    assert TestClient(api.app).post("/api/backtest", json={"months": 3}).status_code == 400    # ohne Keys kein Backtest
