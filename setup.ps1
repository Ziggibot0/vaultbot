# ===========================================================================
# VaultBot one-click installer for Windows
#
# Run from any folder (PowerShell):
#   irm https://github.com/Ziggibot0/vaultbot/raw/main/setup.ps1 | iex
#
# Downloads the repo, creates a Python venv, installs everything,
# pulls AI models, asks your name, and opens Obsidian. No terminal
# knowledge required.
# ===========================================================================

$ErrorActionPreference = "Stop"
$frameworkName = "VaultBot"   # the folder the repo (framework) clones into

function Write-Step  { param($msg) Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2 { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red }

# -- Install-state resume helpers ------------------------------------------
# The installer writes a .vaultbot-install-state.json inside the vault folder
# tracking which steps have completed. On re-run (e.g. the user killed the
# terminal mid-download, or a step failed and they're trying again), each
# step checks the state before running and skips if already done. This makes
# "re-run the same command - it picks up where it left off" literally true
# instead of aspirational.
#
# The state file lives at $vaultPath/.vaultbot-install-state.json, so it's
# only available AFTER step 3 (download). Steps 1-2 (prerequisites + name)
# are interactive and always run - they're safe to repeat.
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
            # ConvertFrom-Json returns a PSCustomObject, not a hashtable.
            # On PowerShell 5.1, assigning a NEW property to a PSCustomObject
            # throws "The property 'X' cannot be found on this object."
            # Copy the existing properties into a real hashtable first.
            $obj = Get-Content $script:stateFile -Raw | ConvertFrom-Json
            foreach ($prop in $obj.PSObject.Properties) {
                $state[$prop.Name] = $prop.Value
            }
        }
        $state[$step] = $true
        # Write UTF-8 WITHOUT BOM (BOM breaks JSON parsers).
        [System.IO.File]::WriteAllText($script:stateFile, ($state | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warn2 "Could not write install state (non-fatal): $_"
    }
}

function Set-StateValue {
    param([string]$key, [string]$value)
    if (-not $script:stateFile) { return }
    try {
        $state = @{}
        if (Test-Path $script:stateFile) {
            # Same PSCustomObject -> hashtable copy as Set-StepDone above.
            $obj = Get-Content $script:stateFile -Raw | ConvertFrom-Json
            foreach ($prop in $obj.PSObject.Properties) {
                $state[$prop.Name] = $prop.Value
            }
        }
        $state[$key] = $value
        [System.IO.File]::WriteAllText($script:stateFile, ($state | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warn2 "Could not write install state (non-fatal): $_"
    }
}

function Get-StateValue {
    param([string]$key)
    if (-not $script:stateFile -or -not (Test-Path $script:stateFile)) { return $null }
    try {
        $state = Get-Content $script:stateFile -Raw | ConvertFrom-Json
        return $state.$key
    } catch { return $null }
}

function Get-FreeCloudModel {
    # Query OpenRouter's PUBLIC model list (no API key needed) and pick the
    # best free model that can drive the "big" cartridge: it must be free
    # (pricing 0/0) AND have a large context window (>=128K) so it can hold
    # the agentic loop + RAG context. We prefer a curated, ordered list of
    # known-good free models (so the pick is a model we've verified can
    # tool-call and reason), falling back to the first free+capable model
    # the API reports if none of the curated picks are still live. Returns
    # "" if the query fails (offline) - the caller falls back to a default.
    $curated = @(
        "z-ai/glm-5.2:free",                          # 256K ctx, strong agentic
        "nvidia/nemotron-3-ultra-550b-a55b:free",     # 1M ctx
        "dots-studio/dots-3-note-preview:free"        # 512K ctx
    )
    try {
        $resp = Invoke-RestMethod -Uri "https://openrouter.ai/api/v1/models" -TimeoutSec 15
        $live = @{}
        $firstFree = ""
        foreach ($m in $resp.data) {
            $id = $m.id
            if (-not $id) { continue }
            $p = $m.pricing
            $prompt = "$($p.prompt)"
            $completion = "$($p.completion)"
            $isFree = ($prompt -eq "0" -or $prompt -eq "0.0") -and ($completion -eq "0" -or $completion -eq "0.0")
            $ctx = 0
            try { $ctx = [int]$m.context_length } catch { $ctx = 0 }
            if ($isFree -and $ctx -ge 128000) {
                $live[$id] = $true
                if (-not $firstFree) { $firstFree = $id }
            }
        }
        foreach ($c in $curated) {
            if ($live.ContainsKey($c)) { return $c }
        }
        return $firstFree
    } catch {
        return ""
    }
}

function Get-LatestReleaseTag {
    # Resolve the latest GitHub Release tag for VaultBot code installs.
    # Returns "" when the API is unavailable so the caller can fail clearly.
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/Ziggibot0/vaultbot/releases/latest" -TimeoutSec 20
        $tag = "$($rel.tag_name)".Trim()
        if ($tag) { return $tag }
        return ""
    } catch {
        return ""
    }
}

Write-Host ""
Write-Host "  =============================" -ForegroundColor Cyan
Write-Host "      VaultBot Installer" -ForegroundColor Cyan
Write-Host "  =============================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Prerequisite checks (auto-install what's missing) -------------------
# VaultBot should be one paste for a non-technical user. So instead of just
# DETECTING Python/Git/Ollama and telling the user to install them, we
# AUTO-INSTALL them via winget when they're missing. The user never has to
# remember "Add Python to PATH" - winget's Python installer does it for us.
Write-Step "Checking prerequisites..."

# Refresh PATH from the registry so freshly-installed tools are visible in
# this session without a restart. winget installs to a path that may not be
# on the current session's PATH.
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Install a package via winget if it's available; returns $true on success.
function Install-ViaWinget {
    param([string]$PackageId, [string]$Label)
    Write-Step "Installing $Label (one-time)..."
    try {
        & winget install --id $PackageId --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            Refresh-Path
            Write-OK "$Label installed"
            return $true
        }
    } catch {
        Write-Warn2 "Could not auto-install $Label."
    }
    return $false
}

# Python 3.11+
# Try several launchers in order: `python` (on PATH), `py` (the Windows
# launcher, present even when `python` is not on PATH), then `python3`.
# We surface the REAL error instead of swallowing it, so a user whose
# Python is installed-but-not-on-PATH (or blocked by an Application
# Control / WDAC policy) sees why instead of a false "install Python".
$pyOk = $false
$pyErr = ""
foreach ($launcher in @("python", "py", "python3")) {
    try {
        $pv = & $launcher --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $pv -match "Python 3\.(\d+)") {
            $minor = [int]$matches[1]
            if ($minor -ge 11) {
                $pyOk = $true
                Write-OK "Python: $pv (via '$launcher')"
                break
            } else {
                Write-Err "Python 3.11+ required, found $pv (via '$launcher')"
                $pyErr = "found $pv (too old)"
            }
        } else {
            $pyErr = "$pv"
        }
    } catch {
        $pyErr = $_.Exception.Message
    }
}
if (-not $pyOk) {
    # Auto-install Python via winget (sets PATH for us - no "Add to PATH"
    # footgun). If winget isn't available or fails, fall back to the
    # download page.
    if (Install-ViaWinget "Python.Python.3.12" "Python 3.12") {
        $pyOk = $true
        Write-OK "Python installed and on PATH"
    } else {
        Write-Warn2 "Python check failed: $pyErr"
        Write-Host "  Could not auto-install Python. Install it from" -ForegroundColor Yellow
        Write-Host "  https://python.org/downloads (check 'Add Python to PATH')," -ForegroundColor Yellow
        Write-Host "  then re-run this installer." -ForegroundColor Yellow
        Start-Process "https://python.org/downloads"
        return
    }
}

# Git - needed to download and update VaultBot. Auto-install if missing.
$gitOk = $false
try {
    $gv = & git --version 2>&1
    if ($LASTEXITCODE -eq 0) { $gitOk = $true; Write-OK "Git: $gv" }
} catch {}
if (-not $gitOk) {
    if (Install-ViaWinget "Git.Git" "Git") {
        $gitOk = $true
    } else {
        Write-Warn2 "Could not auto-install Git."
        Write-Host "  Install Git from https://git-scm.com/downloads, then" -ForegroundColor Yellow
        Write-Host "  re-run this installer." -ForegroundColor Yellow
        Start-Process "https://git-scm.com/downloads"
        return
    }
}

# Ollama - a background service, harder to fully bundle. Auto-install the
# app via winget if possible; otherwise open the download page. The user
# still needs to run Ollama once (it starts a tray service), but we remove
# the "find the download" step.
$ollamaOk = $false
try {
    $ov = & ollama --version 2>&1
    if ($LASTEXITCODE -eq 0) { $ollamaOk = $true; Write-OK "Ollama: $ov" }
} catch {}
if (-not $ollamaOk) {
    if (-not (Install-ViaWinget "Ollama.Ollama" "Ollama")) {
        Write-Warn2 "Could not auto-install Ollama."
        Write-Host "  Download Ollama from https://ollama.com and run it once," -ForegroundColor Yellow
        Write-Host "  then re-run this installer." -ForegroundColor Yellow
        Start-Process "https://ollama.com"
        return
    }
    # winget installs Ollama but the service may not be running yet. Check
    # again; if still not found, tell the user to launch it once.
    try {
        $ov = & ollama --version 2>&1
        if ($LASTEXITCODE -eq 0) { $ollamaOk = $true; Write-OK "Ollama: $ov" }
    } catch {}
    if (-not $ollamaOk) {
        Write-Warn2 "Ollama installed but not running yet."
        Write-Host "  Open the Ollama app once to start its background service," -ForegroundColor Yellow
        Write-Host "  then re-run this installer." -ForegroundColor Yellow
        return
    }
}

# Obsidian - the app the user actually talks to VaultBot through. Unlike
# Python/Git/Ollama, the installer previously ASSUMED Obsidian was already
# installed, so a fresh machine would reach the end, fail to open the
# `obsidian://` deep link (no protocol handler registered), and leave the
# user with a "complete" install they couldn't use. Detect it the same way
# as the other prerequisites and auto-install it when missing.
$obsidianOk = $false
try {
    # Obsidian is rarely on PATH. Detect it via its registered URI protocol
    # handler (HKCU\Software\Classes\obsidian) or its known install paths.
    $obsidianExe = $null
    $proto = Get-ItemProperty -Path "HKCU:\Software\Classes\obsidian\shell\open\command" -ErrorAction SilentlyContinue
    if ($proto) {
        $cmd = $proto.'(default)'
        if ($cmd -match '"(?<exe>[^"]+\.exe)"') { $obsidianExe = $matches['exe'] }
    }
    if (-not $obsidianExe) {
        foreach ($candidate in @(
            (Join-Path $env:LOCALAPPDATA "Obsidian\Obsidian.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Obsidian\Obsidian.exe"),
            (Join-Path $env:ProgramFiles "Obsidian\Obsidian.exe")
        )) {
            if (Test-Path $candidate) { $obsidianExe = $candidate; break }
        }
    }
    if ($obsidianExe) { $obsidianOk = $true; Write-OK "Obsidian: $obsidianExe" }
} catch {}
if (-not $obsidianOk) {
    if (Install-ViaWinget "Obsidian.Obsidian" "Obsidian") {
        $obsidianOk = $true
        Write-OK "Obsidian installed"
        # Re-detect the exe so the final open step can launch it directly
        # if the deep link fails (winget may not register the URI handler
        # until the app is run once).
        foreach ($candidate in @(
            (Join-Path $env:LOCALAPPDATA "Obsidian\Obsidian.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Obsidian\Obsidian.exe"),
            (Join-Path $env:ProgramFiles "Obsidian\Obsidian.exe")
        )) {
            if (Test-Path $candidate) { $obsidianExe = $candidate; break }
        }
    } else {
        Write-Warn2 "Could not auto-install Obsidian."
        Write-Host "  Download Obsidian from https://obsidian.md, then" -ForegroundColor Yellow
        Write-Host "  re-run this installer." -ForegroundColor Yellow
        Start-Process "https://obsidian.md"
        return
    }
}

# -- 2. Ask the user's name --------------------------------------------------
Write-Host ""
Write-Host "  What's your name? VaultBot will call you by this." -ForegroundColor Cyan
$ownerName = Read-Host "  Your name"
if ([string]::IsNullOrWhiteSpace($ownerName)) { $ownerName = "friend" }
Write-Host ""

# -- 3. Get the repo (latest tagged release) -------------------------------
# VaultBot installs from the latest GitHub Release tag rather than moving
# `main`, so fresh installs get a stable published build instead of in-flight
# development commits.
#
# No GitHub account is required to install. Only *pushing* (contributing)
# needs an account, and that is opt-in later, not a gate on install.
#
# We add an `upstream` remote pointing at Ziggibot0/vaultbot so the
# in-Obsidian updater's `git pull upstream main` works out of the box.
# When the user later opts into "Allow contributions", the
# submit_contribution tool forks the repo and adds a `fork` remote on its
# own - no install-time sign-in needed.
#
# The repo clones into a FRAMEWORK folder ($frameworkName). Inside it, the
# `myvault/` subfolder is the user's Obsidian vault. The vault folder name
# is fixed ("myvault") so that upstream updates (new procedures, Knowledge
# notes, plugin code) always land in the right place for every user. The
# backend (vaultbot_backend/) and .venv/ live at the framework root, OUTSIDE
# the vault, so the user never sees them.
# Detect if we're already inside a VaultBot repo. This happens when:
#   1. The user cloned the repo and ran the installer from inside it.
#   2. On case-insensitive filesystems (Windows NTFS), "$PWD/VaultBot"
#      case-insensitively matches an existing "vaultbot/" folder, so
#      Test-Path returns true even though the folder name differs.
# In both cases, use the existing repo instead of cloning a nested copy.
$frameworkPath = Join-Path $PWD $frameworkName
$inExistingRepo = $false

if ((Test-Path (Join-Path $PWD "vaultbot_backend")) -and
    (Test-Path (Join-Path $PWD "setup.ps1"))) {
    # Case 1: $PWD itself is a VaultBot repo (installer run from inside it).
    $frameworkPath = $PWD
    $inExistingRepo = $true
    Write-Warn2 "Already inside a VaultBot repo -- using this folder."
} elseif (Test-Path $frameworkPath) {
    # Case 2: $frameworkPath exists. On Windows, "VaultBot" might
    # case-insensitively match "vaultbot". Verify it's a VaultBot repo
    # before using it; abort if it's an unrelated folder to avoid clobbering.
    if ((Test-Path (Join-Path $frameworkPath "vaultbot_backend")) -and
        (Test-Path (Join-Path $frameworkPath "setup.ps1"))) {
        $inExistingRepo = $true
        Write-Warn2 "Found existing VaultBot repo -- using it."
    } else {
        Write-Err "Folder '$frameworkName' already exists but isn't a VaultBot repo."
        Write-Host "  Pick a different location or remove the existing folder." -ForegroundColor Yellow
        return
    }
} else {
    # Anonymous clone - no GitHub account required to install or update.
    # git is the only prerequisite here (gh is NOT required; it's only for
    # the optional "share fixes" contribution flow, handled later).
    $gitOk = $false
    try {
        $gv = & git --version 2>&1
        if ($LASTEXITCODE -eq 0) { $gitOk = $true }
    } catch {}

    if (-not $gitOk) {
        # git is missing. Offer to install it (free, one-click). If the user
        # declines, we can't clone, so installation aborts with a clear path.
        Write-Step "Git not found"
        Write-Host "  VaultBot needs Git to download and update itself." -ForegroundColor Cyan
        Write-Host "  It's a free, one-click download." -ForegroundColor Cyan
        Write-Host ""
        $installGit = Read-Host "  Install Git now? (y/n)"
        if ($installGit -match "^(y|yes)$") {
            Write-Step "Installing Git..."
            try {
                & winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
                if ($LASTEXITCODE -eq 0) {
                    # winget installs to a path that may not be on the current
                    # session's PATH. Refresh PATH from the registry.
                    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
                    $gv = & git --version 2>&1
                    if ($LASTEXITCODE -eq 0) { $gitOk = $true; Write-OK "Git installed" }
                }
            } catch {
                Write-Warn2 "Could not install Git automatically."
            }
        }
    }

    if (-not $gitOk) {
        Write-Err "Git is required to install VaultBot."
        Write-Host "  Install Git from https://git-scm.com/downloads, then" -ForegroundColor Yellow
        Write-Host "  re-run this installer." -ForegroundColor Yellow
        return
    }

    Write-Step "Resolving latest VaultBot release..."
    $releaseTag = Get-LatestReleaseTag
    if ([string]::IsNullOrWhiteSpace($releaseTag)) {
        Write-Err "Could not resolve latest VaultBot release tag."
        Write-Host "  Check your network connection and GitHub availability," -ForegroundColor Yellow
        Write-Host "  then re-run this installer." -ForegroundColor Yellow
        return
    }

    Write-Step "Downloading VaultBot ($releaseTag)..."
    & git clone --branch $releaseTag --depth 1 https://github.com/Ziggibot0/vaultbot.git $frameworkName
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $frameworkPath)) {
        Write-Err "Could not download VaultBot release $releaseTag."
        Write-Host "  Check your network connection, then re-run this installer." -ForegroundColor Yellow
        return
    }

    # Add an `upstream` remote so the in-Obsidian updater can `git pull
    # upstream main`. (A plain clone already sets `origin` to upstream, but
    # the updater prefers `upstream` and the contribution flow expects it.)
    Push-Location $frameworkPath
    try {
        & git remote add upstream https://github.com/Ziggibot0/vaultbot.git 2>$null
    } finally { Pop-Location }

    Write-OK "VaultBot downloaded at release $releaseTag"

    # Optional: detect GitHub CLI for the contribution flow. This is NOT
    # required to use or update VaultBot - the user only needs to sign in
    # the first time they opt into "Allow contributions" and their vaultbot
    # has something to give back. We just note availability, never gate on it.
    $ghOk = $false
    try {
        $gv = & gh --version 2>&1
        if ($LASTEXITCODE -eq 0) { $ghOk = $true }
    } catch {}

    if ($ghOk) {
        try {
            & gh auth status *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-OK "GitHub CLI detected - you can share fixes with the community (optional)."
            }
        } catch {}
    } else {
        Write-Host ""
        Write-Host "  Tip: to share fixes with the community later, install the" -ForegroundColor DarkGray
        Write-Host "  GitHub CLI and sign in. You don't need it to use VaultBot." -ForegroundColor DarkGray
        Write-Host ""
    }
}

# -- 3a. Repair/update an existing install to the latest release -----------
# When re-run over an existing install, bring the CODE up to the latest
# release. Installs are shallow, detached clones, so we do NOT merge (a
# shallow clone shares no history with the fetched tag and a merge aborts on
# "unrelated histories" - the bug that left older installs unable to update).
# Instead we fetch the release tag and `reset --hard` onto it: this lands the
# code EXACTLY on the release and can never conflict. reset --hard never
# touches UNTRACKED files, so all notes, chat logs, API keys, learned state,
# and bot-authored procedures are preserved; every runtime-state/log file is
# gitignored and stays put.
if ($inExistingRepo -and (Test-Path (Join-Path $frameworkPath ".git"))) {
    Write-Step "Updating existing VaultBot to the latest release..."
    $repairTag = Get-LatestReleaseTag
    if ([string]::IsNullOrWhiteSpace($repairTag)) {
        Write-Warn2 "Could not resolve latest release - keeping current code."
    } else {
        Push-Location $frameworkPath
        try {
            # Prefer the upstream remote; fall back to origin.
            $repairRemote = "upstream"
            & git remote get-url upstream *> $null
            if ($LASTEXITCODE -ne 0) { $repairRemote = "origin" }

            # Back up any tracked file with local modifications first, so a
            # bot/user edit to a repo-tracked file is never silently lost.
            $modified = (& git diff --name-only HEAD 2>$null) | Where-Object { $_ -and $_.Trim() }
            if ($modified) {
                $bts = (Get-Date).ToString("yyyy-MM-ddTHH-mm-ss")
                $backupDir = Join-Path $frameworkPath "vaultbot_backend\.vaultbot-update-backup\$bts"
                foreach ($rel in $modified) {
                    $srcp = Join-Path $frameworkPath $rel
                    if (Test-Path $srcp) {
                        $dstp = Join-Path $backupDir ($rel -replace '[\\/]', '__')
                        New-Item -ItemType Directory -Force -Path (Split-Path $dstp) | Out-Null
                        Copy-Item $srcp $dstp -Force
                    }
                }
                Write-Warn2 "Backed up $($modified.Count) locally-modified file(s) before update."
            }

            $fetchOk = $false
            foreach ($spec in @($repairTag, "refs/tags/$repairTag")) {
                & git fetch --depth 1 $repairRemote $spec *> $null
                if ($LASTEXITCODE -eq 0) { $fetchOk = $true; break }
            }
            if ($fetchOk) {
                & git reset --hard FETCH_HEAD *> $null
                if ($LASTEXITCODE -eq 0) {
                    Write-OK "Updated to release $repairTag"
                } else {
                    Write-Warn2 "Could not apply $repairTag - keeping current code."
                }
            } else {
                Write-Warn2 "Could not fetch $repairTag - keeping current code."
            }
        } finally { Pop-Location }
    }
}

# -- 3b. Set the vault path ------------------------------------------------
# The repo ships a `myvault/` subfolder (the user's Obsidian vault). The
# folder name is FIXED to "myvault" so that `git pull` updates (new
# procedures, Knowledge notes, plugin code) always merge into the right
# place. Allowing users to rename the vault folder broke updates: a
# renamed vault meant upstream changes to vaultbot-stuff/System/Procedures/
# etc. landed in `vault/` (the old name) while the user's vault lived
# elsewhere, so nobody got procedure updates. Keeping the name fixed
# eliminates that entire class of sync bugs.
$vaultPath = Join-Path $frameworkPath "myvault"
if (-not (Test-Path $vaultPath)) {
    New-Item -ItemType Directory -Path $vaultPath | Out-Null
    Write-Warn2 "No shipped myvault/ found - created an empty 'myvault' folder."
}

# Now that $vaultPath exists, set the install-state file path so steps
# 4-7 can resume if the user re-runs after a partial install.
$script:stateFile = Join-Path $frameworkPath ".vaultbot-install-state.json"
if (Test-Path $script:stateFile) {
    Write-Warn2 "Found previous install state - resuming where you left off."
}

# -- 4. Create the Python virtual environment --------------------------------
# `.venv` lives at the FRAMEWORK root (outside the vault), so the user never
# sees it in Obsidian.
$venvPath = Join-Path $frameworkPath ".venv"
if (Test-StepDone "venv_created") {
    Write-Warn2 "Virtual environment already created -- skipping."
} elseif (Test-Path (Join-Path $venvPath "Scripts\python.exe")) {
    Write-Warn2 "Virtual environment already exists -- skipping."
    Set-StepDone "venv_created"
} else {
    Write-Step "Creating Python environment (a few seconds)..."
    Push-Location $frameworkPath
    try { & python -m venv .venv } finally { Pop-Location }
    Write-OK "Virtual environment created"
    Set-StepDone "venv_created"
}

# -- 5. Install dependencies -------------------------------------------------
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$reqPath    = Join-Path $frameworkPath "vaultbot_backend\requirements.txt"

if (Test-StepDone "deps_installed") {
    Write-Warn2 "Dependencies already installed -- skipping."
} else {
    Write-Step "Installing dependencies (5-15 min, one-time only)..."
    Write-Host "  Grab a coffee. This is the longest step." -ForegroundColor DarkGray
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r $reqPath
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency installation failed. See errors above."
        Write-Host "  Re-run the same command - it picks up from here." -ForegroundColor Yellow
        return
    }
    Write-OK "Dependencies installed"
    Set-StepDone "deps_installed"
}

# -- 5b. Set up SearXNG search container (optional, needs Docker) -----------
# SearXNG is a self-hosted meta-search engine that gives VaultBot's research
# feature access to Google, Brave, DuckDuckGo, etc. via one private container.
# Without it, research falls back to keyless backends (DDG Lite, Marginalia,
# arXiv) which are rate-limited and less reliable. With Docker installed, we
# start the container automatically so research works out of the box.
if (Test-StepDone "searxng_setup") {
    Write-Warn2 "SearXNG search container already set up -- skipping."
} else {
    $dockerOk = $false
    $dockerDaemonOk = $false
    try {
        $dv = & docker --version 2>&1
        if ($LASTEXITCODE -eq 0) { $dockerOk = $true; Write-OK "Docker: $dv" }
    } catch {}

    if ($dockerOk) {
        # `docker --version` only proves the CLI is installed. Probe the
        # daemon with `docker info` - on Windows the daemon lives in Docker
        # Desktop, and if the app isn't running every `docker ps`/`run` call
        # fails with a pipe-not-found error.
        try {
            & docker info *> $null
            if ($LASTEXITCODE -eq 0) { $dockerDaemonOk = $true }
        } catch {}
    }

    if ($dockerOk -and -not $dockerDaemonOk) {
        Write-Warn2 "Docker is installed but the Docker daemon isn't running."
        Write-Host "  Start Docker Desktop, wait for it to finish starting, then" -ForegroundColor DarkGray
        Write-Host "  re-run setup to enable SearXNG web search." -ForegroundColor DarkGray
        # Do NOT mark searxng_setup done so the next run retries.
    } elseif ($dockerDaemonOk) {
        Write-Step "Starting SearXNG search container (one-time, ~30 seconds)..."

        try {
            # Check if the container is already running
            $existing = & docker ps -a --filter "name=vaultbot_searxng" --format "{{.Names}}" 2>$null
            if ($existing -eq "vaultbot_searxng") {
                Write-Warn2 "SearXNG container already exists -- starting it."
                & docker start vaultbot_searxng 2>$null | Out-Null
            } else {
                # Run the container with the bundled settings file mounted.
                $settingsPath = Join-Path $frameworkPath "vaultbot_backend\searxng_settings.yml"
                # Convert to Windows-style absolute path for Docker bind mount
                $settingsPath = (Get-Item $settingsPath -ErrorAction SilentlyContinue).FullName
                if ($settingsPath) {
                    # Docker on Windows needs forward-slash or escaped backslashes
                    $mountPath = $settingsPath -replace '\\', '/'
                    $runArgs = @("run","-d","--name","vaultbot_searxng","-p","8080:8080","-v","${mountPath}:/etc/searxng/settings.yml:ro","searxng/searxng")
                    & docker @runArgs 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
                } else {
                    # No settings file -- run without the mount (uses SearXNG defaults)
                    & docker run -d --name vaultbot_searxng -p 8080:8080 searxng/searxng 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
                }
            }

            # Wait for the container to be ready (up to 30 seconds)
            $ready = $false
            for ($i = 0; $i -lt 15; $i++) {
                Start-Sleep -Seconds 2
                try {
                    $resp = Invoke-WebRequest -Uri "http://localhost:8080" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
                    if ($resp.StatusCode -eq 200) { $ready = $true; break }
                } catch {}
            }

            if ($ready) {
                Write-OK "SearXNG search container is running on port 8080"
                Write-Host "  VaultBot's research feature can now search Google, Brave, and more." -ForegroundColor DarkGray
            } else {
                Write-Warn2 "SearXNG container started but not responding yet."
                Write-Host "  It may need a few more seconds. Research will work once it's ready." -ForegroundColor DarkGray
            }
            Set-StepDone "searxng_setup"
        } catch {
            Write-Warn2 "SearXNG container setup failed: $_"
            Write-Host "  Research will fall back to keyless backends. Re-run setup to retry." -ForegroundColor DarkGray
            # Do NOT mark searxng_setup done so the next run retries.
        }
    } else {
        Write-Warn2 "Docker not found -- SearXNG search container skipped."
        Write-Host "  Without Docker, VaultBot's research feature uses keyless backends" -ForegroundColor DarkGray
        Write-Host "  (DuckDuckGo Lite, Marginalia, arXiv) which work but are rate-limited." -ForegroundColor DarkGray
        Write-Host "  Install Docker Desktop (https://docker.com) and re-run setup to enable" -ForegroundColor DarkGray
        Write-Host "  full web search via SearXNG." -ForegroundColor DarkGray
        Set-StepDone "searxng_setup"
    }
}

# -- 6. Pull embedding + small models via Ollama ---------------------------
# The lightweight embedding model (nomic-embed-text, ~270 MB) and the small
# classification model (qwen3.5:4b, ~4 GB) are auto-pulled. The chat/synthesis
# LLM is handled in step 6b based on the user's choice (local vs cloud API).
if (Test-StepDone "models_pulled") {
    Write-Warn2 "Embedding + small models already downloaded -- skipping."
} else {
    Write-Step "Downloading embedding model (~270 MB, one-time only)..."
    & ollama pull nomic-embed-text
    Write-OK "Embedding model ready"
    # The small model (qwen3.5:4b) drives the small cartridge: cheap
    # classification, tagging, and routing. It MUST be >= ~3-4B - a sub-1B
    # model (like the old qwen3.5:0.8b) can't reliably classify or route,
    # which makes VaultBot feel broken. Pull it here so the one-liner is
    # truly all a user needs (no manual `ollama pull` afterward).
    Write-Step "Downloading small model (qwen3.5:4b, ~4 GB) for classification/routing..."
    & ollama pull qwen3.5:4b
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Small model pull failed. You can run 'ollama pull qwen3.5:4b' manually later."
    } else {
        Write-OK "Small model ready"
    }
    Set-StepDone "models_pulled"
}

# -- 6b. Ask: local chat model or cloud API? --------------------------------
# The embedding model (above) is mandatory and always local. The CHAT
# model is the user's choice: a local Ollama model (free, private, heavy)
# or a cloud API key (zero local compute, recommended for laptops).
#
# We default to local (LLM_BACKEND=ollama) because it's zero-config: the
# user already has Ollama installed for embeddings. If they choose cloud,
# we write LLM_BACKEND=openai into .env and they add their key later.
$chatBackend = "ollama"  # default
$chatModel   = ""
$apiKey      = ""
$apiBaseUrl  = ""
$apiModel    = ""
if (-not (Test-StepDone "chat_backend_chosen")) {
    Write-Host ""
    Write-Host "  VaultBot needs a chat model to talk to you." -ForegroundColor Cyan
    Write-Host "  Two options:" -ForegroundColor White
    Write-Host "    1. Local (free, private, uses Ollama - already installed)" -ForegroundColor White
    Write-Host "       Downloads a model (1-5 GB). Best if you have 8+ GB RAM." -ForegroundColor DarkGray
    Write-Host "    2. Cloud API (zero local compute, recommended for laptops)" -ForegroundColor White
    Write-Host "       Free OpenRouter tier - no credit card needed." -ForegroundColor DarkGray
    Write-Host ""
    $choice = Read-Host "  Pick 1 or 2 (default: 1)"
    if ($choice -eq "2") {
        $chatBackend = "openai"
        Write-Host ""
        Write-Host "  VaultBot will use a cloud model (recommended for laptops)." -ForegroundColor Cyan
        Write-Host "  The easiest free option is OpenRouter - it has a free tier" -ForegroundColor White
        Write-Host "  with no credit card required." -ForegroundColor White
        Write-Host ""
        Write-Host "  A browser window will open so you can create an account." -ForegroundColor Cyan
        Write-Host "  Then click 'Create Key', copy it, and paste it back here." -ForegroundColor Cyan
        Write-Host ""
        Start-Process "https://openrouter.ai"
        Start-Process "https://openrouter.ai/keys"
        Write-Host "  (If the browser didn't open, go to https://openrouter.ai/keys)" -ForegroundColor DarkGray
        Write-Host ""
        $apiKey = Read-Host "  Paste your API key (or press Enter to skip and add it later)"
        if ([string]::IsNullOrWhiteSpace($apiKey)) {
            Write-Host ""
            Write-Host "  No problem - you can add your key later. After setup, edit" -ForegroundColor Yellow
            Write-Host "  the .env file and set:" -ForegroundColor Yellow
            Write-Host "    LLM_API_KEY=sk-..." -ForegroundColor White
            Write-Host "    LLM_BASE_URL=https://openrouter.ai/api/v1" -ForegroundColor White
            Write-Host "    LLM_MODEL=z-ai/glm-5.2:free" -ForegroundColor White
            Write-Host "  (Or set LLM_BACKEND=ollama in .env to use local instead.)" -ForegroundColor DarkGray
        } else {
            $apiKey = $apiKey.Trim()
            $apiBaseUrl = "https://openrouter.ai/api/v1"
            # Pick the best free model that can drive the big cartridge.
            # Live-query OpenRouter's free list so a new user never lands on
            # a deprecated or rate-limited model; fall back to a known-good
            # default if the query fails (offline).
            Write-Host ""
            Write-Host "  Picking a free model for you..." -ForegroundColor Cyan
            $apiModel = Get-FreeCloudModel
            if ([string]::IsNullOrWhiteSpace($apiModel)) {
                $apiModel = "z-ai/glm-5.2:free"
                Write-Warn2 "Couldn't reach OpenRouter to pick a model - using a default."
            }
            Write-OK "API key saved - VaultBot will use $apiModel (free tier)."
        }
    } else {
        $chatBackend = "ollama"
        Write-Host ""
        Write-Host "  Which model? Popular choices:" -ForegroundColor Cyan
        Write-Host "    qwen3:latest       (4-8B, good balance, ~4 GB)" -ForegroundColor White
        Write-Host "    llama3.2:latest    (3B, lightweight, ~2 GB)" -ForegroundColor White
        Write-Host "    qwen3.6:latest      (larger, best quality, ~8 GB)" -ForegroundColor White
        Write-Host "  Type a model name or press Enter for qwen3:latest" -ForegroundColor DarkGray
        $chatModel = Read-Host "  Model name"
        if ([string]::IsNullOrWhiteSpace($chatModel)) { $chatModel = "qwen3:latest" }
    }
    # Persist the choice so a re-run after a failed step doesn't reset it.
    Set-StateValue "chat_backend" $chatBackend
    Set-StateValue "chat_model" $chatModel
    Set-StateValue "api_key" $apiKey
    Set-StateValue "api_base_url" $apiBaseUrl
    Set-StateValue "api_model" $apiModel
    Set-StepDone "chat_backend_chosen"
} else {
    # Resume: restore the previously-chosen backend + key from state.
    $chatBackend = Get-StateValue "chat_backend"
    if (-not $chatBackend) { $chatBackend = "ollama" }
    $chatModel  = Get-StateValue "chat_model"
    $apiKey     = Get-StateValue "api_key"
    $apiBaseUrl = Get-StateValue "api_base_url"
    $apiModel   = Get-StateValue "api_model"
    if (Test-StepDone "chat_model_pulled") {
        Write-Warn2 "Chat model choice already made -- skipping."
    }
}

# -- 6c. Pull the chat model if local --------------------------------------
if ($chatBackend -eq "ollama" -and $chatModel -and -not (Test-StepDone "chat_model_pulled")) {
    Write-Step "Downloading chat model: $chatModel (this can take a while)..."
    Write-Host "  Grab a coffee. Large models take 5-30 min depending on your connection." -ForegroundColor DarkGray
    & ollama pull $chatModel
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Chat model pull failed. You can run 'ollama pull $chatModel' manually later."
        Write-Host "  VaultBot will still start - you'll just need to pull a model before chatting." -ForegroundColor Yellow
    } else {
        Write-OK "Chat model ready: $chatModel"
    }
    Set-StepDone "chat_model_pulled"
}

# -- 7. Write .env with the user's name + LLM config -------------------------
$envExample = Join-Path $frameworkPath ".env.example"
$envFile    = Join-Path $frameworkPath ".env"
if (Test-StepDone "env_written") {
    Write-Warn2 "Config already written -- skipping."
} elseif (Test-Path $envExample) {
    $content = Get-Content $envExample -Raw
    $content = $content -replace 'VAULTBOT_OWNER=.*', "VAULTBOT_OWNER=$ownerName"
    $content = $content -replace 'VAULT_PATH=.*', "VAULT_PATH=$vaultPath"
    $content = $content -replace 'LLM_BACKEND=.*', "LLM_BACKEND=$chatBackend"
    if ($chatBackend -eq "ollama" -and $chatModel) {
        $content = $content -replace 'OLLAMA_LLM_MODEL=.*', "OLLAMA_LLM_MODEL=$chatModel"
    }
    if ($chatBackend -eq "openai" -and $apiKey) {
        $content = $content -replace 'LLM_API_KEY=.*', "LLM_API_KEY=$apiKey"
        $content = $content -replace 'LLM_BASE_URL=.*', "LLM_BASE_URL=$apiBaseUrl"
        $content = $content -replace 'LLM_MODEL=.*', "LLM_MODEL=$apiModel"
    }
    # Write UTF-8 WITHOUT BOM (BOM breaks Python's dotenv parser)
    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.UTF8Encoding]::new($false))
    Write-OK "Configured -- VaultBot will call you $ownerName"
    if ($chatBackend -eq "openai" -and -not $apiKey) {
        Write-Host "  Don't forget: add your LLM_API_KEY to .env to use your cloud model." -ForegroundColor Yellow
    }
    Set-StepDone "env_written"
} else {
    Write-Warn2 ".env.example not found -- skipping .env creation"
    Set-StepDone "env_written"
}

# -- 7b. Configure Obsidian (hide repo-hygiene docs) -------------------------
# The repo root carries GitHub-facing docs (AGENTS.md, README.md, SECURITY.md,
# LICENSE, CONTRIBUTING.md) that must stay at the root for GitHub to see them,
# but they should not clutter the user's Obsidian file explorer. Obsidian's
# userIgnoreFilters (in .obsidian/app.json) hides them. We MERGE into any
# existing filters so we never clobber a user's own ignore list.
$obsidianDir = Join-Path $vaultPath ".obsidian"
$appJson = Join-Path $obsidianDir "app.json"
$repoDocs = @("AGENTS.md", "README.md", "SECURITY.md", "LICENSE", "CONTRIBUTING.md")
if (Test-StepDone "obsidian_ignore_configured") {
    Write-Warn2 "Obsidian ignore filters already configured -- skipping."
} else {
    if (-not (Test-Path $obsidianDir)) { New-Item -ItemType Directory -Path $obsidianDir | Out-Null }
    $app = @{}
    if (Test-Path $appJson) {
        try {
            # ConvertFrom-Json returns a PSCustomObject in PS 5.1, not a
            # hashtable. Copy its properties into a real hashtable so we can
            # add/overwrite keys (same pattern as Set-StepDone above).
            $obj = Get-Content $appJson -Raw | ConvertFrom-Json
            foreach ($prop in $obj.PSObject.Properties) {
                $app[$prop.Name] = $prop.Value
            }
        } catch { $app = @{} }
    }
    $filters = @()
    if ($app.ContainsKey("userIgnoreFilters") -and $app["userIgnoreFilters"]) {
        $filters = @($app["userIgnoreFilters"])
    }
    foreach ($doc in $repoDocs) {
        if ($filters -notcontains $doc) { $filters += $doc }
    }
    $app["userIgnoreFilters"] = $filters
    # Write UTF-8 WITHOUT BOM (BOM breaks JSON parsers).
    [System.IO.File]::WriteAllText($appJson, ($app | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    Write-OK "Obsidian configured to hide repo docs from the file explorer"
    Set-StepDone "obsidian_ignore_configured"
}

# -- 7c. Configure Obsidian (dark mode) --------------------------------------
# Obsidian's appearance.json controls the theme. "baseTheme": "obsidian" is
# the built-in dark theme. We write it so a fresh install opens in dark mode
# without the user having to toggle it manually. We MERGE into any existing
# appearance.json so we never clobber a user's cssTheme or other settings.
$appearanceJson = Join-Path $obsidianDir "appearance.json"
if (Test-StepDone "obsidian_dark_mode") {
    Write-Warn2 "Obsidian dark mode already configured -- skipping."
} else {
    if (-not (Test-Path $obsidianDir)) { New-Item -ItemType Directory -Path $obsidianDir | Out-Null }
    $appearance = @{}
    if (Test-Path $appearanceJson) {
        try {
            $obj = Get-Content $appearanceJson -Raw | ConvertFrom-Json
            foreach ($prop in $obj.PSObject.Properties) {
                $appearance[$prop.Name] = $prop.Value
            }
        } catch { $appearance = @{} }
    }
    $appearance["baseTheme"] = "obsidian"
    # Write UTF-8 WITHOUT BOM (BOM breaks JSON parsers).
    [System.IO.File]::WriteAllText($appearanceJson, ($appearance | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    Write-OK "Obsidian configured to open in dark mode"
    Set-StepDone "obsidian_dark_mode"
}

# -- 7d. Register the vault with Obsidian ------------------------------------
# The `obsidian://open?path=...` deep link in step 8 can only open a vault
# Obsidian ALREADY KNOWS ABOUT. Per the Obsidian URI docs, the `path`
# parameter "will cause the app to search for the most specific vault which
# contains the specified file path" - and that search only covers vaults
# registered in %APPDATA%\obsidian\obsidian.json (the vault manager's
# store). An unregistered folder is NOT auto-added: on a machine where
# Obsidian has never opened this vault, the deep link fails with a
# "Vault not found" error dialog (or is ignored entirely on a truly fresh
# install, which lands in the vault manager instead). Exactly what every
# new user hits at the end of this installer.
#
# Writing the vault into that store here - merging, never clobbering other
# vaults - makes the deep link open straight into the vault on first
# launch. The store maps vault IDs (16 hex chars) to entries:
#   {"vaults": {"<id>": {"path": "C:\\path\\to\\vault", "ts": <ms>,
#                        "open": true}}}
# We skip the rewrite while Obsidian is RUNNING: on quit it rewrites the
# file from its in-memory copy and would silently drop our entry.
$obsidianJson    = Join-Path $env:APPDATA "obsidian\obsidian.json"
$obsidianRunning = @(Get-Process -Name "Obsidian" -ErrorAction SilentlyContinue).Count -gt 0
if (Test-StepDone "vault_registered") {
    Write-Warn2 "Vault already registered with Obsidian -- skipping."
} elseif (-not (Test-Path $obsidianJson)) {
    Write-Warn2 "No Obsidian vault store yet (first-ever launch) -- nothing to pre-register."
    Write-Host "  If asked on first launch, choose 'Open folder as vault' and" -ForegroundColor DarkGray
    Write-Host "  select the vault path printed below." -ForegroundColor DarkGray
    Set-StepDone "vault_registered"
} elseif ($obsidianRunning) {
    Write-Warn2 "Obsidian is currently running -- skipping vault registration."
    Write-Host "  Close Obsidian and re-run the installer (it resumes from here)" -ForegroundColor DarkGray
    Write-Host "  to get the one-click open." -ForegroundColor DarkGray
} else {
    try {
        $obj = Get-Content $obsidianJson -Raw | ConvertFrom-Json
        $vaults = @{}
        if ($obj.PSObject.Properties["vaults"] -and $obj.vaults) {
            foreach ($prop in $obj.vaults.PSObject.Properties) {
                $entry = @{}
                if ($prop.Value -is [System.Management.Automation.PSCustomObject]) {
                    foreach ($p in $prop.Value.PSObject.Properties) { $entry[$p.Name] = $p.Value }
                } else {
                    $entry["path"] = "$($prop.Value)"
                }
                $entry["open"] = $false   # the target vault is re-opened below
                $vaults[$prop.Name] = $entry
            }
        }
        $matchId = $null
        foreach ($id in @($vaults.Keys)) {
            if ("$($vaults[$id]['path'])" -ieq "$vaultPath") { $matchId = $id }
        }
        if ($matchId) {
            $vaults[$matchId]["open"] = $true
            $vaults[$matchId]["ts"]   = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        } else {
            $newId = -join ((1..16) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
            $vaults[$newId] = @{
                path = "$vaultPath"
                ts   = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                open = $true
            }
        }
        # Write UTF-8 WITHOUT BOM (BOM breaks JSON parsers).
        [System.IO.File]::WriteAllText($obsidianJson, (@{ vaults = $vaults } | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
        Write-OK "Vault registered with Obsidian - the deep link below opens it directly"
        Set-StepDone "vault_registered"
    } catch {
        # Non-fatal: registration is an optimization. Worst case Obsidian's
        # vault picker appears at first launch instead of the vault itself.
        Write-Warn2 "Could not pre-register the vault with Obsidian: $_"
        Write-Host "  If asked on first launch, use 'Open folder as vault'." -ForegroundColor DarkGray
    }
}

# -- 8. Done -- open Obsidian ------------------------------------------------
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

# Try to open Obsidian deep-linked to the vault.
#
# The vault was registered with Obsidian in step 7d, so this deep link
# resolves against a KNOWN vault. The `path` parameter may point at the
# vault folder itself or any file inside it: Obsidian picks the most
# specific registered vault containing that path. Pointing at the folder
# also makes this work in the edge case where registration was skipped
# (Obsidian first-ever run / was running during install) but the user later
# added the vault manually, and keeps step 8 functional on a re-run.
#
# If Obsidian still isn't installed (e.g. winget failed and the user
# declined), fall back to launching the app directly, so the user is never
# left with a "complete" install they can't open.
$openTarget = $vaultPath
try {
    $uri = "obsidian://open?path=$([uri]::EscapeDataString($openTarget))"
    Start-Process $uri
} catch {
    Write-Warn2 "Couldn't auto-open Obsidian via deep link."
    if ($obsidianExe) {
        Write-Host "  Launching Obsidian directly..." -ForegroundColor DarkGray
        Start-Process $obsidianExe
    } else {
        Write-Host "  Open Obsidian manually and choose 'Open folder as vault' ->" -ForegroundColor Yellow
        Write-Host "  select the folder above." -ForegroundColor Yellow
    }
}