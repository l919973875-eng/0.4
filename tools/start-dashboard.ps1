$Root = Split-Path -Parent $PSScriptRoot
$Candidates = @(
  (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
  (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if (-not $Candidates) { throw 'Python runtime was not found. Please open this project in Codex once, then retry.' }
$Python = $Candidates[0]
$Dashboard = Join-Path $PSScriptRoot 'local_dashboard.py'
try {
  Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8765/api/status' -TimeoutSec 1 | Out-Null
  Start-Process 'http://127.0.0.1:8765/'
} catch {
  Start-Process -FilePath $Python -ArgumentList @($Dashboard, '--open') -WorkingDirectory $Root -WindowStyle Hidden
}