import { useCallback, useEffect, useState } from "react";
import { Moon, RefreshCw, Sun } from "lucide-react";
import { getState, syncState } from "./api";
import type { State } from "./types";
import Hero from "./components/Hero";
import TradePanel from "./components/Trade";
import Stats from "./components/Stats";
import History from "./components/History";
import BacktestPanel from "./components/Backtest";

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* Speicher nicht verfügbar */
    }
    setDark(next);
  };
  return [dark, toggle];
}

export default function App() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [dark, toggleTheme] = useTheme();

  const load = useCallback(async () => {
    try {
      setState(await getState());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const sync = async () => {
    setBusy(true);
    setNotice(null);
    try {
      setNotice((await syncState()).message);
      await load();
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-[980px] space-y-5 px-5 py-8 sm:py-12">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-[22px] font-semibold tracking-tight">SPY Bot</p>
          <p className="label">Regelbasiert · SMA 9 · VWAP · Bollinger 20/2 · 5 Min</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" onClick={sync} disabled={busy} title="Aktuellen Bot-Zustand vom GitHub-Branch bot-data laden">
            <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            <span className="hidden sm:inline">Von GitHub laden</span>
          </button>
          <button className="btn px-3" onClick={toggleTheme} aria-label="Hell/Dunkel umschalten">
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </header>

      {notice && <p className="rounded-xl bg-soft px-4 py-3 text-[14px] text-muted">{notice}</p>}
      {error && <p className="rounded-xl bg-soft px-4 py-3 text-[14px] text-loss">Backend nicht erreichbar: {error}</p>}

      {state ? (
        <>
          <Hero s={state} now={now} />
          <TradePanel s={state} now={now} />
          <Stats s={state} />
          <History s={state} />
          <BacktestPanel />
          {state.events.some((e) => e.level !== "info") && (
            <p className="px-1 text-[13px] text-muted">
              Letzte Meldung: {state.events.find((e) => e.level !== "info")?.message}
            </p>
          )}
        </>
      ) : (
        !error && <p className="label">Lade …</p>
      )}
    </main>
  );
}
