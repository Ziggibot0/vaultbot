# ═══════════════════════════════════════════════════════════════════════════
# VaultBot one-click installer for Windows
#
# Run from any folder (PowerShell):
#   irm https://github.com/ziggibot-uni/vaultbot/raw/main/setup.ps1 | iex
#
# Downloads the repo, creates a Python venv, installs everything,
# pulls AI models, asks your name, and opens Obsidian. No terminal
# knowledge required.
# ═══════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$repoZip   = "https://github.com/ziggibot-uni/vaultbot/archive/refs/heads/main.zip"
$vaultName = "VaultBot"

function Write-Step  { param($msg) Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2 { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "  =============================" -ForegroundColor Cyan
Write-Host "      VaultBot Installer" -ForegroundColor Cyan
Write-Host "  =============================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Prerequisite checks ─────────────────────────────────────────────────
Write-Step "Checking prerequisites..."
$missing = @()

# Python 3.11+
$pyOk = $false
try {
    $pv = & python --version 2>&1
    if ($pv -match "Python 3\.(\d+)") {
        $minor = [int]$matches[1]
        if ($minor -ge 11) { $pyOk = $true; Write-OK "Python: $pv" }
        else { Write-Err "Python 3.11+ required, found $pv" }
    }
} catch {}
if (-not $pyOk) {
    $missing += "Python 3.11+  ->  https://python.org/downloads`n         (check 'Add Python to PATH' during install!)"
}

# Ollama
$ollamaOk = $false
try {
    $ov = & ollama --version 2>&1
    if ($LASTEXITCODE -eq 0) { $ollamaOk = $true; Write-OK "Ollama: $ov" }
} catch {}
if (-not $ollamaOk) {
    $missing += "Ollama  ->  https://ollama.com"
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "  Almost there! Install these first, then run the command again:" -ForegroundColor Red
    Write-Host ""
    foreach ($m in $missing) { Write-Host "    - $m" -ForegroundColor Yellow }
    Write-Host ""
    # Open download pages in the browser so they just click
    foreach ($m in $missing) {
        if ($m -match "python\.org") { Start-Process "https://python.org/downloads" }
        if ($m -match "ollama\.com") { Start-Process "https://ollama.com" }
    }
    return  # exits the iex scope without killing the terminal window
}

# ── 2. Ask the user's name ──────────────────────────────────────────────────
Write-Host ""
Write-Host "  What's your name? VaultBot will call you by this." -ForegroundColor Cyan
$ownerName = Read-Host "  Your name"
if ([string]::IsNullOrWhiteSpace($ownerName)) { $ownerName = "friend" }
Write-Host ""

# ── 3. Download the repo ────────────────────────────────────────────────────
$vaultPath = Join-Path $PWD $vaultName
if (Test-Path $vaultPath) {
    Write-Warn2 "Folder '$vaultName' already exists here -- using it."
} else {
    Write-Step "Downloading VaultBot..."
    $zipPath = Join-Path $env:TEMP "vaultbot-setup.zip"
    & curl.exe -sL -o $zipPath $repoZip
    if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -lt 1000) {
        # Fallback to .NET download if curl isn't available
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($repoZip, $zipPath)
    }

    Write-Step "Extracting..."
    $extractDir = Join-Path $env:TEMP "vaultbot-extract-$(Get-Random)"
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    $inner = Get-ChildItem $extractDir -Directory | Select-Object -First 1
    Move-Item $inner.FullName $vaultPath
    Remove-Item $zipPath -Force
    Remove-Item $extractDir -Recurse -Force
    Write-OK "Downloaded to $vaultPath"
}

# ── 4. Create the Python virtual environment ────────────────────────────────
$venvPath = Join-Path $vaultPath "vaultbot_venv"
if (Test-Path (Join-Path $venvPath "Scripts\python.exe")) {
    Write-Warn2 "Virtual environment already exists -- skipping."
} else {
    Write-Step "Creating Python environment (a few seconds)..."
    Push-Location $vaultPath
    try { & python -m venv vaultbot_venv } finally { Pop-Location }
    Write-OK "Virtual environment created"
}

# ── 5. Install dependencies ─────────────────────────────────────────────────
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$reqPath    = Join-Path $vaultPath "vaultbot_backend\requirements.txt"

Write-Step "Installing dependencies (5-15 min, one-time only)..."
Write-Host "  Grab a coffee. This is the longest step." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r $reqPath
if ($LASTEXITCODE -ne 0) {
    Write-Err "Dependency installation failed. See errors above."
    Write-Host "  Try re-running the command. If it keeps failing, ask for help." -ForegroundColor Yellow
    return
}
Write-OK "Dependencies installed"

# ── 6. Pull AI models via Ollama ────────────────────────────────────────────
Write-Step "Downloading AI models (~2 GB, one-time only)..."
Write-Host "  This is the big download. It resumes if interrupted." -ForegroundColor DarkGray
& ollama pull qwen3.6:latest
& ollama pull nomic-embed-text
Write-OK "Models ready"

# ── 7. Write .env with the user's name ──────────────────────────────────────
$envExample = Join-Path $vaultPath ".env.example"
$envFile    = Join-Path $vaultPath ".env"
if (Test-Path $envExample) {
    $content = Get-Content $envExample -Raw
    $content = $content -replace 'VAULTBOT_OWNER=.*', "VAULTBOT_OWNER=$ownerName"
    # Write UTF-8 WITHOUT BOM (BOM breaks Python's dotenv parser)
    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.UTF8Encoding]::new($false))
    Write-OK "Configured -- VaultBot will call you $ownerName"
} else {
    Write-Warn2 ".env.example not found -- skipping .env creation"
}

# ── 8. Done -- open Obsidian ────────────────────────────────────────────────
Write-Host ""
Write-Host "  =============================" -ForegroundColor Green
Write-Host "      Setup Complete!" -ForegroundColor Green
Write-Host "  =============================" -ForegroundColor Green
Write-Host ""
Write-Host "  Your vault is at:" -ForegroundColor Cyan
Write-Host "    $vaultPath" -ForegroundColor White
Write-Host ""
Write-Host "  Opening Obsidian for you now..." -ForegroundColor Cyan
Write-Host "  (If it doesn't open, open Obsidian manually and" -ForegroundColor DarkGray
Write-Host "   choose 'Open folder as vault' -> select the folder above)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  In Obsidian:" -ForegroundColor Cyan
Write-Host "    1. Settings (gear) -> Community plugins" -ForegroundColor White
Write-Host "    2. Turn OFF 'Restricted mode'" -ForegroundColor White
Write-Host "    3. Find VaultBot -> toggle ON" -ForegroundColor White
Write-Host "    4. Click the robot icon in the sidebar" -ForegroundColor White
Write-Host "    5. Say hi!" -ForegroundColor White
Write-Host ""
Write-Host "  VaultBot knows your name is $ownerName." -ForegroundColor Magenta
Write-Host ""

# Try to open Obsidian deep-linked to the vault
try {
    $uri = "obsidian://open?path=$([uri]::EscapeDataString($vaultPath))"
    Start-Process $uri
} catch {
    Write-Warn2 "Couldn't auto-open Obsidian. Open it manually and pick the folder above."
}