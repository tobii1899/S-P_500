<#
  Dashboard lokal starten (zeigt den Stand des Bots, der auf GitHub läuft).
      powershell -ExecutionPolicy Bypass -File scripts\start.ps1
  Der Bot selbst handelt NICHT lokal - er läuft auf GitHub. Das Dashboard liest nur.
#>
param([switch]$NoSync)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $null
foreach ($candidate in @("py", "python")) { if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break } }
if (-not $py) { Write-Host "Python 3.11+ fehlt. Installieren mit:  winget install Python.Python.3.12  (danach PowerShell neu öffnen)" -ForegroundColor Red; exit 1 }
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Host "Es wird Python 3.11 oder neuer benötigt (winget install Python.Python.3.12)." -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".venv")) { Write-Host "Erstelle virtuelle Umgebung ..."; & $py -m venv .venv }
$venv = ".\.venv\Scripts\python.exe"
Write-Host "Installiere Pakete (beim ersten Mal ca. 1 Minute) ..."
& $venv -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Paket-Installation fehlgeschlagen." -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host ".env angelegt (Keys sind nur für Live-Kurs und Backtest nötig)." }
if (-not $NoSync) { & $venv -m backend.cli sync }

Write-Host "Dashboard: http://127.0.0.1:8000   (beenden mit Strg+C)"
Start-Process "http://127.0.0.1:8000"
& $venv -m backend.cli serve
