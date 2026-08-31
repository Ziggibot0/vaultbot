# VaultBot one-shot diagnostic - run on the affected machine with:
#   powershell -ExecutionPolicy Bypass -File .\vaultbot_doctor.ps1 [-FrameworkRoot "C:\path\to\vaultbot-root"]
param(
  [string]$FrameworkRoot = ""
)
$ErrorActionPreference = "Continue"
Write-Host "=== VaultBot diagnostic ===" -ForegroundColor Cyan
if (-not $FrameworkRoot) {
  $cands = @(Get-ChildItem $env:USERPROFILE -Directory -Filter "vaultbot*" -ErrorAction SilentlyContinue) +
           @(Get-ChildItem $env:USERPROFILE\Desktop -Directory -Filter "vaultbot*" -ErrorAction SilentlyContinue) +
           @(Get-ChildItem $env:LOCALAPPDATA -Directory -Filter "vaultbot*" -ErrorAction SilentlyContinue)
  if ($cands.Count -eq 0) { Write-Host "No vaultbot* folder found under USERPROFILE/Desktop/LocalAppData - pass -FrameworkRoot" -ForegroundColor Red; exit 1 }
  $FrameworkRoot = $cands[0].FullName
}
Write-Host "framework root: $FrameworkRoot"
$man = Get-Content "$FrameworkRoot\myvault\.obsidian\plugins\vaultbot\manifest.json" -Raw | ConvertFrom-Json
Write-Host "installed plugin version: $($man.version)"
# Entailment-gate probe: v1.5.3 had provenance_runtime.py; fixed versions do not.
if (Test-Path "$FrameworkRoot\vaultbot_backend\provenance_runtime.py") {
  Write-Host "ENTAILMENT GATE CODE PRESENT (old, answer-blocking build!)" -ForegroundColor Red
} else {
  Write-Host "entailment gate: absent (good)" -ForegroundColor Green
}
$pj = "$FrameworkRoot\providers.json"
if (Test-Path $pj) {
  $cfg = Get-Content $pj -Raw | ConvertFrom-Json
  Write-Host "providers.json: providers=$($cfg.providers.Count) models=$($cfg.models.Count)"
  Write-Host "  roles: big='$($cfg.roles.big)' small='$($cfg.roles.small)' vision='$($cfg.roles.vision)'"
  if ($cfg.models.Count -eq 0) { Write-Host "  -> NO MODELS IN THE POT: fresh boot created an empty registry BEFORE .env could migrate." -ForegroundColor Red }
} else { Write-Host "providers.json: MISSING (first boot will migrate from .env)" -ForegroundColor Yellow }
$envFile = "$FrameworkRoot\.env"
if (Test-Path $envFile) {
  Write-Host ".env (LLM lines, keys redacted):"
  Select-String -Path $envFile -Pattern "^(LLM_|OLLAMA_HOST|SMALL_MODEL|VISION_MODEL)" | ForEach-Object { ($_.Line -replace "KEY=.*","KEY=***") }
} else { Write-Host ".env: MISSING" -ForegroundColor Red }
Write-Host "backend :8000 -> " -NoNewline
try { Invoke-RestMethod http://localhost:8000/health -TimeoutSec 3 | Out-Null; Write-Host "UP" -ForegroundColor Green }
catch { Write-Host "DOWN" -ForegroundColor Red }
Write-Host "ollama models on this machine:"
& ollama list
$sl = Get-ChildItem "$FrameworkRoot\vaultbot_backend\sessions\*.jsonl" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($sl) {
  Write-Host "newest session: $($sl.Name) ($([math]::Round($sl.Length/1KB)) KB, $($sl.LastWriteTime))"
  Write-Host "--- last 12 events (trim to 200 chars) ---"
  Get-Content $sl.FullName -Tail 12 | ForEach-Object { if ($_.Length -gt 200) { $_.Substring(0,200) } else { $_ } }
} else { Write-Host "no session logs found" -ForegroundColor Red }
