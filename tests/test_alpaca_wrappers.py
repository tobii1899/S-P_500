"""Alpaca-Wrapper ohne Netzwerk: Requests werden korrekt gebaut, echte alpaca-py-Modelle korrekt gelesen."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.models import Order

from backend.broker import AlpacaBroker, order_dict
from backend.config import Settings
from backend.marketdata import AlpacaData, normalize_bars


def order_json(**kw):
    base = {
        "id": "61e69015-8549-4bfd-b9c3-01e75843f47d", "client_order_id": "spybot-2026-11-25",
        "created_at": "2026-11-25T14:35:08.123Z", "updated_at": "2026-11-25T14:35:08.200Z",
        "submitted_at": "2026-11-25T14:35:08.123Z", "filled_at": "2026-11-25T14:35:08.150Z",
        "expired_at": None, "canceled_at": None, "failed_at": None, "replaced_at": None, "replaced_by": None, "replaces": None,
        "asset_id": "b28f4066-5c6d-479b-a2af-85dc1a8f16fb", "symbol": "SPY", "asset_class": "us_equity",
        "notional": None, "qty": "294", "filled_qty": "294", "filled_avg_price": "680.25",
        "order_class": "simple", "order_type": "market", "type": "market", "side": "buy", "time_in_force": "gtc",
        "limit_price": None, "stop_price": None, "status": "filled", "extended_hours": False, "legs": None,
        "trail_percent": None, "trail_price": None, "hwm": None,
    }
    base.update(kw)
    return base


def make_broker(client):
    b = AlpacaBroker(Settings(api_key="k", secret_key="s"))
    b.client = client
    return b


def test_order_dict_reads_real_alpaca_models_including_legs():
    tp = order_json(id="11111111-8549-4bfd-b9c3-01e75843f47d", client_order_id="leg-tp", order_type="limit", type="limit",
                    side="sell", limit_price="680.98", status="new", filled_qty="0", filled_avg_price=None, filled_at=None)
    sl = order_json(id="22222222-8549-4bfd-b9c3-01e75843f47d", client_order_id="leg-sl", order_type="stop", type="stop",
                    side="sell", stop_price="679.90", status="held", filled_qty="0", filled_avg_price=None, filled_at=None)
    parent = Order(**order_json(order_class="bracket", legs=[tp, sl]))
    d = order_dict(parent)
    assert d["status"] == "filled" and d["side"] == "buy" and d["type"] == "market"
    assert d["filled_avg_price"] == 680.25 and d["filled_qty"] == 294.0 and d["filled_at"].year == 2026
    assert [(x["type"], x["limit_price"], x["stop_price"]) for x in d["legs"]] == [("limit", 680.98, None), ("stop", None, 679.90)]


def test_place_bracket_builds_valid_gtc_bracket_request():
    sent = []
    broker = make_broker(SimpleNamespace(submit_order=lambda req: sent.append(req) or Order(**order_json(order_class="bracket"))))
    out = broker.place_bracket("SHORT", 294, stop=680.10, take_profit=679.02, client_id="spybot-2026-11-25")
    req = sent[0]
    assert req.symbol == "SPY" and req.qty == 294 and req.side == OrderSide.SELL
    assert req.order_class == OrderClass.BRACKET and req.time_in_force == TimeInForce.GTC      # Legs bleiben über Nacht geschützt
    assert req.take_profit.limit_price == 679.02 and req.stop_loss.stop_price == 680.10
    assert req.client_order_id == "spybot-2026-11-25" and out["id"].startswith("61e69015")


def test_exit_fill_picks_filled_opposite_order_and_classifies_reason():
    entry = Order(**order_json())
    tp = Order(**order_json(id="11111111-8549-4bfd-b9c3-01e75843f47d", order_type="limit", type="limit", side="sell",
                            limit_price="680.98", filled_avg_price="680.98", filled_at="2026-11-25T14:49:30Z"))
    sl = Order(**order_json(id="22222222-8549-4bfd-b9c3-01e75843f47d", order_type="stop", type="stop", side="sell",
                            stop_price="679.90", status="canceled", filled_qty="0", filled_avg_price=None, filled_at=None))
    broker = make_broker(SimpleNamespace(get_orders=lambda req: [entry, tp, sl]))
    fill = broker.exit_fill("LONG", datetime(2026, 11, 25, 14, 35, tzinfo=timezone.utc))
    assert fill["reason"] == "TP" and fill["price"] == 680.98
    forced = Order(**order_json(id="33333333-8549-4bfd-b9c3-01e75843f47d", side="sell", filled_avg_price="680.30", filled_at="2026-11-25T20:55:02Z"))
    broker = make_broker(SimpleNamespace(get_orders=lambda req: [entry, forced]))
    assert broker.exit_fill("LONG", datetime(2026, 11, 25, 14, 35, tzinfo=timezone.utc))["reason"] == "FORCED"
    broker = make_broker(SimpleNamespace(get_orders=lambda req: [entry]))
    assert broker.exit_fill("LONG", datetime(2026, 11, 25, 14, 35, tzinfo=timezone.utc)) is None


def test_bars_request_uses_iex_feed_and_multiindex_result_is_normalized():
    idx = pd.MultiIndex.from_tuples(
        [("SPY", pd.Timestamp("2026-11-25 14:30", tz="UTC")), ("SPY", pd.Timestamp("2026-11-25 14:35", tz="UTC"))],
        names=["symbol", "timestamp"])
    raw = pd.DataFrame({"open": [680.0, 680.2], "high": [680.5, 680.6], "low": [679.9, 680.1], "close": [680.2, 680.4],
                        "volume": [1000.0, 900.0], "trade_count": [10.0, 9.0], "vwap": [680.1, 680.3]}, index=idx)
    seen = []
    data = AlpacaData.__new__(AlpacaData)
    data.s, data.feed = Settings(api_key="k", secret_key="s"), DataFeed.IEX
    data.client = SimpleNamespace(get_stock_bars=lambda req: seen.append(req) or SimpleNamespace(df=raw))
    df = data.bars(datetime(2026, 11, 25, tzinfo=timezone.utc), datetime(2026, 11, 26, tzinfo=timezone.utc), 5)
    assert seen[0].feed == DataFeed.IEX and seen[0].adjustment == Adjustment.RAW
    assert seen[0].timeframe.amount == 5 and list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC" and len(df) == 2


def test_empty_bars_are_handled():
    assert normalize_bars(pd.DataFrame(), "SPY").empty
