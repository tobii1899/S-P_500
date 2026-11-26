"""Marktdaten von Alpaca (IEX gratis in Echtzeit, SIP nur mit Abo) plus Kerzen-Helfer."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from .config import Settings
from .timeutil import NY

log = logging.getLogger("bot.data")
COLS = ["open", "high", "low", "close", "volume"]


def retry(fn, what: str, attempts: int = 3, delay: float = 2.0):
    """Lesende API-Aufrufe wiederholen (automatische Wiederverbindung bei kurzen Aussetzern)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            log.warning("%s fehlgeschlagen (Versuch %d/%d): %s", what, i + 1, attempts, exc)
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    raise last  # type: ignore[misc]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC", name="timestamp"), dtype="float64")


def normalize_bars(df: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return empty_bars()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level=0)
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = df[COLS].astype("float64").copy()
    out.index = idx
    return out.sort_index()


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Nur Kerzen der regulären Handelszeit (Kerzenbeginn 9:30 bis 15:59 ET)."""
    if df.empty:
        return df
    loc = df.index.tz_convert(NY)
    minutes = loc.hour * 60 + loc.minute
    return df[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]


def closed_only(df: pd.DataFrame, now: datetime, minutes: int) -> pd.DataFrame:
    """Nur abgeschlossene Kerzen: Beginn + Länge <= jetzt. Die laufende Kerze wird nie verwendet."""
    if df.empty:
        return df
    return df[df.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)]


class AlpacaData:
    def __init__(self, s: Settings):
        self.s = s
        self.client = StockHistoricalDataClient(s.api_key, s.secret_key)
        self.feed = DataFeed.SIP if s.data_feed == "sip" else DataFeed.IEX

    def bars(self, start: datetime, end: datetime, minutes: int) -> pd.DataFrame:
        req = StockBarsRequest(
            symbol_or_symbols=self.s.symbol,
            timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
            start=start,
            end=end,
            feed=self.feed,
            adjustment=Adjustment.RAW,
        )
        bar_set = retry(lambda: self.client.get_stock_bars(req), f"{minutes}-Min-Kerzen")
        return normalize_bars(bar_set.df, self.s.symbol)

    def latest_price(self) -> float:
        req = StockLatestTradeRequest(symbol_or_symbols=self.s.symbol, feed=self.feed)
        trade = retry(lambda: self.client.get_stock_latest_trade(req), "Letzter Kurs")
        return float(trade[self.s.symbol].price)
