# Laptop A: hub + director + observer (dashboard on http://localhost:8080).
#   ./scripts/run_station.ps1                      # station only, drones run on the other laptops
#   ./scripts/run_station.ps1 -MockNodes           # also run all 10 MOCK drones in-process (single laptop demo)
#   ./scripts/run_station.ps1 -RunId demo-final    # name of runs/<RunId>/ (events.jsonl + exports)
# PowerShell 5.1 compatible (no && chains). Never runs firewall commands (see firewall_rules.ps1).
param(
    [string]$RunId = "",
    [string]$Mission = "",
    [switch]$MockNodes,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"

$py = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Missing .venv. Create it once:"
    Write-Host "  py -3 -m venv .venv"
    Write-Host "  ./.venv/Scripts/python -m pip install aiohttp pyyaml pytest"
    exit 1
}

$pyArgs = @("-m", "swarm.station")
if ($RunId -ne "") { $pyArgs += @("--run-id", $RunId) }
if ($Mission -ne "") { $pyArgs += @("--mission", $Mission) }
if ($MockNodes) { $pyArgs += "--mock-nodes" }

Write-Host "Your Wi-Fi IPv4 (give this to laptops B/C as -Hub <ip>:47000):"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -match "Wi-?Fi|Wireless" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object { Write-Host ("  " + $_.IPAddress + "  (" + $_.InterfaceAlias + ")") }
Write-Host "First run: click Allow for Python on PRIVATE networks (or see scripts/firewall_rules.ps1)."

if (-not $NoBrowser) { Start-Process "http://localhost:8080" }
& $py @pyArgs
