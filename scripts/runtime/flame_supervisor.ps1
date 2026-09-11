[CmdletBinding()]
param(
    [int]$Port = 8000,
    [int]$HealthIntervalSeconds = 15,
    [int]$HealthFailureLimit = 5
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$backendDir = Join-Path $repoRoot 'backend'
$pythonPath = Join-Path $backendDir '.venv\Scripts\python.exe'
$frontendDist = Join-Path $repoRoot 'frontend\dist'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FlameContractor\runtime'
$logRoot = Join-Path $env:LOCALAPPDATA 'FlameContractor\logs'
$supervisorPidFile = Join-Path $runtimeRoot 'supervisor.pid'
$backendPidFile = Join-Path $runtimeRoot 'backend.pid'
$supervisorLog = Join-Path $logRoot 'supervisor.log'

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-RuntimeLog {
    param([string]$Message)
    if ((Test-Path -LiteralPath $supervisorLog) -and
        (Get-Item -LiteralPath $supervisorLog).Length -gt 5MB) {
        $archive = Join-Path $logRoot ("supervisor-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Move-Item -LiteralPath $supervisorLog -Destination $archive
    }
    Add-Content -LiteralPath $supervisorLog -Encoding UTF8 -Value (
        "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    )
}

function Test-Health {
    try {
        $response = Invoke-WebRequest `
            -Uri ("http://127.0.0.1:{0}/health" -f $Port) `
            -UseBasicParsing `
            -TimeoutSec 5
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-PortOpen {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(1000)) {
            return $false
        }
        $client.EndConnect($connect)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Start-Backend {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python runtime not found: $pythonPath"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDist 'index.html'))) {
        throw "Frontend build not found: $frontendDist"
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdoutPath = Join-Path $logRoot "backend-$stamp.out.log"
    $stderrPath = Join-Path $logRoot "backend-$stamp.err.log"
    $env:FLAME_SERVE_FRONTEND = 'true'
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @(
            '-m', 'uvicorn', 'app.main:app',
            '--host', '127.0.0.1',
            '--port', $Port,
            '--no-access-log'
        ) `
        -WorkingDirectory $backendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    Set-Content -LiteralPath $backendPidFile -Encoding ASCII -Value $process.Id
    Write-RuntimeLog "Started backend pid=$($process.Id) port=$Port"
    return $process
}

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\FlameContractorSupervisor', [ref]$createdNew)
if (-not $createdNew) {
    Write-RuntimeLog 'Another supervisor instance is already running; exiting.'
    exit 0
}

$executionStateType = Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FlameExecutionState {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint executionState);
}
'@ -PassThru

$esContinuous = [uint32]2147483648
$esSystemRequired = [uint32]0x00000001
$previousExecutionState = $executionStateType::SetThreadExecutionState($esContinuous -bor $esSystemRequired)
if ($previousExecutionState -eq 0) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "Failed to create the system-awake request. Win32 error: $errorCode"
}
Set-Content -LiteralPath $supervisorPidFile -Encoding ASCII -Value $PID
Write-RuntimeLog "Supervisor started pid=$PID; system-awake request enabled."

$backendProcess = $null
$healthFailures = 0

try {
    while ($true) {
        if ($null -ne $backendProcess -and $backendProcess.HasExited) {
            Write-RuntimeLog "Backend pid=$($backendProcess.Id) exited with code=$($backendProcess.ExitCode)."
            Remove-Item -LiteralPath $backendPidFile -Force -ErrorAction SilentlyContinue
            $backendProcess = $null
        }

        if (Test-Health) {
            $healthFailures = 0
        }
        elseif ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
            $healthFailures += 1
            Write-RuntimeLog "Owned backend pid=$($backendProcess.Id) failed health check ($healthFailures/$HealthFailureLimit)."
        }
        elseif (Test-PortOpen) {
            $healthFailures += 1
            Write-RuntimeLog "Health check failed while port $Port is owned by another or unresponsive process ($healthFailures/$HealthFailureLimit)."
        }
        else {
            if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
                $backendProcess.Refresh()
            }
            $backendProcess = Start-Backend
            $healthFailures = 0
        }

        if ($healthFailures -ge $HealthFailureLimit -and $null -ne $backendProcess) {
            Write-RuntimeLog "Backend pid=$($backendProcess.Id) exceeded the health failure limit; restarting owned process."
            Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $backendPidFile -Force -ErrorAction SilentlyContinue
            $backendProcess = $null
            $healthFailures = 0
        }

        Start-Sleep -Seconds $HealthIntervalSeconds
    }
}
finally {
    $null = $executionStateType::SetThreadExecutionState($esContinuous)
    Remove-Item -LiteralPath $supervisorPidFile -Force -ErrorAction SilentlyContinue
    $mutex.ReleaseMutex()
    $mutex.Dispose()
    Write-RuntimeLog 'Supervisor stopped; system-awake request cleared.'
}
