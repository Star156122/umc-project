$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskUrl = 'http://127.0.0.1:8765/'
function Test-ResearchServer {
    try {
        $taskReply = Invoke-RestMethod -Uri ($taskUrl + 'api/research') -TimeoutSec 2
        return ($null -ne $taskReply.strategies)
    } catch { return $false }
}
if (-not (Test-ResearchServer)) {
    $taskUv = (Get-Command uv -ErrorAction Stop).Source
    $taskLogs = Join-Path $taskRoot 'logs'
    New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
    $taskProcess = Start-Process -FilePath $taskUv -ArgumentList @('run','python','research.py','serve') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'research_server.log') -RedirectStandardError (Join-Path $taskLogs 'research_server_error.log')
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        if (Test-ResearchServer) { $taskReady = $true; break }
        if ($taskProcess.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $taskReady) { throw 'Server failed to start. Check logs/research_server_error.log.' }
}
Start-Process $taskUrl
