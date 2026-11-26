"""Kommandozeile: Verdrahtung der GitHub-Läufe (State-Datei, Zusammenfassung) und die Prüfung."""
import json
from argparse import Namespace
from datetime import date

import pandas as pd

from backend import cli
from backend.config import Settings
from backend.db import Store
from tests.helpers import FakeBroker, FakeClock, FakeData, build_history, ny

D = date(2026, 11, 25)


def make_env(monkeypatch, tmp_path, kind="up", start=(9, 20)):
    df, sessions = build_history([(date(2026, 11, 23), "flat"), (date(2026, 11, 24), "flat"), (D, kind)])
    clock = FakeClock(ny(D, *start))
    data = FakeData(df, clock)
    broker = FakeBroker(data, clock, sessions)
    s = Settings(api_key="PKTEST", secret_key="geheim", mode="paper", state_dir=tmp_path / "state")
    monkeypatch.setattr(cli, "load_settings", lambda: s)
    monkeypatch.setattr(cli, "AlpacaData", lambda _s: data)
    monkeypatch.setattr(cli, "AlpacaBroker", lambda _s: broker)
    monkeypatch.setattr(cli, "get_usd_eur", lambda _s: 0.9)
    monkeypatch.setattr("backend.engine.SystemClock", lambda: clock)
    return s, clock, broker


def test_run_session_writes_state_status_and_github_summary(monkeypatch, tmp_path):
    s, clock, broker = make_env(monkeypatch, tmp_path)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.cmd_run(Namespace(cmd="run-session", state_dir=None)) == 0
    store = Store(s.state_dir / "trading.db")
    assert store.get_trade("2026-11-25")["side"] == "LONG"
    assert store.meta_get("last_outcome") == "entered" and store.meta_get("last_run_utc")
    status = json.loads((s.state_dir / "status.json").read_text(encoding="utf-8"))
    assert status["today"]["status"] == "ACTIVE" and status["trade"]["side"] == "LONG"
    text = summary.read_text(encoding="utf-8")
    assert "entered" in text and "LONG" in text
    assert "geheim" not in text and "geheim" not in json.dumps(status)           # Secret taucht nirgends auf


def test_run_close_after_session(monkeypatch, tmp_path):
    s, clock, broker = make_env(monkeypatch, tmp_path, kind="up_then_flat")
    assert cli.cmd_run(Namespace(cmd="run-session", state_dir=None)) == 0
    clock.t = ny(D, 15, 30)
    assert cli.cmd_run(Namespace(cmd="run-close", state_dir=None)) == 0
    store = Store(s.state_dir / "trading.db")
    assert store.get_trade("2026-11-25")["exit_reason"] == "FORCED"
    assert json.loads((s.state_dir / "status.json").read_text(encoding="utf-8"))["today"]["status"] in ("WIN", "LOSS", "CLOSED")


def test_failed_run_is_logged_and_returns_error(monkeypatch, tmp_path):
    s, clock, broker = make_env(monkeypatch, tmp_path)

    def boom(day):
        raise ConnectionError("Alpaca down")

    broker.session_for = boom
    assert cli.cmd_run(Namespace(cmd="run-session", state_dir=None)) == 1          # roter Lauf in GitHub -> E-Mail an dich
    store = Store(s.state_dir / "trading.db")
    assert any("abgebrochen" in e["message"] for e in store.recent_events(10))
    assert (s.state_dir / "status.json").exists()                                    # Zustand wird trotzdem geschrieben


def test_check_command_reports_ready(monkeypatch, tmp_path, capsys):
    s, clock, broker = make_env(monkeypatch, tmp_path)
    clock.t = ny(D, 8, 0)
    broker.account = lambda: {"equity": 100000.0, "cash": 100000.0, "buying_power": 400000.0, "status": "ACTIVE", "currency": "USD",
                              "trading_blocked": False, "account_blocked": False, "shorting_enabled": True, "multiplier": "4"}
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: clock.now())}))
    assert cli.cmd_check(Namespace(state_dir=None)) == 0
    out = capsys.readouterr().out
    assert "Alles bereit" in out and "Einstiegsfenster bis 17:00" in out and "Zwangsschluss 21:55" in out
    assert "geheim" not in out


def test_check_without_keys_fails_clearly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(api_key="PK", secret_key="HIER_SECRET_KEY_EINTRAGEN"))
    assert cli.cmd_check(Namespace(state_dir=None)) == 1
    assert "Keine Alpaca-Keys" in capsys.readouterr().out
