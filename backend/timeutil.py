"""Zeit-Helfer: New York (Börse) <-> Wien (Anzeige). Es werden nie feste UTC-Zeiten angenommen."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
VIE = ZoneInfo("Europe/Vienna")
BAR_MIN = 5


@dataclass(frozen=True)
class Session:
    """Ein Handelstag: Open/Close als zeitzonenbehaftete Zeitpunkte (NY)."""

    day: date
    open: datetime
    close: datetime

    def entry_end(self, window_min: int) -> datetime:
        return self.open + timedelta(minutes=window_min)

    def force_close_at(self, minutes_before: int) -> datetime:
        return self.close - timedelta(minutes=minutes_before)


def _as_ny(x: datetime) -> datetime:
    return x.replace(tzinfo=NY) if x.tzinfo is None else x.astimezone(NY)


def session_from_calendar(day: date, open_: datetime, close: datetime) -> Session:
    """Alpaca liefert Open/Close als naive Zeiten in New Yorker Ortszeit (inkl. verkürzter Tage)."""
    return Session(day, _as_ny(open_), _as_ny(close))


def default_session(day: date) -> Session | None:
    """Notnagel ohne Kalender: Mo-Fr 9:30-16:00 ET, Feiertage unbekannt."""
    if day.weekday() >= 5:
        return None
    return Session(day, datetime.combine(day, time(9, 30), tzinfo=NY), datetime.combine(day, time(16, 0), tzinfo=NY))


def ny_date(now: datetime) -> date:
    return now.astimezone(NY).date()


def vienna(dt: datetime) -> datetime:
    return dt.astimezone(VIE)


def fmt_vie(dt: datetime, seconds: bool = False) -> str:
    return vienna(dt).strftime("%H:%M:%S" if seconds else "%H:%M")


def floor_boundary(dt: datetime, minutes: int = BAR_MIN) -> datetime:
    """Letzte volle Kerzengrenze <= dt (NY-Offsets sind ganze Stunden, daher reicht Epoch-Rechnung)."""
    step = minutes * 60
    ts = int(dt.timestamp())
    return datetime.fromtimestamp(ts - ts % step, UTC)
