# Laptop B / C: run every drone whose host label matches, in one process.
#   ./scripts/run_nodes.ps1 -HostName B -Hub 192.168.1.20:47000
#   ./scripts/run_nodes.ps1 -HostName C -Hub 192.168.1.20 -RunId demo-final
#   ./scripts/run_nodes.ps1 -HostName A,B,board -Hub 127.0.0.1     # mock only: simulate the board too
# -Engine real needs Track A's engine (read-only import via PYTHONPATH) and -Keys <dir with <name>.json> (never git).
# PowerShell 5.1 compatible (no && chains).
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][string]$Hub,
    [ValidateSet("mock", "real")][string]$Engine = "mock",
    [string]$RunId = "",
    [string]$Mission = "",
    [string]$Keys = "",
    [string]$Observer = "",
    [switch]$ShimObsConflict   # real engine: work around the one-OBS-per-block bug until Track A fixes _build_block
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"

$py = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Missing .venv. Create it once:  py -3 -m venv .venv ; ./.venv/Scripts/python -m pip install aiohttp pyyaml pytest"
    exit 1
}

if ($Engine -eq "real") {
    $ledger = Join-Path $root "ledger"                     # copy of Track A's package inside this workspace
    if (-not (Test-Path (Join-Path $ledger "swarmledger"))) { $ledger = Join-Path (Split-Path -Parent $root) "MST-Newrro-/ledger" }
    if (-not (Test-Path $ledger)) { Write-Host "Real engine needs ledger/ in the repo root (or MST-Newrro-/ledger next to this folder) (read-only import)."; exit 1 }
    $env:PYTHONPATH = $ledger
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Write-Host ("Real engine from: " + $ledger + "   (needs: pip install eth-keys eth-utils pycryptodome in .venv)")
}

if ($Hub -notmatch ":") { $Hub = $Hub + ":47000" }
$pyArgs = @("-m", "swarm.node_host", "--host", $HostName, "--hub", $Hub, "--engine", $Engine)
if ($RunId -ne "") { $pyArgs += @("--run-id", $RunId) }
if ($Mission -ne "") { $pyArgs += @("--mission", $Mission) }
if ($Keys -ne "") { $pyArgs += @("--keys", $Keys) }
if ($Observer -ne "") { $pyArgs += @("--observer", $Observer) }
if ($ShimObsConflict) { $pyArgs += "--shim-obs-conflict" }

Write-Host ("Starting drones of host " + $HostName + " -> hub " + $Hub + " (Ctrl+C exports ledgers to runs/ and uploads them to laptop A)")
& $py @pyArgs
