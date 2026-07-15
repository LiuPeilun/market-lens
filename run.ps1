param(
    [string]$HostAddress = "127.0.0.1",
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 5173,
    [switch]$SkipSupabaseStart
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path $RootDir "frontend"
$UvCacheDir = Join-Path $RootDir ".uv-cache"
$ApiTarget = "http://${HostAddress}:${BackendPort}"

function Test-PortListening {
    param([int]$Port)

    $lines = netstat -ano | Select-String ":$Port"
    foreach ($line in $lines) {
        if ($line.Line -match "LISTENING\s+(\d+)$") {
            return $Matches[1]
        }
    }
    return $null
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 20
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 2 | Out-Null
            return $true
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    return $false
}

if (-not $SkipSupabaseStart) {
    $supabaseHealthUrl = "http://127.0.0.1:54321/auth/v1/health"
    Write-Host "Checking local Supabase..."
    if (-not (Wait-HttpOk -Url $supabaseHealthUrl -TimeoutSeconds 2)) {
        Write-Host "Starting local Supabase..."
        & npx supabase@latest start *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Local Supabase failed to start. Run 'npx supabase@latest start' for details."
        }
    }
    if (-not (Wait-HttpOk -Url $supabaseHealthUrl -TimeoutSeconds 30)) {
        throw "Local Supabase did not become healthy within 30 seconds."
    }
    Write-Host "Local Supabase is ready: http://127.0.0.1:54321"
}

$backendPid = Test-PortListening -Port $BackendPort
if ($backendPid) {
    Write-Host "Backend port $BackendPort is already in use by PID $backendPid."
    Write-Host "Close the old backend window or run: Stop-Process -Id $backendPid"
    exit 1
}

$frontendPid = Test-PortListening -Port $FrontendPort
if ($frontendPid) {
    Write-Host "Frontend port $FrontendPort is already in use by PID $frontendPid."
    Write-Host "Close the old frontend window or run: Stop-Process -Id $frontendPid"
    exit 1
}

$UvicornExe = Join-Path $RootDir ".venv\Scripts\uvicorn.exe"
if (Test-Path $UvicornExe) {
    $backendStart = @"
Set-Location -LiteralPath '$RootDir'
`$env:UV_CACHE_DIR = '$UvCacheDir'
& '$UvicornExe' market_lens.api.app:app --host '$HostAddress' --port '$BackendPort'
"@
}
else {
    $backendStart = @"
Set-Location -LiteralPath '$RootDir'
`$env:UV_CACHE_DIR = '$UvCacheDir'
uv run --no-sync uvicorn market_lens.api.app:app --host '$HostAddress' --port '$BackendPort'
"@
}

$ViteCmd = Join-Path $FrontendDir "node_modules\.bin\vite.CMD"
if (-not (Test-Path $ViteCmd)) {
    throw "Cannot find $ViteCmd. Install frontend dependencies first."
}

$frontendStart = @"
Set-Location -LiteralPath '$FrontendDir'
`$env:VITE_API_PROXY_TARGET = '$ApiTarget'
& '$ViteCmd' --host '$HostAddress' --port '$FrontendPort'
"@

Write-Host "Starting backend in a new PowerShell window: $ApiTarget"
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $backendStart
)

if (-not (Wait-HttpOk -Url "$ApiTarget/health")) {
    Write-Host "Backend did not become ready within 20 seconds."
    Write-Host "Check the backend PowerShell window for the error message."
    exit 1
}

Write-Host "Starting frontend in a new PowerShell window: http://${HostAddress}:${FrontendPort}"
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $frontendStart
)

if (-not (Wait-HttpOk -Url "http://${HostAddress}:${FrontendPort}/health")) {
    Write-Host "Frontend did not become ready within 20 seconds."
    Write-Host "Check the frontend PowerShell window for the error message."
    exit 1
}

Write-Host ""
Write-Host "Market Lens is running."
Write-Host "Frontend: http://${HostAddress}:${FrontendPort}"
Write-Host "Backend:  $ApiTarget"
Write-Host ""
Write-Host "To stop services, close the two PowerShell windows that were opened."
