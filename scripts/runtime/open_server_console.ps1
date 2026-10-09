[CmdletBinding()]
param(
    [string]$Server = 'root@47.253.197.95',
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\codex_alicloud_ed25519",
    [int]$LocalPort = 18000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
if ($LocalPort -lt 1 -or $LocalPort -gt 65535) { throw 'Invalid local port.' }
if (-not (Test-Path -LiteralPath $IdentityFile)) { throw "SSH key not found: $IdentityFile" }
$url = "http://127.0.0.1:$LocalPort/"
function Test-Console {
    try {
        $health = Invoke-RestMethod -Uri "${url}health" -TimeoutSec 2
        return $health.app -eq 'Flame Contractor' -and $health.database -eq 'ready'
    } catch { return $false }
}

if (-not (Test-Console)) {
    if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $LocalPort is already occupied. Choose another -LocalPort."
    }
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $runtimeDir = Join-Path $env:LOCALAPPDATA 'FlameContractor\runtime'
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $log = Join-Path $runtimeDir 'server-tunnel.stderr.log'
    $arguments = "-i `"$IdentityFile`" -N -L 127.0.0.1:${LocalPort}:127.0.0.1:8000 -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $Server"
    $tunnel = Start-Process -FilePath $ssh -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardError $log -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        $tunnel.Refresh()
        if ($tunnel.HasExited) { break }
        if (Test-Console) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        $tunnel.Refresh()
        if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id }
        throw "SSH tunnel or server health check failed. See $log"
    }
    Set-Content -LiteralPath (Join-Path $runtimeDir 'server-tunnel.pid') -Value $tunnel.Id
}

Write-Output "Server console: $url"
if (-not $NoBrowser) { Start-Process $url }
