"""Der Tagesablauf.

run_session : sucht im Einstiegsfenster (9:30-11:00 ET = 15:30-17:00 Wien) nach dem ersten gültigen Signal,
              eröffnet höchstens einen Trade und beendet sich dann. SL/TP liegen beim Broker.
run_close   : schließt einen noch offenen Trade 5 Minuten vor Börsenschluss (15:55 ET = 21:55 Wien,
              an verkürzten Tagen entsprechend früher) und rechnet den Tag ab.
Beide Läufe sind idempotent: Ein erneuter Start (Neustart, doppelter Cron) eröffnet nie einen zweiten Trade.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import pandas as pd

from .config import Settings
from .db import FINAL, Store, iso, utcnow_iso
from .marketdata import closed_only, filter_rth
from .risk import Sizing, size_position
from .simulator import ExitResult, simulate_exit, trade_pnl
from .strategy import Signal, StrategyParams, TradePlan, compute_indicators, evaluate_bar, plan_trade
from .timeutil import BAR_MIN, UTC, Session, floor_boundary, fmt_vie, ny_date

log = logging.getLogger("bot.engine")
PRE_START_MIN = 25   # früher als so viele Minuten vor dem Open tut ein Lauf nichts
POLL_SEC = 15
DONE_STATES = ("filled", "canceled", "expired", "replaced", "rejected", "done_for_day")
REASON_TEXT = {
    "TP": "Take-Profit erreicht",
    "SL": "Stop-Loss ausgelöst",
    "FORCED": "Zwangsschluss vor Börsenende",
    "OVERNIGHT": "Über Nacht offen geblieben, zum nächsten Open geschlossen",
}


class ConnectionProblem(Exception):
    pass


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, sec: float) -> None:
        time.sleep(max(0.0, sec))

    def sleep_until(self, dt: datetime) -> None:
        self.sleep((dt - self.now()).total_seconds())


class Engine:
    def __init__(self, s: Settings, store: Store, data, broker, clock=None, fx=None):
        self.s, self.store, self.data, self.broker = s, store, data, broker
        self.clock = clock or SystemClock()
        if fx is None:
            from .fx import get_usd_eur
            fx = lambda: get_usd_eur(s)  # noqa: E731
        self._fx = fx
        self.params = StrategyParams(s.sma_len, s.bb_len, s.bb_std, s.rr)
        self.can_trade = s.mode in ("paper", "live")
        self.mode = s.mode

    # ------------------------------------------------------------------ Hilfen
    def _session(self, d: date) -> Session | None:
        last: Exception | None = None
        for _ in range(3):
            try:
                return self.broker.session_for(d)
            except Exception as exc:
                last = exc
                log.warning("Börsenkalender nicht abrufbar: %s", exc)
                self.clock.sleep(5)
        raise ConnectionProblem(f"Börsenkalender nicht abrufbar: {last}")

    def _status(self, day: str, status: str, reason: str | None = None, force: bool = False, **extra) -> None:
        self.store.set_day(day, status, reason, force=force, mode=self.mode, **extra)

    def _remember_session(self, day: str, sess: Session) -> None:
        cur = self.store.get_day(day)
        self.store.set_day(
            day, cur["status"] if cur else "PENDING", None, force=True, mode=self.mode,
            session_open_utc=iso(sess.open), session_close_utc=iso(sess.close),
        )

    def _pick_mode(self) -> None:
        """Paper/Live nur, wenn der Broker erreichbar ist - sonst fällt der Tag auf den Analysemodus zurück."""
        self.can_trade = self.s.mode in ("paper", "live")
        self.mode = self.s.mode
        if self.can_trade:
            try:
                eq = float(self.broker.account()["equity"])
                if self.store.meta_get("start_equity_usd") is None:
                    self.store.meta_set("start_equity_usd", eq)
                self.store.meta_set("last_equity_usd", eq)
                self.store.meta_set("last_broker_ok_utc", utcnow_iso())
            except Exception as exc:
                self.store.event("error", "broker", f"Broker nicht erreichbar, heute nur Analysemodus: {exc}")
                log.error("Broker nicht erreichbar -> Analysemodus: %s", exc)
                self.can_trade, self.mode = False, "analysis"
        if self.store.meta_get("start_equity_usd") is None:
            self.store.meta_set("start_equity_usd", self.s.start_balance)

    def _equity(self) -> float:
        if self.can_trade:
            eq = float(self.broker.account()["equity"])
            self.store.meta_set("last_equity_usd", eq)
            return eq
        return self.s.start_balance + self.store.realized_pnl_usd()

    def _blocked_reason(self, side: str) -> str | None:
        acct = self.broker.account()
        if acct["trading_blocked"] or acct["account_blocked"]:
            return "Konto ist für den Handel gesperrt"
        flags = self.broker.asset_flags()
        if not flags["tradable"]:
            return f"{self.s.symbol} ist bei Alpaca nicht handelbar"
        if side == "SHORT":
            if not acct["shorting_enabled"]:
                return "Leerverkäufe sind für dieses Konto nicht aktiviert"
            if not (flags["shortable"] and flags["easy_to_borrow"]):
                return f"{self.s.symbol} ist aktuell nicht leerverkaufbar"
        return None

    @staticmethod
    def _entry_dt(t: dict) -> datetime:
        return datetime.fromisoformat(t["entry_time_utc"])

    # ------------------------------------------------------------------ Einstiegssuche
    def run_session(self) -> str:
        s = self.s
        now = self.clock.now()
        d = ny_date(now)
        day = d.isoformat()
        sess = self._session(d)
        if sess is None:
            self._status(day, "CLOSED", "Börse geschlossen (Wochenende/Feiertag)")
            log.info("Heute (%s) ist die Börse geschlossen.", day)
            return "closed"
        self._remember_session(day, sess)
        row = self.store.get_day(day)
        if self.store.get_trade(day):
            log.info("Für %s existiert bereits ein Trade - nichts zu tun.", day)
            return "already_traded"
        if row and row["status"] in FINAL:
            return "already_done"

        open_utc = sess.open.astimezone(UTC)
        first_be = open_utc + timedelta(minutes=BAR_MIN)                       # erste auswertbare Kerze (9:35 ET)
        entry_end = sess.entry_end(s.window_minutes).astimezone(UTC)           # 11:00 ET
        last_be = entry_end - timedelta(minutes=BAR_MIN)                       # letzte Kerze mit Einstiegsrecht (Schluss 10:55)
        if now < open_utc - timedelta(minutes=PRE_START_MIN):
            log.info("Zu früh (Open um %s Uhr Wien) - beende mich.", fmt_vie(open_utc))
            return "too_early"

        self._pick_mode()
        if now < open_utc + timedelta(seconds=15):
            self._status(day, "PENDING", f"Warte auf Handelsbeginn um {fmt_vie(open_utc)} Uhr (Wien)")
            log.info("Warte auf Handelsbeginn (%s Uhr Wien) ...", fmt_vie(open_utc))
            self.clock.sleep_until(open_utc + timedelta(seconds=15))
        self._settle_stale_trades()

        done: set[datetime] = set()
        while True:
            now = self.clock.now()
            latest_be = floor_boundary(now - timedelta(seconds=s.bar_latency_sec))
            if latest_be < first_be:
                self._status(day, "SEARCHING", f"Erste Kerze schließt um {fmt_vie(first_be)} Uhr (Wien)")
                self.clock.sleep_until(first_be + timedelta(seconds=s.bar_latency_sec))
                continue
            if latest_be > last_be:
                if self.store.get_trade(day):
                    return "entered"
                self._status(day, "NO_TRADE", f"Bis {fmt_vie(entry_end)} Uhr kein passendes Signal - kein Trade")
                log.info("Einstiegsfenster vorbei, kein Trade.")
                return "no_trade"
            if latest_be not in done and self._process_bars(day, first_be, latest_be, done) == "entered":
                return "entered"
            self.clock.sleep_until(latest_be + timedelta(minutes=BAR_MIN, seconds=s.bar_latency_sec))

    def _process_bars(self, day: str, first_be: datetime, latest_be: datetime, done: set) -> str:
        try:
            ind = self._fetch_indicators(latest_be)
        except Exception as exc:
            log.error("Marktdaten nicht abrufbar: %s", exc)
            self.store.event("error", "data", f"Marktdaten nicht abrufbar: {exc}")
            done.add(latest_be)  # Verbindungsproblem: auf dieser Kerze wird nicht eingestiegen
            return "error"
        be = first_be
        while be <= latest_be:
            if be not in done:
                done.add(be)
                if self._evaluate(day, ind, be, eligible=(be == latest_be)) == "ENTERED":
                    return "entered"
            be += timedelta(minutes=BAR_MIN)
        return "ok"

    def _fetch_indicators(self, latest_be: datetime) -> pd.DataFrame:
        need = pd.Timestamp(latest_be - timedelta(minutes=BAR_MIN))
        rth = None
        for _ in range(12):
            now = self.clock.now()
            raw = self.data.bars(now - timedelta(days=10), now, BAR_MIN)
            rth = closed_only(filter_rth(raw), now, BAR_MIN)
            if len(rth) and rth.index[-1] >= need:
                break
            self.clock.sleep(5)  # Kerze liegt beim Anbieter noch nicht vor
        return compute_indicators(rth, self.params)

    def _evaluate(self, day: str, ind: pd.DataFrame, be: datetime, eligible: bool) -> str:
        start = be - timedelta(minutes=BAR_MIN)
        base = {"date": day, "bar_start_utc": iso(start), "bar_end_utc": iso(be)}
        ts = pd.Timestamp(start)
        if ts not in ind.index:
            msg = f"{fmt_vie(be)} Uhr: Kerze fehlt in den Daten (keine Umsätze oder Datenlücke)"
            self.store.add_signal(side="NONE", action="MISSING", note=msg, **base)
            self._status(day, "SEARCHING", msg)
            log.warning(msg)
            return "MISSING"
        row = ind.loc[ts]
        sig = evaluate_bar(row)
        action = "NONE"
        if sig.side:
            action = self._try_enter(day, be, row, sig) if eligible else "STALE"
        self.store.add_signal(
            side=sig.side or "NONE", bonus=int(sig.bonus), close=float(row["close"]), sma=float(row["sma"]),
            vwap=float(row["vwap"]), bb_upper=float(row["bb_upper"]), bb_lower=float(row["bb_lower"]),
            stop=sig.stop, action=action, note=sig.reason, **base,
        )
        text = f"{fmt_vie(be)} Uhr: {sig.reason}"
        if action.startswith("SKIPPED"):
            text += f" - Einstieg übersprungen ({action.split(':', 1)[1]})"
        elif action == "STALE":
            text += " - Kerze zu alt für einen Einstieg"
        log.info(text)
        if action != "ENTERED":
            self._status(day, "SEARCHING", text)
        return action

    def _try_enter(self, day: str, be: datetime, row, sig: Signal) -> str:
        s = self.s
        if self.store.get_trade(day):
            return "SKIPPED:es gibt heute schon einen Trade"
        age = (self.clock.now() - be).total_seconds()
        if age > s.max_entry_delay_sec:
            return f"SKIPPED:Signal {age:.0f}s alt (Limit {s.max_entry_delay_sec}s)"
        try:
            last = float(self.data.latest_price())
            equity = self._equity()
            blocked = self._blocked_reason(sig.side) if self.can_trade else None
        except Exception as exc:
            log.error("Verbindungsproblem vor dem Einstieg: %s", exc)
            self.store.event("error", "connection", f"Verbindungsproblem vor dem Einstieg: {exc}")
            return "SKIPPED:Verbindungsproblem, kein Einstieg"
        if blocked:
            return f"SKIPPED:{blocked}"
        close, stop = float(row["close"]), float(sig.stop)
        if abs(last - close) > 0.5 * abs(close - stop):
            return f"SKIPPED:Kurs {last:.2f} weicht zu weit vom Signal {close:.2f} ab"
        entry_exp = last + s.slippage if sig.side == "LONG" else last - s.slippage
        plan = plan_trade(sig.side, entry_exp, stop, s.rr)
        if plan is None:
            return "SKIPPED:Stop-Loss liegt nicht hinter dem Einstieg"
        size = size_position(equity, s.risk_pct, plan, s.max_leverage, s.min_stop_distance)
        if not size.ok:
            return f"SKIPPED:{size.reason}"
        try:
            self._execute(day, be, sig, plan, size, last)
        except Exception as exc:
            log.exception("Order fehlgeschlagen")
            self.store.event("error", "broker", f"Order fehlgeschlagen: {exc}")
            return f"SKIPPED:Order fehlgeschlagen ({exc})"
        return "ENTERED"

    def _execute(self, day: str, be: datetime, sig: Signal, plan: TradePlan, size: Sizing, last: float) -> None:
        s = self.s
        client_id = f"spybot-{day}"  # doppelte Order beim Broker unmöglich: die ID ist pro Tag eindeutig
        fields = dict(
            date=day, mode=self.mode, symbol=s.symbol, side=sig.side, qty=size.qty, status="OPEN",
            signal_bar_utc=iso(be - timedelta(minutes=BAR_MIN)), bonus=int(sig.bonus),
            planned_entry=plan.entry, entry_time_utc=iso(self.clock.now()), entry_price=plan.entry,
            stop_price=plan.stop, take_profit=plan.take_profit, risk_usd=size.risk_usd,
            potential_usd=size.potential_usd, client_order_id=client_id,
        )
        if self.can_trade:
            order = self.broker.order_by_client_id(client_id) or self.broker.place_bracket(
                sig.side, size.qty, plan.stop, plan.take_profit, client_id
            )
            fields["broker_parent_id"] = order["id"]
            trade_id = self.store.create_trade(**fields)
            self.store.log_order(trade_id, "ENTRY", order)
            self._apply_fill(trade_id, sig.side, size.qty, self._wait_fill(order), last)
        else:
            fields["slippage_usd"] = round(s.slippage * size.qty, 2)
            trade_id = self.store.create_trade(**fields)
        self._status(
            day, "ACTIVE",
            f"{sig.side} eröffnet: {size.qty} x {s.symbol}, Stop {plan.stop:.2f}, Ziel {plan.take_profit:.2f}",
        )
        self.store.event("info", "engine", f"{sig.side} {size.qty} x {s.symbol} ({self.mode}), Stop {plan.stop:.2f}, Ziel {plan.take_profit:.2f}")
        log.info("TRADE ERÖFFNET (%s): %s %d x %s, Stop %.2f, Ziel %.2f, Risiko %.2f USD",
                 self.mode, sig.side, size.qty, s.symbol, plan.stop, plan.take_profit, size.risk_usd)
        self._trade_id = trade_id

    def _wait_fill(self, order: dict) -> dict:
        for _ in range(10):
            o = self.broker.order(order["id"])
            if o:
                order = o
                if o["status"] in DONE_STATES:
                    break
            self.clock.sleep(2)
        return order

    def _apply_fill(self, trade_id: int, side: str, qty: int, order: dict, ref: float) -> None:
        if not (order and order.get("filled_avg_price")):
            return
        fill = float(order["filled_avg_price"])
        fqty = int(order["filled_qty"] or qty)
        slip = (fill - ref) if side == "LONG" else (ref - fill)
        upd = dict(entry_price=fill, qty=fqty, slippage_usd=round(slip * fqty, 2))
        if order.get("filled_at"):
            upd["entry_time_utc"] = iso(order["filled_at"])
        self.store.update_trade(trade_id, **upd)

    # ------------------------------------------------------------------ Zwangsschluss / Abrechnung
    def run_close(self) -> str:
        s = self.s
        now = self.clock.now()
        d = ny_date(now)
        day = d.isoformat()
        sess = self._session(d)
        if sess is None:
            return "closed"
        self._pick_mode()
        self._settle_stale_trades()
        trade = self.store.get_trade(day)
        if trade is None and self.can_trade:
            trade = self._adopt_orphan(day)
        if trade is None:
            return self._finish_without_trade(day, sess)
        if trade["status"] == "CLOSED":
            return "already_closed"
        force_at = sess.force_close_at(s.force_close_min).astimezone(UTC)
        if now < force_at - timedelta(minutes=40):
            log.info("Zu früh: Zwangsschluss um %s Uhr (Wien) - beende mich.", fmt_vie(force_at))
            return "too_early"
        log.info("Überwache offenen Trade bis zum Zwangsschluss um %s Uhr (Wien).", fmt_vie(force_at))
        while True:
            now = self.clock.now()
            trade = self.store.get_trade(day)
            try:
                if self._check_trade(trade, force_at, now):
                    return "closed"
            except Exception as exc:
                log.exception("Trade-Prüfung fehlgeschlagen")
                self.store.event("error", "engine", f"Trade-Prüfung fehlgeschlagen: {exc}")
            if now > sess.close.astimezone(UTC) + timedelta(minutes=15):
                self.store.event("error", "engine", "Trade nach Börsenschluss noch offen - Abrechnung beim nächsten Lauf")
                return "timeout"
            self.clock.sleep(POLL_SEC)

    def _finish_without_trade(self, day: str, sess: Session) -> str:
        row = self.store.get_day(day)
        if row and row["status"] in FINAL:
            return "no_trade"
        if self.clock.now() >= sess.entry_end(self.s.window_minutes):
            note = "Kein Einstieg im Zeitfenster" + ("" if row else " (in diesem Fenster lief kein Bot)")
            self._status(day, "NO_TRADE", note, session_open_utc=iso(sess.open), session_close_utc=iso(sess.close))
        return "no_trade"

    def _check_trade(self, t: dict, force_at: datetime, now: datetime) -> bool:
        if t["mode"] == "analysis":
            res = self._simulate(t, force_at, now)
            if res.reason == "OPEN":
                return False
            self._finalize(t, res.price, res.time, res.reason, res.slip_per_share)
            return True
        pos = self.broker.position()
        if pos is None:
            fill = self.broker.exit_fill(t["side"], self._entry_dt(t))
            if fill:
                self._finalize(t, fill["price"], fill["time"], fill["reason"])
                return True
            return self._void_if_unfilled(t)
        if now >= force_at:
            fill = self._force_close(t)
            if fill:
                self._finalize(t, fill["price"], fill["time"], "FORCED")
                return True
        return False

    def _simulate(self, t: dict, force_at: datetime | None, now: datetime) -> ExitResult:
        start = self._entry_dt(t)
        end = now if force_at is None else min(now, force_at + timedelta(minutes=2))
        m1 = filter_rth(self.data.bars(start - timedelta(minutes=1), end, 1))
        return simulate_exit(m1, t["side"], t["entry_price"], t["stop_price"], t["take_profit"], start, force_at, self.s.slippage, now)

    def _force_close(self, t: dict) -> dict | None:
        """Offene Ausstiegs-Orders stornieren, Position per Market schließen, Fill abwarten."""
        log.warning("ZWANGSSCHLUSS: schließe %s-Position.", t["side"])
        try:
            if t.get("broker_parent_id"):
                self.broker.cancel_legs(t["broker_parent_id"])
        except Exception as exc:
            log.warning("Legs nicht stornierbar: %s", exc)
        order = None
        for attempt in range(6):
            if self.broker.position() is None:
                break  # inzwischen durch SL/TP geschlossen
            try:
                order = self.broker.close_position()
                break
            except Exception as exc:
                log.warning("Schließen fehlgeschlagen (%d/6): %s", attempt + 1, exc)
                self.clock.sleep(3)
        if order is None:
            return self.broker.exit_fill(t["side"], self._entry_dt(t))
        self.store.log_order(t["id"], "CLOSE", order)
        for _ in range(15):
            o = self.broker.order(order["id"])
            if o and o["status"] == "filled" and o["filled_avg_price"]:
                return {"price": o["filled_avg_price"], "time": o["filled_at"] or self.clock.now(), "reason": "FORCED"}
            self.clock.sleep(2)
        return None

    def _void_if_unfilled(self, t: dict) -> bool:
        o = self.broker.order(t["broker_parent_id"]) if t.get("broker_parent_id") else None
        if o and o["status"] in ("canceled", "rejected", "expired"):
            self.store.update_trade(t["id"], status="CLOSED", exit_reason="NOT_FILLED", pnl_usd=0.0)
            self.store.set_day(t["date"], "NO_TRADE", f"Order nicht ausgeführt ({o['status']})", force=True, mode=t["mode"])
            return True
        return False

    def _finalize(self, t: dict, exit_price: float, exit_time, reason: str, exit_slip: float = 0.0) -> None:
        qty, entry = int(t["qty"]), float(t["entry_price"])
        pnl = trade_pnl(t["side"], entry, float(exit_price), qty, self.s.fee_per_share)
        slip_total = round(float(t["slippage_usd"] or 0.0) + exit_slip * qty, 2)
        self.store.update_trade(
            t["id"], status="CLOSED", exit_time_utc=iso(exit_time) if isinstance(exit_time, datetime) else utcnow_iso(),
            exit_price=round(float(exit_price), 4), exit_reason=reason, pnl_usd=pnl,
            fees_usd=round(self.s.fee_per_share * qty * 2, 4), slippage_usd=slip_total, fx_usd_eur=float(self._fx()),
        )
        status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "CLOSED"
        text = f"{REASON_TEXT.get(reason, reason)}: {pnl:+.2f} USD"
        self.store.set_day(t["date"], status, text, force=True, mode=t["mode"])
        self.store.event("info", "engine", f"Trade {t['date']} abgeschlossen ({reason}): {pnl:+.2f} USD")
        log.info("TRADE ABGESCHLOSSEN: %s", text)
        try:
            eq = float(self.broker.account()["equity"]) if t["mode"] != "analysis" else self.s.start_balance + self.store.realized_pnl_usd()
            self.store.meta_set("last_equity_usd", eq)
        except Exception:
            pass

    def _settle_stale_trades(self) -> None:
        """Trades früherer Tage, die noch OPEN sind (z. B. Zwangsschluss verpasst), sauber abrechnen."""
        today = ny_date(self.clock.now()).isoformat()
        for t in self.store.open_trades():
            if t["date"] >= today:
                continue
            try:
                self._settle_one_stale(t)
            except Exception as exc:
                log.exception("Altlast-Trade nicht abrechenbar")
                self.store.event("error", "engine", f"Alter Trade {t['date']} nicht abrechenbar: {exc}")

    def _settle_one_stale(self, t: dict) -> None:
        now = self.clock.now()
        old = self._session(date.fromisoformat(t["date"]))
        force_at = old.force_close_at(self.s.force_close_min) if old else None
        if t["mode"] == "analysis":
            res = self._simulate(t, force_at, now)
            if res.reason != "OPEN":
                self._finalize(t, res.price, res.time, res.reason, res.slip_per_share)
            return
        if self.broker.position() is None:
            fill = self.broker.exit_fill(t["side"], self._entry_dt(t))
            if fill:
                self._finalize(t, fill["price"], fill["time"], fill["reason"])
            else:
                self._void_if_unfilled(t)
            return
        today = self._session(ny_date(now))
        if today and today.open <= now < today.close:
            self.store.event("error", "engine", f"Position von {t['date']} war über Nacht offen - wird jetzt geschlossen")
            fill = self._force_close(t)
            if fill:
                self._finalize(t, fill["price"], fill["time"], "OVERNIGHT")

    def _adopt_orphan(self, day: str) -> dict | None:
        """Order existiert beim Broker, aber nicht in der Datenbank (Absturz nach dem Senden): übernehmen."""
        o = self.broker.order_by_client_id(f"spybot-{day}")
        if not o:
            return None
        full = self.broker.order(o["id"]) or o
        legs = full.get("legs") or []
        tp = next((x["limit_price"] for x in legs if x["type"] == "limit"), None)
        sl = next((x["stop_price"] for x in legs if x["type"] in ("stop", "stop_limit")), None)
        entry = float(full.get("filled_avg_price") or 0.0)
        if not entry:
            return None
        now = utcnow_iso()
        tid = self.store.create_trade(
            date=day, mode=self.mode, symbol=self.s.symbol, side="LONG" if full["side"] == "buy" else "SHORT",
            qty=int(full.get("filled_qty") or full.get("qty") or 0), status="OPEN", planned_entry=entry,
            entry_time_utc=iso(full["filled_at"]) if full.get("filled_at") else now, entry_price=entry,
            stop_price=sl if sl is not None else entry, take_profit=tp if tp is not None else entry,
            client_order_id=full["client_order_id"], broker_parent_id=full["id"],
        )
        self.store.event("warn", "engine", f"Order {full['client_order_id']} beim Broker gefunden und in die Datenbank übernommen")
        self._status(day, "ACTIVE", "Trade beim Broker gefunden und übernommen")
        return self.store.get_trade(day)
