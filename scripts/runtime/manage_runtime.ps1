[CmdletBinding()]
param(
    [ValidateSet('Install', 'Start', 'Stop', 'Status', 'Uninstall')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = 'Stop'
$taskName = 'Flame Contractor Runtime'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$supervisorPath = Join-Path $PSScriptRoot 'flame_supervisor.ps1'
$pythonPath = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
$frontendDir = Join-Path $repoRoot 'frontend'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FlameContractor\runtime'
$logRoot = Join-Path $env:LOCALAPPDATA 'FlameContractor\logs'
$supervisorPidFile = Join-Path $runtimeRoot 'supervisor.pid'
$backendPidFile = Join-Path $runtimeRoot 'backend.pid'
$powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function Get-TrackedProcess {
    param(
        [string]$PidFile,
        [string]$ExpectedExecutable,
        [string]$ExpectedCommand
    )
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }
    $trackedPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidFile -Raw).Trim(), [ref]$trackedPid)) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $trackedPid" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    if ($ExpectedExecutable -and $process.ExecutablePath -ne $ExpectedExecutable) {
        return $null
    }
    if ($ExpectedCommand -and $process.CommandLine -notlike "*$ExpectedCommand*") {
        return $null
    }
    return $process
}

function Test-RuntimeHealth {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
        return $response.status -eq 'ok' -and $response.database -eq 'ready'
    }
    catch {
        return $false
    }
}

function Show-RuntimeStatus {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $supervisor = Get-TrackedProcess `
        -PidFile $supervisorPidFile `
        -ExpectedExecutable $powershellPath `
        -ExpectedCommand $supervisorPath
    $backend = Get-TrackedProcess `
        -PidFile $backendPidFile `
        -ExpectedExecutable $pythonPath `
        -ExpectedCommand 'uvicorn app.main:app'
    [pscustomobject]@{
        TaskInstalled = $null -ne $task
        TaskState = if ($task) { $task.State } else { 'NotInstalled' }
        SupervisorPid = if ($supervisor) { $supervisor.ProcessId } else { $null }
        BackendPid = if ($backend) { $backend.ProcessId } else { $null }
        HealthReady = Test-RuntimeHealth
        Url = 'http://127.0.0.1:8000/'
        Logs = $logRoot
    } | Format-List
}

function Stop-RuntimeProcesses {
    $backend = Get-TrackedProcess `
        -PidFile $backendPidFile `
        -ExpectedExecutable $pythonPath `
        -ExpectedCommand 'uvicorn app.main:app'
    if ($backend) {
        Stop-Process -Id $backend.ProcessId -Force
    }
    $supervisor = Get-TrackedProcess `
        -PidFile $supervisorPidFile `
        -ExpectedExecutable $powershellPath `
        -ExpectedCommand $supervisorPath
    if ($supervisor) {
        Stop-Process -Id $supervisor.ProcessId -Force
    }
    Remove-Item -LiteralPath $backendPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $supervisorPidFile -Force -ErrorAction SilentlyContinue
}

switch ($Action) {
    'Install' {
        if (-not (Test-Path -LiteralPath $pythonPath)) {
            throw "Python runtime not found: $pythonPath"
        }
        Push-Location $frontendDir
        try {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) {
                throw "Frontend build failed with exit code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }

        $actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisorPath`""
        $taskAction = New-ScheduledTaskAction `
            -Execute $powershellPath `
            -Argument $actionArguments `
            -WorkingDirectory $repoRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $settings = New-ScheduledTaskSettingsSet `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries
        $principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive `
            -RunLevel Limited
        $task = New-ScheduledTask `
            -Action $taskAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description 'Keeps Flame Contractor available in the current Windows user session.'
        Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 8
        Show-RuntimeStatus
    }
    'Start' {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) {
            throw "Scheduled task is not installed. Run with -Action Install first."
        }
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 5
        Show-RuntimeStatus
    }
    'Stop' {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Stop-RuntimeProcesses
        Show-RuntimeStatus
    }
    'Status' {
        Show-RuntimeStatus
    }
    'Uninstall' {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Stop-RuntimeProcesses
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Show-RuntimeStatus
    }
}
