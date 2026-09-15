param(
  [ValidateSet('morning','afternoon')]
  [string]$Slot = $(if ((Get-Date).Hour -lt 12) {'morning'} else {'afternoon'})
)
$Root = Split-Path -Parent $PSScriptRoot
$Profile = Join-Path $Root 'data\local-chrome-profile'
$Http = 'http' + '://'
$Https = 'https' + '://'
$CdpStatusUrl = $Http + '127.0.0.1:9222/json/version'
$ChromeOrigin = $Http + 'localhost'
$XHomeUrl = $Https + 'x.com/home'
$Chrome = Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'
if (-not (Test-Path -LiteralPath $Chrome)) { $Chrome = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe' }
if (-not (Test-Path -LiteralPath $Chrome)) { throw '未找到 Google Chrome。' }
try { Invoke-WebRequest -UseBasicParsing $CdpStatusUrl -TimeoutSec 1 | Out-Null }
catch {
  New-Item -ItemType Directory -Force -Path $Profile | Out-Null
  Start-Process -FilePath $Chrome -ArgumentList '--remote-debugging-port=9222', ("--remote-allow-origins=" + $ChromeOrigin), "--user-data-dir=$Profile", '--new-window', $XHomeUrl
  Start-Sleep -Seconds 5
  Read-Host '请在这个专用 Chrome 窗口登录 X、微博、抖音、小红书、YouTube；完成后按回车开始采集'
}
& npx.cmd -y bun (Join-Path $PSScriptRoot 'local-social-scan.js') $Slot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python (Join-Path $Root 'cloud_runner.py') --mode rebuild
