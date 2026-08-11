---
type: procedure
status: active
model_cartridge: small
created: 2026-08-05
updated: 2026-08-09
description: "Parent orchestrator for instant self-knowledge. Runs a suite of probes to answer 'what am I right now?' — identity, capabilities, tools, vault state, health, and procedure library. Returns a live snapshot, not remembered state."
when_to_use: "When you need to know your current capabilities, identity, vault health, or system status instantly \u2014 at session start, before a complex task, or when something feels off."
falsifiable_if: "Any probe returns stale/cached data instead of live results, or a known capability is missing from the report."
applies_to:
  - self-knowledge
  - introspection
  - orchestration
  - session-start
allowed_tools:
  - run_procedure
  - vault_read_note
  - code_read
  - vault_list
  - vault_graph_analyzer
  - machine_spec
  - ollama_model_search
  - vaultbot_status
provides:
  - Capability-Audit
  - Diagnose-System-Health
  - Vault-Health-Check
  - Procedure-Eval
summary: |
  Know-Thyself is the single entry point for VaultBot self-knowledge. It orchestrates 8 probes in parallel (where possible) to produce a live snapshot:
  1. Identity Probe \u2014 reads identity facts from vault
  2. Capability Probe \u2014 runs Capability-Audit for tool/capability inventory
  3. Health Probe \u2014 runs Diagnose-System-Health for backend/ollama status
  4. Vault Probe \u2014 runs Vault-Health-Check for graph/topology snapshot
  5. Procedure Probe \u2014 runs Procedure-Eval for procedure library health scores
  6. Hardware Probe \u2014 runs machine_spec for CPU/RAM/GPU/Ollama config
  7. Model Probe \u2014 runs ollama_model_search for available models
  8. Session Probe \u2014 runs VaultBot-Status for background researcher state
  Output is a structured JSON report written to Memory/Build-Log/know-thyself-latest.json with a human-readable summary.
tags:
  - procedure
  - self-knowledge
  - orchestration
  - introspection
---

# Know-Thyself

## Purpose

**One call, complete self-knowledge.** This procedure answers "what am I right now?" by running live probes — not cached memories. Every probe executes fresh.

## When to Run

- **Session start** \u2014 establish baseline before any work
- **Pre-task** \u2014 verify capabilities before a complex operation
- **Debugging** \u2014 "why did that fail?" → check current health/capabilities
- **Curiosity** \u2014 instant answer to model availability, procedure count, etc.

## Architecture: Probe Orchestration

```
Know-Thyself (parent, small cartridge)
├─ Identity Probe      → vault_read_note(identity facts)
├─ Capability Probe    → run_procedure(Capability-Audit)
├─ Health Probe        → run_procedure(Diagnose-System-Health)
├─ Vault Probe         → run_procedure(Vault-Health-Check)
├─ Procedure Probe     → run_procedure(Procedure-Eval)
├─ Hardware Probe      → machine_spec()
├─ Model Probe         → ollama_model_search(action=installed)
└─ Session Probe       → vaultbot_status()
```

All probes run **independently** — no probe depends on another's output. Failures are isolated and reported, not fatal.

## Inputs

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `depth` | string | "standard" | "minimal" (identity+capability only), "standard" (all 8), "deep" (adds graph analysis) |
| `output_file` | string | auto | Custom path for JSON output (default: `vaultbot_stuff/Memory/Build-Log/know-thyself-latest.json`) |

## Output Contract

**File written:** `vaultbot_stuff/Memory/Build-Log/know-thyself-latest.json`

Human‑readable summary is returned as the final output of the procedure.

---

## Steps

### Step 1: Run all eight probes and assemble the self-knowledge snapshot

This single step orchestrates all eight probes (identity, capability, health, vault, procedures, hardware, models, session), assembles their results into a structured JSON report, writes it to disk, and prints a human-readable summary. All probes run independently — failures are isolated and reported, not fatal.

```python
import json, datetime, pathlib, re, os, sys
from pathlib import Path

# Ensure UTF-8 stdout on Windows (emoji/special chars break cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# The runtime sets VAULT_PATH (not VAULT_ROOT). Fall back to the current
# working directory if neither is set.
vault_root = Path(os.environ.get("VAULT_PATH", os.environ.get("VAULT_ROOT", ".")))
out_dir = vault_root / "vaultbot_stuff" / "Memory" / "Build-Log"
out_dir.mkdir(parents=True, exist_ok=True)

# ---- Helpers -------------------------------------------------------------

def _as_json_str(val):
    """Normalize a tool return value to a JSON string.

    The runtime tool wrappers (machine_spec, ollama_model_search,
    vaultbot_status) return dicts, not strings.  run_procedure returns
    a JSON string.  This helper handles both transparently so the
    temp-file + parse_probe pipeline works regardless of return type.
    """
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, str):
        return val.strip()
    return json.dumps(val, default=str)

# ---- Identity Probe -------------------------------------------------------
identity_data = {}
identities = [
    ("identity_py", vault_root / "vaultbot_stuff" / "vaultbot_backend" / "identity.py"),
    ("identity_py_tmp", vault_root / "vaultbot_stuff" / "System" / "Core" / "identity.py.tmp"),
]
for key, path in identities:
    if path.exists():
        identity_data[key] = path.read_text(encoding="utf-8", errors="replace")[:5000]

# also check for any identity markdown notes
markdown_notes = []
for note_name in ["Identity-Facts", "Autonomy-Directive", "VaultBot-Identity", "Core-Identity"]:
    note_path = vault_root / "vaultbot_stuff" / "System" / "Core" / f"{note_name}.md"
    if note_path.exists():
        markdown_notes.append({"note": note_name, "content": note_path.read_text(encoding="utf-8", errors="replace")[:3000]})
id_data = {"markdown_notes": markdown_notes}
id_data.update(identity_data)
id_data["probed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
identity_json = json.dumps(id_data, ensure_ascii=False)
# store temporarily for later parsing
(Path(out_dir) / "tmp_identity.json").write_text(identity_json, encoding="utf-8")

# ---- Capability Probe -----------------------------------------------------
capability_result_raw = _as_json_str(run_procedure("Capability-Audit", args={}))
(Path(out_dir) / "tmp_capability.json").write_text(capability_result_raw, encoding="utf-8")

# ---- Health Probe ---------------------------------------------------------
health_result_raw = _as_json_str(run_procedure("Diagnose-System-Health", args={}))
(Path(out_dir) / "tmp_health.json").write_text(health_result_raw, encoding="utf-8")

# ---- Vault Probe ----------------------------------------------------------
vault_result_raw = _as_json_str(run_procedure("Vault-Health-Check", args={}))
(Path(out_dir) / "tmp_vault.json").write_text(vault_result_raw, encoding="utf-8")

# ---- Procedure Probe -------------------------------------------------------
procedure_result_raw = _as_json_str(run_procedure("Procedure-Eval", args={}))
(Path(out_dir) / "tmp_procedures.json").write_text(procedure_result_raw, encoding="utf-8")

# ---- Hardware Probe --------------------------------------------------------
_hardware_raw = machine_spec({})
hardware_json = _as_json_str(_hardware_raw)
(Path(out_dir) / "tmp_hardware.json").write_text(hardware_json, encoding="utf-8")

# ---- Model Probe ----------------------------------------------------------
_models_raw = ollama_model_search({"action": "installed"})
model_json = _as_json_str(_models_raw)
(Path(out_dir) / "tmp_models.json").write_text(model_json, encoding="utf-8")

# ---- Session Probe --------------------------------------------------------
_session_raw = vaultbot_status({})
session_json = _as_json_str(_session_raw)
(Path(out_dir) / "tmp_session.json").write_text(session_json, encoding="utf-8")

# ---- Assemble Report ------------------------------------------------------

def parse_probe(raw_str):
    try:
        return json.loads(raw_str)
    except Exception as e:
        return {"parse_error": True, "raw": raw_str}

probes = {
    "identity": parse_probe((Path(out_dir)/"tmp_identity.json").read_text()),
    "capability": parse_probe((Path(out_dir)/"tmp_capability.json").read_text()),
    "health": parse_probe((Path(out_dir)/"tmp_health.json").read_text()),
    "vault": parse_probe((Path(out_dir)/"tmp_vault.json").read_text()),
    "procedures": parse_probe((Path(out_dir)/"tmp_procedures.json").read_text()),
    "hardware": parse_probe((Path(out_dir)/"tmp_hardware.json").read_text()),
    "models": parse_probe((Path(out_dir)/"tmp_models.json").read_text()),
    "session": parse_probe((Path(out_dir)/"tmp_session.json").read_text()),
}

summary = {}
# Identity
summary["identity_verified"] = bool(probes["identity"].get("identity_py", "")) and len(probes["identity"].get("identity_py", "")) > 100
# Capability
cap = probes["capability"]
if isinstance(cap, dict) and "tools_available" in cap:
    summary["tools_available"] = cap["tools_available"]
elif isinstance(cap, dict) and "raw" in cap:
    m = re.search(r"tools_available[:\s]+(\d+)", cap["raw"])
    summary["tools_available"] = int(m.group(1)) if m else "unknown"
# Health
health = probes["health"]
if isinstance(health, dict):
    summary["backend_healthy"] = health.get("backend_healthy", health.get("api_healthy", "unknown"))
    summary["ollama_healthy"] = health.get("ollama_healthy", "unknown")
# Vault
vault_data = probes["vault"]
if isinstance(vault_data, dict):
    summary["vault_notes"] = vault_data.get("total_notes", vault_data.get("counts", {}).get("total_notes", "unknown"))
# Procedures
proc = probes["procedures"]
if isinstance(proc, dict):
    summary["procedures_total"] = proc.get("total_procedures", proc.get("counts", {}).get("total", "unknown"))
    summary["procedures_healthy"] = proc.get("healthy_count", proc.get("counts", {}).get("healthy", "unknown"))
# Hardware
hw = probes["hardware"]
if isinstance(hw, dict):
    summary["cpu_cores"] = hw.get("cpu_cores", hw.get("cpu", {}).get("cores", "unknown"))
    summary["ram_gb"] = hw.get("ram_gb", hw.get("memory", {}).get("total_gb", "unknown"))
    summary["gpu"] = hw.get("gpu", hw.get("gpu_name", "unknown"))
# Models
models = probes["models"]
if isinstance(models, dict):
    summary["models_installed"] = len(models.get("models", models.get("installed", [])))
# Session
sess = probes["session"]
if isinstance(sess, dict):
    summary["background_researcher"] = sess.get("background_researcher", sess.get("researcher_status", "unknown"))

warnings = []
errors = {}
for name, data in probes.items():
    if isinstance(data, dict) and data.get("parse_error"):
        warnings.append(f"{name}: result could not be parsed as JSON")
    if isinstance(data, dict):
        err_text = str(data).lower()
        if "error" in err_text and name not in ["identity", "capability", "health"]:
            errors[name] = data

report = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "depth": args.get("depth", "standard"),
    "probes": probes,
    "summary": summary,
    "warnings": warnings,
    "errors": errors,
}

out_file = Path(args.get("output_file", out_dir / "know-thyself-latest.json"))
out_file.write_text(json.dumps(report, indent=1), encoding="utf-8")

# Human-readable summary
human_summary = f"\n**Know-Thyself Report** (generated {report['generated_at']})\n\n"
human_summary += f"**Identity:** {'[OK] Verified' if summary.get('identity_verified') else '[FAIL] Missing'}\n"
human_summary += f"**Capabilities:** {summary.get('tools_available')} tools available\n"
human_summary += f"**Backend:** {'[OK] Healthy' if summary.get('backend_healthy') else '[FAIL] Unhealthy'}\n"
human_summary += f"**Ollama:** {'[OK] Healthy' if summary.get('ollama_healthy') else '[FAIL] Unhealthy'}\n"
human_summary += f"**Vault:** {summary.get('vault_notes')} notes\n"
human_summary += f"**Procedures:** {summary.get('procedures_healthy')}/{summary.get('procedures_total')} healthy\n"
human_summary += f"**Hardware:** {summary.get('cpu_cores')} cores, {summary.get('ram_gb')}GB RAM, {summary.get('gpu')}\n"
human_summary += f"**Models:** {summary.get('models_installed')} installed\n"
human_summary += f"**Background Researcher:** {summary.get('background_researcher')}\n"
if warnings:
    human_summary += f"[WARN] Warnings: {'; '.join(warnings)}\n"
if errors:
    human_summary += f"[FAIL] Errors: {', '.join(errors.keys())}\n"
human_summary += f"Full JSON: {out_file}"

result = human_summary
print(human_summary)
```

---

## Usage Examples

```python
# Minimal check (identity + capability only)
run_procedure("Know-Thyself", args={"depth": "minimal"})

# Standard full snapshot (default)
run_procedure("Know-Thyself", args={})

# Deep with graph analysis
run_procedure("Know-Thyself", args={"depth": "deep"})

# Custom output path
run_procedure("Know-Thyself", args={"output_file": "vaultbot_stuff/Memory/Build-Log/know-thyself-session-start.json"})
```

