# SPY Bot – regelbasierter S&P-500-Bot (Alpaca · GitHub Actions)

Feste Regeln, keine KI-Entscheidungen. Der Bot handelt **SPY** auf der 5-Minuten-Kerze mit SMA 9, Session-VWAP und
Bollinger-Bändern 20/2. Er läuft **auf GitHub** (ohne dass dein Rechner an ist), standardmäßig im **Paper-Trading**.
Das Dashboard läuft lokal und zeigt den Stand.

## Live schalten (Windows, ca. 5 Minuten)

1. ZIP entpacken, im Ordner PowerShell öffnen.
2. `powershell -ExecutionPolicy Bypass -File scripts\deploy_github.ps1`
3. Das Skript fragt nach deinem **Alpaca Secret Key** (Key-ID steht schon in `.env`), meldet dich bei GitHub an,
   legt ein **privates** Repo an, hinterlegt die Keys als verschlüsselte Secrets, lädt den Code hoch und startet einen
   Prüf-Lauf (Tests, Konto, SPY-Handelbarkeit, Marktdaten, Börsenzeiten). Danach läuft alles automatisch Mo–Fr.

Ohne Skript (manuell): Repo anlegen, Code pushen, unter *Settings → Secrets and variables → Actions* die Secrets
`ALPACA_API_KEY` und `ALPACA_SECRET_KEY` anlegen, dann *Actions → Check → Run workflow*.

## Tagesablauf (Wiener Zeit, Winter)

| Zeit | Was passiert |
|---|---|
| 15:20 | GitHub startet die Einstiegssuche (Ersatzläufe 15:50, 16:20 …, doppelt handeln ist ausgeschlossen) |
| 15:30 | New-York-Open (VWAP startet) |
| 15:35 – 16:55 | jede **abgeschlossene** 5-Min-Kerze wird geprüft; erstes gültiges Signal → 1 Trade, Stop/Ziel liegen beim Broker |
| ab 17:00 | keine neuen Trades (Kerze mit Schluss 17:00 zählt nicht mehr) |
| 21:30 | Zwangsschluss-Job startet (25 Minuten Puffer) |
| 21:55 | offene Position wird geschlossen (an verkürzten Tagen 5 Min vor Börsenschluss) |
| danach | Abrechnung, Zustand wird im Branch `bot-data` gespeichert |

Sommer-/Winterzeit, Feiertage und verkürzte Handelstage kommen aus dem Alpaca-Börsenkalender, nie aus festen UTC-Zeiten.
In den Wochen, in denen USA und Europa die Uhr zu verschiedenen Terminen umstellen, verschiebt sich die Wiener Anzeige
um eine Stunde (Open dann 14:30) – die Regeln bleiben relativ zur Börse gleich.

## Die Regeln, wie sie umgesetzt sind

| | LONG | SHORT |
|---|---|---|
| Pflicht | Close > SMA 9 **und** Close > Session-VWAP | Close < SMA 9 **und** Close < Session-VWAP |
| Bonus (nur protokolliert, kein Muss) | Close > oberes Band | Close < unteres Band |
| Stop-Loss | unteres Band der Signalkerze | oberes Band der Signalkerze |
| Take-Profit | Einstieg + 2 × Stop-Abstand | Einstieg − 2 × Stop-Abstand |

Weitere feste Regeln: höchstens **1 Trade pro Tag**, kein zweiter Trade nach Gewinn oder Verlust, Positionsgröße aus
Risiko ÷ Stop-Abstand, kein Trade bei Verbindungsproblemen oder wenn die Größe nicht sinnvoll berechenbar ist.

### Annahmen, die ich getroffen habe (bitte prüfen)

- **Indikatoren wie im Chart:** SMA und Bollinger laufen durchgehend über die Vortage (Warm-up), Standardabweichung als
  Populations-Abweichung (wie TradingView); der VWAP (Typical Price) startet jeden Tag neu. Gleichstand = kein Signal.
- **Einstieg:** Market-Order direkt nach Kerzenschluss. Nur die **neueste** Kerze darf einen Einstieg auslösen und nur,
  wenn sie höchstens 150 s alt ist (verspäteter Start → ältere Signale werden nur protokolliert). Weicht der Kurs mehr als
  ½ Stop-Abstand vom Signal ab, wird übersprungen.
- **Stop/Ziel** liegen als Bracket-Order (GTC) beim Broker: fällt der Zwangsschluss aus, bleibt die Position geschützt.
- **Risiko:** 0,5 % pro Trade, höchstens 2× Hebel. Bei engen Bändern begrenzt der Hebel – das tatsächliche Risiko steht je Trade
  in der Datenbank (`risk_usd`).
- **Daten:** Alpaca IEX (gratis, Echtzeit). Das ist ein Teil des Gesamtmarkts – Kurse/Volumen können leicht von
  TradingView abweichen und damit auch Signale. `DATA_FEED=sip` braucht ein Alpaca-Datenabo.
- **Euro:** Konto und Trades laufen in USD; EUR = USD × Tageskurs (frankfurter.dev, Fallback konfigurierbar).
- **Fällt der Broker aus,** läuft der Tag im **Analysemodus** (virtuelle Trades, ausgewertet mit 1-Min-Kerzen; berühren
  Stop und Ziel dieselbe Kerze, gilt konservativ der Stop).

## Lokal: Dashboard, Backtest, Tests

```powershell
scripts\start.ps1                                   # Dashboard auf http://127.0.0.1:8000 (zieht den Stand von GitHub)
.\.venv\Scripts\python -m backend.cli backtest --months 6     # 3, 6 oder 12 Monate; auch im Dashboard per Knopf
.\.venv\Scripts\python -m backend.cli check         # Konto, Zeiten, Daten prüfen
.\.venv\Scripts\python -m pytest                    # 89 Tests (LONG, SHORT, kein Trade, Zeiten, Neustart, Backtest …)
```

Python 3.11–3.13 nötig (nur für lokal; Node.js nur, wenn du das Dashboard ändern willst – es liegt fertig gebaut in `frontend/dist`;
neu bauen mit `cd frontend && npm install && npm run build`). **Lokal handelt nichts** – der Bot läuft ausschließlich auf GitHub,
sonst gäbe es zwei Bots auf demselben Konto.

Backtest: Einstieg am Open der Minute nach Kerzenschluss (kein Look-ahead), Slippage 0,01 USD/Aktie gegen den Trade,
Gebühren konfigurierbar, Zwangsschluss wie live, Monatsperformance, Drawdown, Tage ohne Trade.

## Einstellungen

Lokal in `.env`, auf GitHub als *Variables* (Settings → Secrets and variables → Actions → Variables):

| Name | Standard | Bedeutung |
|---|---|---|
| `BOT_MODE` | `paper` | `paper`, `analysis` (nur virtuell), `live` (echtes Geld, siehe unten) |
| `RISK_PER_TRADE_PCT` | `0.5` | Risiko pro Trade in % vom Kontostand |
| `MAX_LEVERAGE` | `2` | Positionswert höchstens x-faches Kapital |
| `DATA_FEED` | `iex` | `iex` gratis, `sip` mit Abo |
| `SLIPPAGE_PER_SHARE` / `FEE_PER_SHARE` | `0.01` / `0` | Kosten in Analyse und Backtest |

**Live-Handel** ist doppelt gesichert: `BOT_MODE=live` **und** `LIVE_CONFIRM=JA_ECHTES_GELD` (plus Live-Keys). Ohne beides
verweigert der Bot den Start. Erst nach Wochen im Paper-Modus ernsthaft darüber nachdenken.

## Wie der Bot auf GitHub läuft

- Drei Workflows: **Einstiegssuche** (`session.yml`), **Zwangsschluss + Abrechnung** (`close.yml`), **Check** (manuell).
- Zustand = eine SQLite-Datei im Branch `bot-data` (jeder Handelstag, Signal, Trade, Order-ID, Fehler). Jeder Lauf lädt sie, arbeitet, speichert sie.
  Nach jedem Lauf zeigt GitHub eine Zusammenfassung (Actions → Lauf → Summary), auch in der GitHub-App.
- Doppelte Trades sind ausgeschlossen: eine Order-ID pro Tag, ein Trade pro Tag in der Datenbank, Abgleich mit dem Broker bei jedem Start.
- Fehlgeschlagene Läufe schickt GitHub dir per E-Mail.
- **Kosten:** ca. 1.100–1.400 von 2.000 Gratis-Minuten pro Monat (privates Repo). Öffentliche Repos sind unbegrenzt, zeigen aber Code und Ergebnisse.

## Grenzen – bitte ehrlich lesen

- GitHub-Zeitpläne können sich um Minuten verspäten oder selten ausfallen. Eingebaut sind Vorlauf (Start 15 Minuten vor der ersten Auswertung bzw. 25 Minuten vor dem Zwangsschluss),
  Ersatzläufe und GTC-Schutzorders; verpasst ein Lauf trotzdem den Zwangsschluss, wird die Position beim nächsten Lauf geschlossen
  und als `OVERNIGHT` markiert. Gewinne/Verluste sind dann nicht mehr „nach Regel“.
- Paper-Fills sind optimistischer als echte Märkte. Ein Backtest sagt nichts über die Zukunft. Keine Anlageberatung.
- Dein Secret nie in Chats posten. Wurde er bereits geteilt: im Alpaca-Dashboard neu generieren und `deploy_github.ps1` erneut ausführen.

## Struktur

```
backend/   strategy.py (Regeln) · risk.py · engine.py (Tagesablauf) · broker.py · marketdata.py · simulator.py
           backtest.py · db.py (SQLite) · export.py · api.py (Dashboard-Backend) · cli.py
frontend/  React + TypeScript + Tailwind (Dashboard, fertig gebaut in dist/)
tests/     89 Tests, laufen ohne Netzwerk mit simuliertem Markt, Broker und Uhr
.github/   session.yml · close.yml · check.yml        scripts/  deploy_github.ps1 · start.ps1 · ci_state.sh
```
