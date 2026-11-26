"""Testhilfen: künstliche Kursverläufe, simulierte Uhr, simulierter Datenanbieter und Broker."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from backend.marketdata import filter_rth
from backend.simulator import simulate_exit
from backend.timeutil import NY, UTC, Session, default_session


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, sec: float) -> None:
        self.t += timedelta(seconds=max(float(sec), 0.0))

    def sleep_until(self, dt: datetime) -> None:
        if dt > self.t:
            self.t = dt


def ny(day: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    """Zeitpunkt in New Yorker Ortszeit, als UTC-datetime."""
    return datetime.combine(day, time(hh, mm, ss), tzinfo=NY).astimezone(UTC)


def make_day(day: date, path, base: float, close_h: int = 16, close_m: int = 0) -> pd.DataFrame:
    """1-Minuten-Kerzen eines Handelstags. path(i) = Schlusskurs der i-ten Minute nach dem Open."""
    start = ny(day, 9, 30)
    n = int((ny(day, close_h, close_m) - start).total_seconds() // 60)
    closes = [round(float(path(i)), 2) for i in range(n)]
    opens = [round(base, 2)] + closes[:-1]
    rows = [(o, round(max(o, c) + 0.02, 2), round(min(o, c) - 0.02, 2), c, 1000.0) for o, c in zip(opens, closes)]
    idx = pd.DatetimeIndex([start + timedelta(minutes=i) for i in range(n)])
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


PATHS = {
    "flat": lambda b: (lambda i: b),
    "up": lambda b: (lambda i: b + 0.05 * (min(i, 29) + 1)),          # +0.05 pro Minute, 30 Minuten lang
    "down": lambda b: (lambda i: b - 0.05 * (min(i, 29) + 1)),
    "rise": lambda b: (lambda i: b + 0.05 * (i + 1)),                  # steigt den ganzen Tag
    "up_then_flat": lambda b: (lambda i: b + 0.05 * (min(i, 4) + 1)),  # 5 Minuten hoch, dann seitwärts
    "reverse": lambda b: (lambda i: b + 0.05 * (i + 1) if i < 5 else b + 0.25 - 0.1 * (min(i, 30) - 4)),
}


def build_history(specs: list[tuple[date, str]], base: float = 680.0, close_h: int = 16, close_m: int = 0):
    """Aneinandergereihte Handelstage; jeder Tag startet beim Schlusskurs des Vortags."""
    frames, sessions = [], {}
    for d, kind in specs:
        df = make_day(d, PATHS[kind](base), base, close_h, close_m)
        frames.append(df)
        sessions[d] = default_session(d) if close_h == 16 else Session(d, ny(d, 9, 30).astimezone(NY), ny(d, close_h, close_m).astimezone(NY))
        base = float(df["close"].iloc[-1])
    return pd.concat(frames), sessions


def resample(df1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 1 or df1.empty:
        return df1
    r = df1.resample(f"{minutes}min", label="left", closed="left")
    out = pd.DataFrame({
        "open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(),
        "close": r["close"].last(), "volume": r["volume"].sum(),
    })
    return out.dropna(subset=["open"])


class FakeData:
    """Kennt nur Kerzen, die zum Uhrzeitpunkt existieren (die laufende Kerze kommt unfertig zurück)."""

    def __init__(self, df1: pd.DataFrame, clock: FakeClock):
        self.df1, self.clock = df1, clock

    def bars(self, start, end, minutes):
        known = self.df1[self.df1.index < pd.Timestamp(self.clock.now())]
        sel = known[(known.index >= pd.Timestamp(start)) & (known.index <= pd.Timestamp(end))]
        return resample(sel, minutes)

    def latest_price(self) -> float:
        done = self.df1[self.df1.index + pd.Timedelta(minutes=1) <= pd.Timestamp(self.clock.now())]
        return float(done["close"].iloc[-1])


class FailingData(FakeData):
    def bars(self, start, end, minutes):
        raise ConnectionError("Datenanbieter nicht erreichbar")


class FakeBroker:
    """Bildet Alpaca nach: Bracket-Order, Position, Stop/Ziel-Fills (über den Kursverlauf), Zwangsschluss."""

    def __init__(self, data: FakeData, clock: FakeClock, sessions: dict, equity: float = 100_000.0):
        self.data, self.clock, self._sessions, self.equity = data, clock, sessions, equity
        self.orders: dict[str, dict] = {}
        self.pos: dict | None = None
        self.exit: dict | None = None
        self.calls: list[str] = []
        self.account_fails = False

    def account(self):
        if self.account_fails:
            raise ConnectionError("Broker nicht erreichbar")
        return {"equity": self.equity, "cash": self.equity, "buying_power": self.equity * 4, "status": "ACTIVE",
                "currency": "USD", "trading_blocked": False, "account_blocked": False,
                "shorting_enabled": True, "multiplier": "4"}

    def asset_flags(self):
        return {"tradable": True, "shortable": True, "easy_to_borrow": True, "status": "active"}

    def sessions(self, start, end):
        return [s for d, s in sorted(self._sessions.items()) if s is not None and start <= d <= end]

    def session_for(self, day):
        return self._sessions.get(day)

    def place_bracket(self, side, qty, stop, take_profit, client_id):
        if any(o["client_order_id"] == client_id for o in self.orders.values()):
            raise RuntimeError("client_order_id must be unique")
        price, now = self.data.latest_price(), self.clock.now()
        oid = f"ord{len(self.orders) + 1}"
        o = {
            "id": oid, "client_order_id": client_id, "status": "filled", "side": "buy" if side == "LONG" else "sell",
            "type": "market", "qty": float(qty), "filled_qty": float(qty), "filled_avg_price": price, "filled_at": now,
            "limit_price": None, "stop_price": None,
            "legs": [
                {"id": oid + "a", "type": "limit", "limit_price": take_profit, "stop_price": None, "status": "new"},
                {"id": oid + "b", "type": "stop", "limit_price": None, "stop_price": stop, "status": "held"},
            ],
        }
        self.orders[oid] = o
        self.pos = {"side": side, "qty": qty, "entry": price, "stop": stop, "tp": take_profit, "time": now}
        self.calls.append("place_bracket")
        return o

    def _resolve(self):
        p = self.pos
        if p is None:
            return
        m1 = filter_rth(self.data.bars(p["time"], self.clock.now(), 1))
        res = simulate_exit(m1, p["side"], p["entry"], p["stop"], p["tp"], p["time"], None, 0.0, now=None)
        if res.reason in ("TP", "SL"):
            self.exit = {"price": res.price, "time": res.time, "reason": res.reason, "order_id": "leg"}
            self.pos = None

    def position(self):
        self._resolve()
        if self.pos is None:
            return None
        return {"qty": float(self.pos["qty"]), "side": self.pos["side"], "avg_entry_price": self.pos["entry"],
                "current_price": self.data.latest_price(), "unrealized_pl": 0.0}

    def order(self, order_id):
        return self.orders.get(order_id)

    def order_by_client_id(self, client_id):
        return next((o for o in self.orders.values() if o["client_order_id"] == client_id), None)

    def exit_fill(self, entry_side, after):
        self._resolve()
        return self.exit

    def cancel_legs(self, parent_id):
        self.calls.append("cancel_legs")
        return 2

    def close_position(self):
        self._resolve()
        if self.pos is None:
            raise RuntimeError("position does not exist")
        price, now = self.data.latest_price(), self.clock.now()
        oid = f"ord{len(self.orders) + 1}"
        o = {"id": oid, "client_order_id": None, "status": "filled", "type": "market",
             "side": "sell" if self.pos["side"] == "LONG" else "buy", "qty": float(self.pos["qty"]),
             "filled_qty": float(self.pos["qty"]), "filled_avg_price": price, "filled_at": now,
             "limit_price": None, "stop_price": None, "legs": []}
        self.orders[oid] = o
        self.exit = {"price": price, "time": now, "reason": "FORCED", "order_id": oid}
        self.pos = None
        self.calls.append("close_position")
        return o
