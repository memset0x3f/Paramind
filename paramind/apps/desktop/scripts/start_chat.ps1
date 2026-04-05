# Experimental Windows helper for launching two ParaMind desktop instances.
# This script is best-effort only. If your local PowerShell, npm, or networking
# setup differs, you may need to adapt it for your machine.

$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

$CoordinatorPort = if ($env:PARAMIND_COORDINATOR_PORT) { $env:PARAMIND_COORDINATOR_PORT } else { '9010' }
$BackendPortA = if ($env:PARAMIND_BACKEND_PORT_A) { $env:PARAMIND_BACKEND_PORT_A } else { '5001' }
$BackendPortB = if ($env:PARAMIND_BACKEND_PORT_B) { $env:PARAMIND_BACKEND_PORT_B } else { '5002' }
$BaseDataDir = if ($env:PARAMIND_APP_DATA_DIR) { $env:PARAMIND_APP_DATA_DIR } else { Join-Path $RootDir '.paramind-local' }

New-Item -ItemType Directory -Force -Path $BaseDataDir | Out-Null

function Stop-PortProcess {
    param([string]$Port)

    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $connections) {
            return
        }

        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pid in $pids) {
            if ($pid) {
                Write-Host "  Releasing port $Port (PID $pid)..."
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Warning "Unable to free port $Port automatically: $($_.Exception.Message)"
    }
}

function Start-ElectronInstance {
    param(
        [string]$InstanceId,
        [string]$InstanceName,
        [string]$BackendPort,
        [string]$InstanceDataDir
    )

    $command = @(
        "set PARAMIND_COORDINATOR_PORT=$CoordinatorPort",
        "set PARAMIND_INSTANCE_ID=$InstanceId",
        "set PARAMIND_INSTANCE_NAME=$InstanceName",
        "set PARAMIND_BACKEND_PORT=$BackendPort",
        "set PARAMIND_APP_DATA_DIR=$BaseDataDir",
        "set PARAMIND_INSTANCE_DATA_DIR=$InstanceDataDir",
        'npm.cmd start'
    ) -join ' && '

    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $command -WorkingDirectory $RootDir -PassThru
}

Write-Host 'Clearing residual processes...'
foreach ($port in @($CoordinatorPort, $BackendPortA, $BackendPortB)) {
    Stop-PortProcess -Port $port
}

Write-Host 'Starting first Electron instance...'
$electron1 = Start-ElectronInstance -InstanceId 'peer-a' -InstanceName 'Peer A' -BackendPort $BackendPortA -InstanceDataDir (Join-Path $BaseDataDir 'peer-a')

Start-Sleep -Seconds 2

Write-Host 'Starting second Electron instance...'
$electron2 = Start-ElectronInstance -InstanceId 'peer-b' -InstanceName 'Peer B' -BackendPort $BackendPortB -InstanceDataDir (Join-Path $BaseDataDir 'peer-b')

Write-Host 'All services started.'
Write-Host "Electron instance 1 PID: $($electron1.Id)"
Write-Host "Electron instance 2 PID: $($electron2.Id)"
Write-Host ''
Write-Host 'Press Ctrl+C to stop both Electron instances.'

try {
    Wait-Process -Id $electron1.Id, $electron2.Id
} finally {
    foreach ($proc in @($electron1, $electron2)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
