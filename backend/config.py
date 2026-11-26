"""Einstellungen: alles kommt aus Umgebungsvariablen bzw. der lokalen .env-Datei."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LIVE_PHRASE = "JA_ECHTES_GELD"
MODES = ("analysis", "paper", "live")


def _num(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw.replace(",", ".")) if raw else float(default)


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    secret_key: str = ""
    mode: str = "paper"              # analysis | paper | live
    symbol: str = "SPY"
    data_feed: str = "iex"           # iex = gratis + Echtzeit, sip = braucht Alpaca-Abo
    risk_pct: float = 0.5            # Risiko pro Trade in % vom Kontostand
    max_leverage: float = 2.0        # Obergrenze Positionswert / Kontostand
    min_stop_distance: float = 0.10  # kleinster erlaubter Stop-Abstand in USD
    rr: float = 2.0                  # Chance-Risiko-Verhältnis
    sma_len: int = 9
    bb_len: int = 20
    bb_std: float = 2.0
    window_minutes: int = 90         # Einstiegsfenster ab NY-Open (9:30 -> 11:00 ET = 15:30 -> 17:00 Wien)
    force_close_min: int = 5         # Zwangsschluss x Minuten vor Börsenschluss (15:55 ET = 21:55 Wien)
    slippage: float = 0.01           # USD pro Aktie bei Market-/Stop-Fills (Analyse + Backtest)
    fee_per_share: float = 0.0       # Gebühren pro Aktie und Seite
    start_balance: float = 100_000.0  # virtuelles Konto im Analysemodus
    usd_eur_fallback: float = 0.86
    bar_latency_sec: int = 8         # Wartezeit nach Kerzenschluss, bis die Kerze beim Anbieter liegt
    max_entry_delay_sec: int = 150   # Signal älter als das -> kein Einstieg
    state_dir: Path = ROOT / "data"

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.secret_key) and "HIER" not in self.secret_key.upper()

    @property
    def paper(self) -> bool:
        return self.mode != "live"


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    mode = (os.getenv("BOT_MODE", "") or "paper").strip().lower()
    if mode not in MODES:
        raise SystemExit(f"BOT_MODE muss eines von {MODES} sein, nicht '{mode}'.")
    if mode == "live" and os.getenv("LIVE_CONFIRM", "").strip() != LIVE_PHRASE:
        raise SystemExit(
            "Live-Handel ist NICHT freigegeben. Nur wenn du echtes Geld riskieren willst: "
            f"LIVE_CONFIRM={LIVE_PHRASE} setzen (und Live-Keys verwenden)."
        )
    risk = _num("RISK_PER_TRADE_PCT", 0.5)
    if not 0 < risk <= 5:
        raise SystemExit("RISK_PER_TRADE_PCT muss größer 0 und höchstens 5 sein.")
    feed = (os.getenv("DATA_FEED", "") or "iex").strip().lower()
    if feed not in ("iex", "sip"):
        raise SystemExit("DATA_FEED muss iex oder sip sein.")
    state = os.getenv("STATE_DIR", "").strip()
    return Settings(
        api_key=(os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID") or "").strip(),
        secret_key=(os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY") or "").strip(),
        mode=mode,
        symbol=(os.getenv("SYMBOL", "") or "SPY").strip().upper(),
        data_feed=feed,
        risk_pct=risk,
        max_leverage=_num("MAX_LEVERAGE", 2.0),
        min_stop_distance=_num("MIN_STOP_DISTANCE", 0.10),
        slippage=_num("SLIPPAGE_PER_SHARE", 0.01),
        fee_per_share=_num("FEE_PER_SHARE", 0.0),
        start_balance=_num("ANALYSIS_START_BALANCE", 100_000),
        usd_eur_fallback=_num("USD_EUR_FALLBACK", 0.86),
        state_dir=Path(state) if state else ROOT / "data",
    )
