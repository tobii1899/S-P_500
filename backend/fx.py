"""USD->EUR-Kurs für die Euro-Anzeige (Konto und Trades werden in USD geführt)."""
from __future__ import annotations

import logging

import requests

from .config import Settings

log = logging.getLogger("bot.fx")
_cache: dict[str, float] = {}
URLS = (
    "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR",
    "https://api.frankfurter.app/latest?from=USD&to=EUR",
)


def get_usd_eur(s: Settings) -> float:
    if "usd_eur" in _cache:
        return _cache["usd_eur"]
    for url in URLS:
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            rate = float(r.json()["rates"]["EUR"])
            if 0.3 < rate < 2.0:
                _cache["usd_eur"] = rate
                return rate
        except Exception as exc:  # Netzwerk egal -> Fallback
            log.warning("Wechselkurs-Abruf fehlgeschlagen (%s): %s", url, exc)
    return s.usd_eur_fallback
