"""Alpaca-Broker (Paper oder Live): dünner Wrapper, der nur einfache dicts zurückgibt."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from alpaca.common.enums import Sort
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetCalendarRequest,
    GetOrderByIdRequest,
    GetOrdersRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from .config import Settings
from .marketdata import retry
from .timeutil import Session, session_from_calendar

log = logging.getLogger("bot.broker")
DONE = ("filled", "canceled", "expired", "replaced", "rejected", "done_for_day")


def _v(x):
    return getattr(x, "value", x)


def _f(x):
    return float(x) if x not in (None, "") else None


def _otype(o) -> str:
    return str(_v(getattr(o, "type", None) or getattr(o, "order_type", None)))


def _is_404(exc: APIError) -> bool:
    return getattr(exc, "status_code", None) == 404


def order_dict(o) -> dict:
    return {
        "id": str(o.id),
        "client_order_id": o.client_order_id,
        "status": str(_v(o.status)),
        "side": str(_v(o.side)),
        "type": _otype(o),
        "qty": _f(o.qty),
        "filled_qty": _f(o.filled_qty) or 0.0,
        "filled_avg_price": _f(o.filled_avg_price),
        "filled_at": o.filled_at,
        "limit_price": _f(o.limit_price),
        "stop_price": _f(o.stop_price),
        "legs": [order_dict(leg) for leg in (o.legs or [])],
    }


class AlpacaBroker:
    def __init__(self, s: Settings):
        self.s = s
        self.client = TradingClient(s.api_key, s.secret_key, paper=s.paper)

    # ---------- Lesen ----------
    def account(self) -> dict:
        a = retry(self.client.get_account, "Konto")
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "status": str(_v(a.status)),
            "currency": a.currency,
            "trading_blocked": bool(a.trading_blocked),
            "account_blocked": bool(a.account_blocked),
            "shorting_enabled": bool(a.shorting_enabled),
            "multiplier": str(a.multiplier),
        }

    def clock(self) -> dict:
        c = retry(self.client.get_clock, "Börsenuhr")
        return {"timestamp": c.timestamp, "is_open": bool(c.is_open), "next_open": c.next_open, "next_close": c.next_close}

    def sessions(self, start: date, end: date) -> list[Session]:
        rows = retry(lambda: self.client.get_calendar(GetCalendarRequest(start=start, end=end)), "Börsenkalender")
        return [session_from_calendar(r.date, r.open, r.close) for r in rows]

    def session_for(self, day: date) -> Session | None:
        for sess in self.sessions(day, day):
            if sess.day == day:
                return sess
        return None

    def asset_flags(self) -> dict:
        a = retry(lambda: self.client.get_asset(self.s.symbol), "Asset")
        return {
            "tradable": bool(a.tradable),
            "shortable": bool(a.shortable),
            "easy_to_borrow": bool(a.easy_to_borrow),
            "status": str(_v(a.status)),
        }

    def position(self) -> dict | None:
        try:
            p = retry(lambda: self.client.get_open_position(self.s.symbol), "Position", attempts=2)
        except APIError as exc:
            if _is_404(exc):
                return None
            raise
        return {
            "qty": float(p.qty),
            "side": str(_v(p.side)),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": _f(p.current_price),
            "unrealized_pl": _f(p.unrealized_pl),
        }

    def order(self, order_id: str) -> dict | None:
        try:
            return order_dict(self.client.get_order_by_id(order_id, GetOrderByIdRequest(nested=True)))
        except APIError as exc:
            if _is_404(exc):
                return None
            raise

    def order_by_client_id(self, client_id: str) -> dict | None:
        try:
            return order_dict(self.client.get_order_by_client_id(client_id))
        except APIError as exc:
            if _is_404(exc):
                return None
            raise

    def exit_fill(self, entry_side: str, after: datetime) -> dict | None:
        """Der gefüllte Gegen-Order nach dem Einstieg (Take-Profit-, Stop- oder Zwangsschluss-Order)."""
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            symbols=[self.s.symbol],
            after=after - timedelta(minutes=2),
            limit=100,
            direction=Sort.ASC,
        )
        orders = retry(lambda: self.client.get_orders(req), "Order-Historie")
        opposite = "sell" if entry_side == "LONG" else "buy"
        fills = [
            o for o in orders
            if str(_v(o.side)) == opposite and str(_v(o.status)) == "filled" and o.filled_at and o.filled_avg_price
        ]
        if not fills:
            return None
        o = max(fills, key=lambda x: x.filled_at)
        kind = _otype(o)
        reason = {"limit": "TP", "stop": "SL", "stop_limit": "SL"}.get(kind, "FORCED")
        return {"price": float(o.filled_avg_price), "time": o.filled_at, "reason": reason, "order_id": str(o.id)}

    # ---------- Schreiben ----------
    def place_bracket(self, side: str, qty: int, stop: float, take_profit: float, client_id: str) -> dict:
        """Market-Einstieg mit Stop-Loss und Take-Profit direkt beim Broker.

        GTC: Die Ausstiegs-Orders erben die Gültigkeit der Haupt-Order. Bei DAY würden sie am Börsenende
        verfallen und eine offene Position ungeschützt über Nacht lassen.
        """
        req = MarketOrderRequest(
            symbol=self.s.symbol,
            qty=qty,
            side=OrderSide.BUY if side == "LONG" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            client_order_id=client_id,
        )
        return order_dict(self.client.submit_order(req))

    def cancel_legs(self, parent_id: str) -> int:
        parent = self.client.get_order_by_id(parent_id, GetOrderByIdRequest(nested=True))
        cancelled = 0
        for leg in parent.legs or []:
            if str(_v(leg.status)) in DONE:
                continue
            try:
                self.client.cancel_order_by_id(leg.id)
                cancelled += 1
            except APIError as exc:
                log.warning("Leg %s nicht stornierbar: %s", leg.id, exc)
        return cancelled

    def close_position(self) -> dict:
        return order_dict(self.client.close_position(self.s.symbol))
