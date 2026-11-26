"""Lokales Dashboard-Backend. Es liest nur: Zustand aus der SQLite-Datei, optional Live-Kurs/Konto von Alpaca.

Der Bot selbst läuft auf GitHub (Actions). Dieses Backend startet NIE Trades.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .backtest import months_to_range, run_backtest
from .broker import AlpacaBroker
from .config import ROOT, Settings, load_settings
from .db import Store
from .export import build_state
from .fx import get_usd_eur
from .marketdata import AlpacaData
from .state_sync import pull_state
from .timeutil import UTC

log = logging.getLogger("bot.api")
app = FastAPI(title="SPY Bot Dashboard")
_live_cache: dict = {"t": 0.0, "v": None}


def _settings() -> Settings:
    try:
        return load_settings()
    except SystemExit as exc:
        raise HTTPException(500, str(exc)) from exc


def _live(s: Settings) -> dict | None:
    """Live-Daten nur mit gültigen Keys; kurz zwischengespeichert, damit das Dashboard Alpaca nicht überlastet."""
    if not s.has_keys:
        return None
    if time.time() - _live_cache["t"] < 8 and _live_cache["v"] is not None:
        return _live_cache["v"]
    out: dict = {"broker_ok": False, "price": None, "account": None, "market_open": None, "error": None}
    try:
        broker = AlpacaBroker(s)
        out["account"] = broker.account()
        out["broker_ok"] = True
        out["market_open"] = broker.clock()["is_open"]
        out["price"] = AlpacaData(s).latest_price()
    except Exception as exc:
        out["error"] = str(exc)[:200]
        log.warning("Live-Daten nicht verfügbar: %s", exc)
    _live_cache.update(t=time.time(), v=out)
    return out


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/state")
def state() -> dict:
    s = _settings()
    store = Store(s.state_dir / "trading.db")
    try:
        return build_state(store, s, datetime.now(UTC), get_usd_eur(s), live=_live(s))
    finally:
        store.close()


@app.post("/api/sync")
def sync() -> dict:
    s = _settings()
    try:
        return {"message": pull_state(s.state_dir)}
    except RuntimeError as exc:
        raise HTTPException(400, f"Konnte den Zustand nicht von GitHub holen: {exc}") from exc


class BacktestRequest(BaseModel):
    months: int = 3


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict:
    s = _settings()
    if not s.has_keys:
        raise HTTPException(400, "Für den Backtest werden Alpaca-Keys in der .env-Datei benötigt.")
    if req.months not in (1, 3, 6, 12):
        raise HTTPException(400, "Zeitraum: 1, 3, 6 oder 12 Monate.")
    start, end = months_to_range(req.months)
    try:
        result = run_backtest(AlpacaData(s), AlpacaBroker(s), s, start, end)
    except Exception as exc:
        raise HTTPException(502, f"Backtest fehlgeschlagen: {exc}") from exc
    result["fx"] = get_usd_eur(s)
    return result


_dist = ROOT / "frontend" / "dist"
if _dist.exists():  # gebautes Dashboard direkt ausliefern: nur ein Prozess, nur ein Port
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
