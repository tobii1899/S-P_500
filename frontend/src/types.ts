export type DayStatus = "PENDING" | "SEARCHING" | "ACTIVE" | "WIN" | "LOSS" | "NO_TRADE" | "CLOSED";

export interface TradeInfo {
  date: string;
  mode: string;
  side: "LONG" | "SHORT";
  qty: number;
  status: "OPEN" | "CLOSED";
  entry_price: number | null;
  stop_price: number;
  take_profit: number;
  entry_time_utc: string | null;
  exit_time_utc: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  bonus: number | null;
  risk_eur: number;
  potential_eur: number;
  pnl_eur: number | null;
  current_price: number | null;
}

export interface Evaluation {
  bar_end_utc: string;
  close: number | null;
  sma: number | null;
  vwap: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
  side: string;
  note: string | null;
  action: string | null;
}

export interface HistoryRow {
  date: string;
  status: DayStatus;
  side: string | null;
  entry: number | null;
  exit: number | null;
  pnl_eur: number | null;
  reason: string | null;
  exit_reason: string | null;
}

export interface Stats {
  trades: number;
  wins: number;
  losses: number;
  winrate: number;
  total_pnl: number;
  total_return_pct: number | null;
  avg_win: number;
  avg_loss: number;
  profit_factor: number | null;
}

export interface State {
  server_time: string;
  mode: "paper" | "live" | "analysis";
  mode_label: string;
  market: { status: "open" | "pre" | "closed"; label: string; open: string | null; close: string | null; entry_end: string | null; force_close_at: string | null };
  broker: { connected: boolean; detail: string };
  account: { equity_eur: number | null; equity_usd: number | null; fx: number; source: string };
  today: { date: string; status: DayStatus; reason: string };
  trade: TradeInfo | null;
  evaluation: Evaluation | null;
  stats: Stats;
  curve: number[];
  history: HistoryRow[];
  events: { ts_utc: string; level: string; source: string; message: string }[];
  live: boolean;
  risk: { risk_pct: number; max_leverage: number };
}

export interface Backtest {
  period: { start: string; end: string; trading_days: number };
  settings: { risk_pct: number; max_leverage: number; slippage: number; fee_per_share: number; start_balance: number; feed: string };
  metrics: Stats & { max_drawdown_pct: number; no_trade_days: number; end_balance: number };
  monthly: { month: string; pnl_usd: number; trades: number; return_pct: number }[];
  fx: number;
}
