"""SQLite: einzige Quelle der Wahrheit. Ein Lauf liest den Zustand, handelt und schreibt ihn zurück."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FINAL = ("WIN", "LOSS", "NO_TRADE", "CLOSED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
  date TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  reason TEXT,
  mode TEXT,
  session_open_utc TEXT,
  session_close_utc TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  bar_start_utc TEXT NOT NULL,
  bar_end_utc TEXT NOT NULL,
  side TEXT NOT NULL,
  bonus INTEGER NOT NULL DEFAULT 0,
  close REAL, sma REAL, vwap REAL, bb_upper REAL, bb_lower REAL, stop REAL,
  action TEXT, note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(date, bar_start_utc)
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL UNIQUE,
  mode TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  status TEXT NOT NULL,
  signal_bar_utc TEXT,
  bonus INTEGER DEFAULT 0,
  planned_entry REAL,
  entry_time_utc TEXT,
  entry_price REAL,
  stop_price REAL NOT NULL,
  take_profit REAL NOT NULL,
  risk_usd REAL,
  potential_usd REAL,
  exit_time_utc TEXT,
  exit_price REAL,
  exit_reason TEXT,
  pnl_usd REAL,
  fees_usd REAL DEFAULT 0,
  slippage_usd REAL DEFAULT 0,
  fx_usd_eur REAL,
  client_order_id TEXT UNIQUE,
  broker_parent_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER,
  role TEXT NOT NULL,
  broker_order_id TEXT,
  status TEXT,
  details TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  level TEXT NOT NULL,
  source TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE")  # eine einzige Datei -> sauber in Git ablegbar
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- Hilfen ----------
    def _one(self, sql: str, args=()) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _write(self, sql: str, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # ---------- Tage ----------
    def get_day(self, d: str) -> dict | None:
        return self._one("SELECT * FROM days WHERE date=?", (d,))

    def set_day(self, d: str, status: str, reason: str | None = None, force: bool = False, **extra) -> None:
        cur = self.get_day(d)
        if cur and cur["status"] in FINAL and status not in FINAL and not force:
            return  # ein abgeschlossener Tag wird nie wieder geöffnet
        fields = {"status": status, "updated_at": utcnow_iso(), **extra}
        if reason is not None:
            fields["reason"] = reason
        if cur:
            cols = ", ".join(f"{k}=?" for k in fields)
            self._write(f"UPDATE days SET {cols} WHERE date=?", (*fields.values(), d))
        else:
            fields["date"] = d
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            self._write(f"INSERT INTO days ({cols}) VALUES ({marks})", tuple(fields.values()))

    # ---------- Signale ----------
    def add_signal(self, **f) -> None:
        f.setdefault("created_at", utcnow_iso())
        cols = ", ".join(f)
        marks = ", ".join("?" for _ in f)
        self._write(f"INSERT OR IGNORE INTO signals ({cols}) VALUES ({marks})", tuple(f.values()))

    def last_signal(self, d: str) -> dict | None:
        return self._one("SELECT * FROM signals WHERE date=? ORDER BY bar_start_utc DESC LIMIT 1", (d,))

    # ---------- Trades ----------
    def create_trade(self, **f) -> int:
        now = utcnow_iso()
        f.setdefault("created_at", now)
        f.setdefault("updated_at", now)
        cols = ", ".join(f)
        marks = ", ".join("?" for _ in f)
        return int(self._write(f"INSERT INTO trades ({cols}) VALUES ({marks})", tuple(f.values())).lastrowid)

    def update_trade(self, trade_id: int, **f) -> None:
        f["updated_at"] = utcnow_iso()
        cols = ", ".join(f"{k}=?" for k in f)
        self._write(f"UPDATE trades SET {cols} WHERE id=?", (*f.values(), trade_id))

    def get_trade(self, d: str) -> dict | None:
        return self._one("SELECT * FROM trades WHERE date=?", (d,))

    def open_trades(self) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE status='OPEN' ORDER BY date")

    def closed_trades(self) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE status='CLOSED' ORDER BY date")

    def realized_pnl_usd(self) -> float:
        row = self._one("SELECT COALESCE(SUM(pnl_usd), 0) AS s FROM trades WHERE status='CLOSED'")
        return float(row["s"]) if row else 0.0

    def history(self, limit: int = 60) -> list[dict]:
        return self._all(
            "SELECT d.date, d.status, d.reason, t.side, t.qty, t.entry_price, t.exit_price, t.pnl_usd, "
            "t.fx_usd_eur, t.exit_reason, t.mode AS trade_mode "
            "FROM days d LEFT JOIN trades t ON t.date = d.date ORDER BY d.date DESC LIMIT ?",
            (limit,),
        )

    # ---------- Orders / Events / Meta ----------
    def log_order(self, trade_id: int | None, role: str, order: dict | None) -> None:
        if not order:
            return
        details = json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in order.items() if k != "legs"})
        self._write(
            "INSERT INTO orders (trade_id, role, broker_order_id, status, details, created_at) VALUES (?,?,?,?,?,?)",
            (trade_id, role, order.get("id"), order.get("status"), details, utcnow_iso()),
        )

    def event(self, level: str, source: str, message: str) -> None:
        self._write("INSERT INTO events (ts_utc, level, source, message) VALUES (?,?,?,?)", (utcnow_iso(), level, source, message[:500]))

    def recent_events(self, n: int = 10) -> list[dict]:
        return self._all("SELECT ts_utc, level, source, message FROM events ORDER BY id DESC LIMIT ?", (n,))

    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value) -> None:
        self._write("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
