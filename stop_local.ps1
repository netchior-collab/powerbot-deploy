$ErrorActionPreference = 'Stop'

$stateRoot = 'E:\YN_AUTO\state\powerbot_local'
$pidFile = Join-Path $stateRoot 'streamlit.pid'

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host '실행 중인 파워봇 PID 기록이 없습니다.'
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host '파워봇은 이미 종료되어 있습니다.'
    exit 0
}
if ($process.CommandLine -notmatch 'streamlit' -or $process.CommandLine -notmatch 'ytpdbot_app\.py') {
    throw "PID $processId 프로세스가 파워봇이 아니므로 종료하지 않았습니다."
}

Stop-Process -Id $processId -Force
Remove-Item -LiteralPath $pidFile -Force
Write-Host '파워봇을 종료했습니다.'
