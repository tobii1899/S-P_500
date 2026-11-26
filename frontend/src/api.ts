import type { Backtest, State } from "./types";

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `Fehler ${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail ?? msg;
    } catch {
      /* keine JSON-Antwort */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const getState = () => fetch("/api/state").then((r) => parse<State>(r));
export const syncState = () => fetch("/api/sync", { method: "POST" }).then((r) => parse<{ message: string }>(r));
export const runBacktest = (months: number) =>
  fetch("/api/backtest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ months }) }).then((r) =>
    parse<Backtest>(r),
  );
