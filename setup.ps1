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

# ── Install-state resume helpers ──────────────────────────────────────────
# The installer writes a .vaultbot-install-state.json inside the vault folder
# tracking which steps have completed. On re-run (e.g. the user killed the
# terminal mid-download, or a step failed and they're trying again), each
# step checks the state before running and skips if already done. This makes
# "re-run the same command — it picks up where it left off" literally true
# instead of aspirational.
#
# The state file lives at $vaultPath/.vaultbot-install-state.json, so it's
# only available AFTER step 3 (download). Steps 1-2 (prerequisites + name)
# are interactive and always run — they're safe to repeat.
$script:stateFile = $null

function Test-StepDone {
    param([string]$step)
    if (-not $script:stateFile -or -not (Test-Path $script:stateFile)) { return $false }
    try {
        $state = Get-Content $script:stateFile -Raw | ConvertFrom-Json
        return ($state.$step -eq $true)
    } catch { return $false }
}

function Set-StepDone {
    param([string]$step)
    if (-not $script:stateFile) { return }
    try {
        $state = @{}
        if (Test-Path $script:stateFile) {
            $state = Get-Content $script:stateFile -Raw | ConvertFrom-Json
        }
        $state.$step = $true
        # Write UTF-8 WITHOUT BOM (BOM breaks JSON parsers).
        [System.IO.File]::WriteAllText($script:stateFile, ($state | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warn2 "Could not write install state (non-fatal): $_"
    }
}

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

# Now that $vaultPath exists, set the install-state file path so steps
# 4-7 can resume if the user re-runs after a partial install.
$script:stateFile = Join-Path $vaultPath ".vaultbot-install-state.json"
if (Test-Path $script:stateFile) {
    Write-Warn2 "Found previous install state — resuming where you left off."
}

# ── 4. Create the Python virtual environment ────────────────────────────────
# `.venv` is hidden in the Obsidian file explorer (dots are filtered),
# keeping the vault clean for end users.
$venvPath = Join-Path $vaultPath ".venv"
if (Test-StepDone "venv_created") {
    Write-Warn2 "Virtual environment already created -- skipping."
} elseif (Test-Path (Join-Path $venvPath "Scripts\python.exe")) {
    Write-Warn2 "Virtual environment already exists -- skipping."
    Set-StepDone "venv_created"
} else {
    Write-Step "Creating Python environment (a few seconds)..."
    Push-Location $vaultPath
    try { & python -m venv .venv } finally { Pop-Location }
    Write-OK "Virtual environment created"
    Set-StepDone "venv_created"
}

# ── 5. Install dependencies ─────────────────────────────────────────────────
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$reqPath    = Join-Path $vaultPath "vaultbot_stuff\vaultbot_backend\requirements.txt"
# Reproducible install: prefer the lockfile (exact pins) if present so a
# fresh clone gets the same versions the project was tested with. Fall back
# to requirements.txt (the >= bounds) if the lock is missing or stale.
$lockPath   = Join-Path $vaultPath "vaultbot_stuff\vaultbot_backend\requirements.lock"
$installReq = if (Test-Path $lockPath) { $lockPath } else { $reqPath }

if (Test-StepDone "deps_installed") {
    Write-Warn2 "Dependencies already installed -- skipping."
} else {
    Write-Step "Installing dependencies (5-15 min, one-time only)..."
    Write-Host "  Grab a coffee. This is the longest step." -ForegroundColor DarkGray
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r $installReq
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency installation failed. See errors above."
        Write-Host "  Re-run the same command — it picks up from here." -ForegroundColor Yellow
        return
    }
    Write-OK "Dependencies installed"
    Set-StepDone "deps_installed"
}

# ── 6. Pull embedding model via Ollama ─────────────────────────────────────
# Only the lightweight embedding model (nomic-embed-text, ~270 MB) is
# auto-pulled. The chat/synthesis LLM is handled in step 6b based on the
# user's choice (local Ollama vs cloud API).
if (Test-StepDone "models_pulled") {
    Write-Warn2 "Embedding model already downloaded -- skipping."
} else {
    Write-Step "Downloading embedding model (~270 MB, one-time only)..."
    & ollama pull nomic-embed-text
    Write-OK "Embedding model ready"
    Set-StepDone "models_pulled"
}

# ── 6b. Pull the small model (always local, always free) ──────────────────
# The small model cartridge (qwen3.5:0.8b, ~1 GB) handles classification,
# routing, compression, and procedure dispatch. It runs on ANY laptop and
# costs zero cloud tokens. This is the ONLY model the installer pulls
# automatically — the big chat model is the user's choice.
if (Test-StepDone "small_model_pulled") {
    Write-Warn2 "Small model already downloaded -- skipping."
} else {
    Write-Step "Downloading small model: qwen3.5:0.8b (~1 GB, one-time only)..."
    & ollama pull qwen3.5:0.8b
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Small model pull failed. You can run 'ollama pull qwen3.5:0.8b' manually later."
        Write-Host "  VaultBot will still start — procedures will fall back to the big model." -ForegroundColor Yellow
    } else {
        Write-OK "Small model ready: qwen3.5:0.8b"
    }
    Set-StepDone "small_model_pulled"
}

# ── 6c. Ask: local chat model or cloud API? ────────────────────────────────
# The embedding model (above) is mandatory and always local. The CHAT
# model is the user's choice: a local Ollama model (free, private, heavy)
# or a cloud API key (zero local compute, recommended for laptops).
#
# NO DEFAULT — the user picks their own model. The small model (qwen3.5:0.8b)
# was already pulled above and handles all the cheap work.
$chatBackend = "ollama"  # default
$chatModel   = ""
if (-not (Test-StepDone "chat_backend_chosen")) {
    Write-Host ""
    Write-Host "  VaultBot needs a big chat model for reasoning and synthesis." -ForegroundColor Cyan
    Write-Host "  (A small model for routing/classification was already downloaded.)" -ForegroundColor DarkGray
    Write-Host "  Two options:" -ForegroundColor White
    Write-Host "    1. Local (free, private, uses Ollama — already installed)" -ForegroundColor White
    Write-Host "       You pick any Ollama model. Needs disk/RAM for the model." -ForegroundColor DarkGray
    Write-Host "    2. Cloud API (zero local compute, recommended for laptops)" -ForegroundColor White
    Write-Host "       You provide an API key later (OpenAI, OpenRouter, etc.)." -ForegroundColor DarkGray
    Write-Host ""
    $choice = Read-Host "  Pick 1 or 2 (default: 1)"
    if ($choice -eq "2") {
        $chatBackend = "openai"
        Write-Host ""
        Write-Host "  You'll need an API key from OpenAI, OpenRouter, or any" -ForegroundColor Yellow
        Write-Host "  OpenAI-compatible provider. Add it to .env after setup:" -ForegroundColor Yellow
        Write-Host "    LLM_API_KEY=sk-..." -ForegroundColor White
        Write-Host "    LLM_MODEL=<your model name>" -ForegroundColor White
        Write-Host "  Free-tier OpenRouter models work great. See openrouter.ai/models" -ForegroundColor DarkGray
    } else {
        $chatBackend = "ollama"
        Write-Host ""
        Write-Host "  Which model? Popular choices:" -ForegroundColor Cyan
        Write-Host "    qwen3:1.7b          (tiny, ~1.4 GB)" -ForegroundColor White
        Write-Host "    llama3.2:latest     (3B, lightweight, ~2 GB)" -ForegroundColor White
        Write-Host "    qwen3:latest        (4-8B, good balance, ~4 GB)" -ForegroundColor White
        Write-Host "    qwen3.6:latest      (larger, best quality, ~8 GB)" -ForegroundColor White
        Write-Host "  Type a model name (required — no default):" -ForegroundColor DarkGray
        $chatModel = Read-Host "  Model name"
        while ([string]::IsNullOrWhiteSpace($chatModel)) {
            Write-Host "  Please enter a model name (e.g. qwen3:latest):" -ForegroundColor Yellow
            $chatModel = Read-Host "  Model name"
        }
    }
    Set-StepDone "chat_backend_chosen"
} elseif (Test-StepDone "chat_model_pulled") {
    Write-Warn2 "Chat model choice already made -- skipping."
}

# ── 6c. Pull the chat model if local ──────────────────────────────────────
if ($chatBackend -eq "ollama" -and $chatModel -and -not (Test-StepDone "chat_model_pulled")) {
    Write-Step "Downloading chat model: $chatModel (this can take a while)..."
    Write-Host "  Grab a coffee. Large models take 5-30 min depending on your connection." -ForegroundColor DarkGray
    & ollama pull $chatModel
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Chat model pull failed. You can run 'ollama pull $chatModel' manually later."
        Write-Host "  VaultBot will still start — you'll just need to pull a model before chatting." -ForegroundColor Yellow
    } else {
        Write-OK "Chat model ready: $chatModel"
    }
    Set-StepDone "chat_model_pulled"
}

# ── 7. Write .env with the user's name + LLM config ─────────────────────────
$envExample = Join-Path $vaultPath "vaultbot_stuff\.env.example"
$envFile    = Join-Path $vaultPath ".env"
if (Test-StepDone "env_written") {
    Write-Warn2 "Config already written -- skipping."
} elseif (Test-Path $envExample) {
    $content = Get-Content $envExample -Raw
    $content = $content -replace 'VAULTBOT_OWNER=.*', "VAULTBOT_OWNER=$ownerName"
    $content = $content -replace 'LLM_BACKEND=.*', "LLM_BACKEND=$chatBackend"
    $content = $content -replace 'SMALL_MODEL=.*', "SMALL_MODEL=qwen3.5:0.8b"
    if ($chatBackend -eq "ollama" -and $chatModel) {
        $content = $content -replace 'OLLAMA_LLM_MODEL=.*', "OLLAMA_LLM_MODEL=$chatModel"
    }
    # Write UTF-8 WITHOUT BOM (BOM breaks Python's dotenv parser)
    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.UTF8Encoding]::new($false))
    Write-OK "Configured -- VaultBot will call you $ownerName"
    if ($chatBackend -eq "openai") {
        Write-Host "  Don't forget: add your LLM_API_KEY to .env to use your cloud model." -ForegroundColor Yellow
    }
    Set-StepDone "env_written"
} else {
    Write-Warn2 ".env.example not found -- skipping .env creation"
    Set-StepDone "env_written"
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