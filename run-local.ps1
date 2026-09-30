# Festive Sale Gadget Advisor - run locally and publish a public URL (Windows).
#
#   powershell -ExecutionPolicy Bypass -File run-local.ps1
#
# Starts the app on http://localhost:7860 and opens a free Cloudflare quick tunnel so it is reachable from the
# internet. No Cloudflare account, no card, no domain. Press Ctrl+C to stop both.
#
# Running here rather than on a cloud host is not a compromise: Amazon and Flipkart challenge datacenter IPs far
# more than residential ones, so price lookups actually succeed more often from your own connection.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$AppDir  = Join-Path $PSScriptRoot 'app'
$VenvDir = Join-Path $PSScriptRoot '.venv'
$Port    = if ($env:PORT) { $env:PORT } else { '7860' }

function Say($msg, $colour = 'Cyan') { Write-Host "`n>> $msg" -ForegroundColor $colour }

# ---------------------------------------------------------------- python ----
# Exe and args are tracked separately: a "$parts[1..($parts.Length-1)]" slice on a single-token command such as
# "python" becomes $parts[1..0], which PowerShell evaluates as a *reverse* range and passes bogus arguments.
Say 'Checking Python'
$PyExe = $null
$PyArgs = @()
foreach ($cand in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python3'), @('python'))) {
    $exe = $cand[0]
    $extra = @(); if ($cand.Count -gt 1) { $extra = $cand[1..($cand.Count - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $v = & $exe @extra -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]'3.11')) {
            $PyExe = $exe; $PyArgs = $extra; break
        }
    } catch { }
}
if (-not $PyExe) {
    Write-Host "Python 3.11 or newer is required (browser-use needs it)." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'." -ForegroundColor Red
    exit 1
}
Write-Host "   using: $PyExe $($PyArgs -join ' ')"

# ---------------------------------------------------------------- chrome ----
Say 'Checking Chrome'
$chrome = @(
    "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
    "${env:PROGRAMFILES(X86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    Write-Host "   found: $chrome"
    $env:PRICE_CHROME_PATH = $chrome
} else {
    Write-Host "   Chrome not found - install it from https://www.google.com/chrome/" -ForegroundColor Yellow
    Write-Host "   (the app starts anyway, but live price checks will fail)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------ node ----
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "`n   Node.js not found. The Tavily and Memory agents need it." -ForegroundColor Yellow
    Write-Host "   Install the LTS build from https://nodejs.org/ then re-run this script." -ForegroundColor Yellow
}

# ------------------------------------------------------------------ venv ----
if (-not (Test-Path $VenvDir)) {
    Say 'Creating virtual environment (one time)'
    & $PyExe @PyArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not create the virtual environment.' -ForegroundColor Red; exit 1 }
}
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPy)) { Write-Host "Virtualenv looks broken: $VenvPy missing." -ForegroundColor Red; exit 1 }

Say 'Installing dependencies (a few minutes the first time)'
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $AppDir 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Host 'Dependency install failed.' -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ keys ----
$EnvFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $EnvFile)) {
    Say 'Setting up your API keys (stored in .env, never committed)' 'Yellow'
    $openai = Read-Host 'OPENAI_API_KEY'
    $tavily = Read-Host 'TAVILY_API_KEY'
    "OPENAI_API_KEY=$openai`nTAVILY_API_KEY=$tavily`n" | Set-Content -Path $EnvFile -Encoding utf8
    Write-Host '   saved to .env'
}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"').Trim("'"))
    }
}

# ------------------------------------------------------------ cloudflared ----
$Cf = Join-Path $PSScriptRoot 'cloudflared.exe'
if (-not (Test-Path $Cf)) {
    Say 'Downloading cloudflared (one time, ~20 MB)'
    $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    try {
        Invoke-WebRequest -Uri $url -OutFile $Cf -UseBasicParsing
    } catch {
        Write-Host "Could not download cloudflared: $_" -ForegroundColor Red
        Write-Host 'Grab it manually from https://github.com/cloudflare/cloudflared/releases and put cloudflared.exe here.' -ForegroundColor Red
        exit 1
    }
}

# --------------------------------------------------------------- run it ----
$env:PORT = $Port
Say "Starting the app on http://localhost:$Port"
$app = Start-Process -FilePath $VenvPy -ArgumentList (Join-Path $AppDir 'app.py') `
                     -WorkingDirectory $AppDir -PassThru -NoNewWindow

Write-Host '   waiting for it to come up...'
$ready = $false
foreach ($i in 1..60) {
    Start-Sleep -Seconds 2
    try {
        if ((Invoke-WebRequest "http://localhost:$Port/healthz" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) {
            $ready = $true; break
        }
    } catch { }
    if ($app.HasExited) { Write-Host 'The app exited during start-up - see the errors above.' -ForegroundColor Red; exit 1 }
}
if (-not $ready) { Write-Host 'App did not respond in time; continuing anyway.' -ForegroundColor Yellow }
else { Write-Host '   app is up' -ForegroundColor Green }

Say 'Opening the public tunnel'
$errLog = Join-Path $env:TEMP 'fsga-tunnel.err.log'
$outLog = Join-Path $env:TEMP 'fsga-tunnel.out.log'
foreach ($f in @($errLog, $outLog)) { if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue } }
$tun = Start-Process -FilePath $Cf -ArgumentList @('tunnel', '--no-autoupdate', '--url', "http://localhost:$Port") `
                     -PassThru -NoNewWindow -RedirectStandardError $errLog -RedirectStandardOutput $outLog

# cloudflared has printed the quick-tunnel URL to stderr in some versions and stdout in others, so read both. The
# files are still open by the tunnel, so read them share-friendly and never let a transient lock end the script.
$publicUrl = $null
foreach ($i in 1..45) {
    Start-Sleep -Seconds 2
    foreach ($f in @($errLog, $outLog)) {
        if (-not (Test-Path $f)) { continue }
        try {
            $fs = [System.IO.File]::Open($f, 'Open', 'Read', 'ReadWrite')
            $sr = New-Object System.IO.StreamReader($fs)
            $text = $sr.ReadToEnd()
            $sr.Close(); $fs.Close()
            $m = [regex]::Match($text, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($m.Success) { $publicUrl = $m.Value; break }
        } catch { }
    }
    if ($publicUrl) { break }
    if ($tun.HasExited) { Write-Host '   the tunnel exited unexpectedly' -ForegroundColor Yellow; break }
}
$log = $errLog

Write-Host ''
Write-Host ('=' * 68) -ForegroundColor Green
if ($publicUrl) {
    Write-Host ' YOUR PUBLIC URL' -ForegroundColor Green
    Write-Host "   $publicUrl" -ForegroundColor White
    Write-Host ''
    Write-Host ' Paste that into the front end to connect it:' -ForegroundColor Green
    Write-Host '   https://festive-sale-gadget-advisor.vercel.app' -ForegroundColor White
    Write-Host '   -> scroll to "Run it" and paste it into the "Connect a backend" box'
} else {
    Write-Host ' Tunnel did not report a URL. Check the log:' -ForegroundColor Yellow
    Write-Host "   $log"
}
Write-Host " Local:  http://localhost:$Port" -ForegroundColor Green
Write-Host ' Ctrl+C here stops both the app and the tunnel.' -ForegroundColor Green
Write-Host ('=' * 68) -ForegroundColor Green

try {
    while (-not $app.HasExited) { Start-Sleep -Seconds 2 }
} finally {
    Say 'Shutting down' 'Yellow'
    foreach ($p in @($tun, $app)) {
        if ($p -and -not $p.HasExited) { try { $p.Kill() } catch { } }
    }
}
