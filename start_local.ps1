$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$app = Join-Path $root 'ytpdbot_app.py'
$dataRoot = 'E:\YN_AUTO\data\powerbot'
$stateRoot = 'E:\YN_AUTO\state\powerbot_local'
$database = Join-Path $dataRoot 'broadcast_ops.db'
$pidFile = Join-Path $stateRoot 'streamlit.pid'
$healthUrl = 'http://127.0.0.1:8501/_stcore/health'
$appUrl = 'http://127.0.0.1:8501'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "파워봇 가상환경 Python을 찾을 수 없습니다: $python"
}
if (-not (Test-Path -LiteralPath $app -PathType Leaf)) {
    throw "파워봇 앱을 찾을 수 없습니다: $app"
}

New-Item -ItemType Directory -Force -Path $dataRoot, $stateRoot | Out-Null

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($health -eq 'ok') {
        Start-Process $appUrl
        exit 0
    }
} catch {
    # 아직 실행 중이 아니므로 새 로컬 인스턴스를 시작한다.
}

$env:YTPDBOT_LOCAL_DESKTOP = '1'
$env:YTPDBOT_DB_PATH = $database
$arguments = @(
    '-m', 'streamlit', 'run', ('"{0}"' -f $app),
    '--server.address=127.0.0.1',
    '--server.port=8501',
    '--server.headless=true',
    '--browser.gatherUsageStats=false'
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii

$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        break
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health -eq 'ok') {
            $ready = $true
            break
        }
    } catch {
        # Streamlit이 포트를 열 때까지 기다린다.
    }
}

if (-not $ready) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw '파워봇이 20초 안에 정상 실행되지 않았습니다.'
}

Start-Process $appUrl
