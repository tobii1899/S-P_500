<#
  SPY-Bot auf GitHub bringen und dort laufen lassen (ohne deinen Laptop).

  Aufruf im Projektordner:
      powershell -ExecutionPolicy Bypass -File scripts\deploy_github.ps1

  Das Skript: installiert Git + GitHub CLI (falls nötig), meldet dich bei GitHub an, legt ein PRIVATES
  Repository an, hinterlegt deinen Alpaca-Key als verschlüsseltes Secret, lädt den Code hoch und startet
  einen Prüf-Lauf (Tests + Alpaca-Verbindung). Danach läuft der Bot jeden Börsentag automatisch.

  Optionen:  -RepoName "mein-name"   (Standard: spy-trading-bot)
             -Public                 (öffentliches Repo: unbegrenzte Gratis-Minuten, aber Code + Ergebnisse sind sichtbar)
#>
param(
  [string]$RepoName = "spy-trading-bot",
  [switch]$Public
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nFEHLER: $text" -ForegroundColor Red; exit 1 }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
# Führt einen Befehl still aus und liefert $true, wenn er mit Exitcode 0 endet.
function Quiet([scriptblock]$Block) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { & $Block *> $null; return ($LASTEXITCODE -eq 0) } finally { $ErrorActionPreference = $old }
}
function EnvValue($name) {
  if (-not (Test-Path ".env")) { return "" }
  $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
  if (-not $line) { return "" }
  return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

# ---------------------------------------------------------------- 1. Werkzeuge
Step "Werkzeuge prüfen (Git, GitHub CLI)"
foreach ($tool in @(@{ cmd = "git"; id = "Git.Git" }, @{ cmd = "gh"; id = "GitHub.cli" })) {
  if (-not (Has $tool.cmd)) {
    Write-Host "$($tool.cmd) fehlt und wird per winget installiert ..."
    if (-not (Has "winget")) { Fail "winget nicht gefunden. Bitte $($tool.cmd) manuell installieren (git-scm.com bzw. cli.github.com) und das Skript erneut starten." }
    winget install --id $tool.id -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Has $tool.cmd)) { Fail "$($tool.cmd) ist installiert, aber noch nicht im PATH. PowerShell schließen, neu öffnen und das Skript erneut starten." }
  }
}

# ---------------------------------------------------------------- 2. GitHub-Login
Step "Bei GitHub anmelden"
if (-not (Quiet { gh auth status })) {
  Write-Host "Ein Browserfenster öffnet sich. Den angezeigten Code dort eingeben und bestätigen."
  gh auth login --hostname github.com --git-protocol https --web --scopes "repo,workflow"
  if ($LASTEXITCODE -ne 0) { Fail "GitHub-Anmeldung fehlgeschlagen." }
}
Quiet { gh auth setup-git } | Out-Null
$login = (gh api user --jq ".login").Trim()
if (-not $login) { Fail "GitHub-Benutzername konnte nicht ermittelt werden." }
Write-Host "Angemeldet als $login"

# ---------------------------------------------------------------- 3. Alpaca-Zugangsdaten
Step "Alpaca-Zugangsdaten"
$apiKey = EnvValue "ALPACA_API_KEY"
$secret = EnvValue "ALPACA_SECRET_KEY"
if (-not $apiKey -or $apiKey -like "*HIER*") { $apiKey = (Read-Host "Alpaca API Key (Paper-Keys beginnen mit PK)").Trim() }
if (-not $secret -or $secret -like "*HIER*") {
  $secure = Read-Host "Alpaca Secret Key (Eingabe bleibt unsichtbar)" -AsSecureString
  $secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
  $saveLocal = Read-Host "Secret auch lokal in .env speichern (für Dashboard/Backtest; .env wird nie hochgeladen)? [J/n]"
  if ($saveLocal -notmatch "^[nN]") {
    $lines = @()
    if (Test-Path ".env") { $lines = @(Get-Content ".env" | Where-Object { $_ -notmatch "^\s*ALPACA_(API|SECRET)_KEY\s*=" }) }
    $lines = @("ALPACA_API_KEY=$apiKey", "ALPACA_SECRET_KEY=$secret") + $lines
    [System.IO.File]::WriteAllLines((Join-Path (Get-Location) ".env"), $lines)  # UTF-8 ohne BOM
  }
}
if (-not $apiKey -or -not $secret) { Fail "API Key und Secret werden benötigt." }
if ($apiKey -notlike "PK*") { Write-Host "Hinweis: Paper-Keys beginnen mit 'PK'. Dein Key beginnt anders - ist es wirklich ein Paper-Key?" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 4. Git
Step "Projekt in Git ablegen"
if (-not (Test-Path ".git")) { git init -b main | Out-Null }
if (-not (git config user.name)) { git config user.name $login }
if (-not (git config user.email)) { git config user.email "$login@users.noreply.github.com" }
git add -A
if (git ls-files ".env") { Fail ".env würde hochgeladen werden - das darf nie passieren. Bitte .gitignore prüfen." }
if (-not (Quiet { git diff --cached --quiet })) { git commit -m "SPY Bot: Projektstand" | Out-Null }
git branch -M main

# ---------------------------------------------------------------- 5. Repository
Step "GitHub-Repository anlegen und Code hochladen"
$full = "$login/$RepoName"
if (Quiet { gh repo view $full }) {
  Write-Host "Repository $full existiert bereits - der Code wird aktualisiert."
  if (-not (Quiet { git remote get-url origin })) { git remote add origin "https://github.com/$full.git" }
  git push -u origin main
  if ($LASTEXITCODE -ne 0) { Fail "Push fehlgeschlagen." }
} else {
  $visibility = if ($Public) { "--public" } else { "--private" }
  gh repo create $full $visibility --source . --remote origin --push
  if ($LASTEXITCODE -ne 0) { Fail "Repository konnte nicht angelegt werden." }
}

# ---------------------------------------------------------------- 6. Secrets
Step "Secrets und Einstellungen auf GitHub hinterlegen"
gh secret set ALPACA_API_KEY --repo $full --body $apiKey
if ($LASTEXITCODE -ne 0) { Fail "Secret ALPACA_API_KEY konnte nicht gesetzt werden." }
gh secret set ALPACA_SECRET_KEY --repo $full --body $secret
if ($LASTEXITCODE -ne 0) { Fail "Secret ALPACA_SECRET_KEY konnte nicht gesetzt werden." }
gh variable set BOT_MODE --repo $full --body "paper"
Write-Host "Keys liegen verschlüsselt auf GitHub. Modus: Paper-Trading."

# ---------------------------------------------------------------- 7. Prüf-Lauf
Step "Prüf-Lauf starten (Tests + Alpaca-Verbindung + Zeiten)"
$started = $false
for ($i = 0; $i -lt 8 -and -not $started; $i++) {
  if (Quiet { gh workflow run check.yml --repo $full --ref main }) { $started = $true } else { Start-Sleep -Seconds 5 }
}
if (-not $started) { Fail "Prüf-Lauf ließ sich nicht starten. Auf GitHub unter Actions -> 'Check' -> 'Run workflow' manuell starten." }
Start-Sleep -Seconds 8
$runId = (gh run list --repo $full --workflow check.yml --limit 1 --json databaseId --jq ".[0].databaseId").Trim()
gh run watch $runId --repo $full --exit-status | Out-Null
$checkOk = ($LASTEXITCODE -eq 0)
Write-Host ""
gh run view $runId --repo $full --log 2>$null |
  Select-String -Pattern "\[OK\]|\[FEHLER\]|\[INFO\]|Einstiegsfenster|Modus |Alles bereit|Es gibt Probleme|passed|failed" |
  ForEach-Object { $_.Line -replace '^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s', '' }
if (-not $checkOk) { Fail "Der Prüf-Lauf ist fehlgeschlagen (Details oben bzw. unter https://github.com/$full/actions). Meist ist der Secret Key falsch: In der Datei .env bei ALPACA_SECRET_KEY den richtigen Wert eintragen (oder durch HIER_SECRET_KEY_EINTRAGEN ersetzen, dann fragt das Skript neu) und das Skript erneut starten." }

# ---------------------------------------------------------------- 8. Sofortstart, falls die Börse gerade im Einstiegsfenster ist
try {
  $ny = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time"))
  $hhmm = $ny.Hour * 100 + $ny.Minute
  if ($ny.DayOfWeek -notin @("Saturday", "Sunday") -and $hhmm -ge 905 -and $hhmm -le 1056) {
    Step "Die Börse ist gerade im Einstiegsfenster - starte die Einstiegssuche sofort"
    gh workflow run session.yml --repo $full --ref main
  }
} catch { }

Write-Host "`nFERTIG - der Bot läuft jetzt auf GitHub." -ForegroundColor Green
Write-Host "Repository : https://github.com/$full"
Write-Host "Läufe/Logs : https://github.com/$full/actions   (auch in der GitHub-App auf dem iPhone)"
Write-Host "Zeitplan   : Mo-Fr automatisch. Einstiegssuche ab 15:20 Uhr (Wien), erste Kerze 15:35, Einstiegsfenster bis 17:00, Zwangsschluss 21:55."
Write-Host "Dashboard  : scripts\start.ps1  (zeigt den aktuellen Stand lokal)"
