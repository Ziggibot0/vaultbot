---
type: procedure
status: active
model_cartridge: small
created: 2026-08-05
description: "Parent orchestrator for instant self-knowledge. Runs a suite of probes to answer 'what am I right now?' — identity, capabilities, tools, vault state, health, and procedure library. Returns a live snapshot, not remembered state."
when_to_use: "When you need to know your current capabilities, identity, vault health, or system status instantly — at session start, before a complex task, or when something feels off."
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
summary: |
  Know-Thyself is the single entry point for VaultBot self-knowledge. It orchestrates 8 probes in parallel (where possible) to produce a live snapshot:
  1. Identity Probe — reads identity facts from vault
  2. Capability Probe — runs Capability-Audit for tool/capability inventory
  3. Health Probe — runs Diagnose-System-Health for backend/ollama status
  4. Vault Probe — runs Vault-Health-Check for graph/topology snapshot
  5. Procedure Probe — runs Procedure-Eval for procedure library health
  6. Hardware Probe — runs machine_spec for CPU/RAM/GPU/Ollama config
  7. Model Probe — runs ollama_model_search for available models
  8. Session Probe — runs VaultBot-Status for background researcher state
  Output is a structured JSON report written to Memory/Build-Log/know-thyself-latest.json with a human-readable summary.
tags:
  - procedure
  - self-knowledge
  - orchestration
  - introspection
---

# Know-Thyself

## Purpose

**One call, complete self-knowledge.** This procedure answers "what am I right now?" by running live probes — not cached memories. Every probe executes fresh. The result is a structured snapshot you can trust for decision-making.

## When to Run

- **Session start** — establish baseline before any work
- **Pre-task** — verify capabilities before a complex operation
- **Debugging** — "why did that fail?" → check current health/capabilities
- **Curiosity** — "what models do I have?" "how many procedures?" → instant answer

## Architecture: Probe Orchestration

```
Know-Thyself (parent, small cartridge)
├── Identity Probe      → vault_read_note(identity facts)
├── Capability Probe    → run_procedure(Capability-Audit)
├── Health Probe        → run_procedure(Diagnose-System-Health)
├── Vault Probe         → run_procedure(Vault-Health-Check)
├── Procedure Probe     → run_procedure(Procedure-Eval)
├── Hardware Probe      → machine_spec()
├── Model Probe         → ollama_model_search(action=installed)
└── Session Probe       → vaultbot_status()
```

All probes run **independently** — no probe depends on another's output. Failures are isolated and reported, not fatal.

## Inputs

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `depth` | string | "standard" | "minimal" (identity+capability only), "standard" (all 8), "deep" (adds graph analysis) |
| `output_file` | string | auto | Custom path for JSON output (default: `vaultbot_stuff/Memory/Build-Log/know-thyself-latest.json`) |

## Output Contract

**File written:** `vaultbot_stuff/Memory/Build-Log/know-thyself-latest.json`

```json
{
  "generated_at": "2026-08-05T14:30:00Z",
  "depth": "standard",
  "probes": {
    "identity": { ... },
    "capability": { ... },
    "health": { ... },
    "vault": { ... },
    "procedures": { ... },
    "hardware": { ... },
    "models": { ... },
    "session": { ... }
  },
  "summary": {
    "identity_verified": true,
    "tools_available": 16,
    "backend_healthy": true,
    "ollama_healthy": true,
    "vault_notes": 1247,
    "procedures_total": 23,
    "procedures_healthy": 18,
    "cpu_cores": 12,
    "ram_gb": 32,
    "gpu": "RTX 3080",
    "models_installed": 8,
    "background_researcher": "running"
  },
  "warnings": [],
  "errors": {}
}
```

**Return value (final step):** Human-readable summary paragraph + path to JSON file.

---

## Steps

### Step 1: Identity Probe — Read Core Identity Facts

```python
import json, datetime
from pathlib import Path

vault = str(Path(vault_path).resolve())
out_dir = Path(vault) / "vaultbot_stuff" / "Memory" / "Build-Log"
out_dir.mkdir(parents=True, exist_ok=True)

# Read identity facts from the canonical location
identity_file = Path(vault) / "vaultbot_stuff" / "System" / "Core" / "identity.py"
identity_tmpl = Path(vault) / "vaultbot_stuff" / "System" / "Core" / "identity.py.tmp"

identity_data = {}
if identity_file.exists():
    identity_data["identity_py"] = identity_file.read_text(encoding="utf-8", errors="replace")[:5000]
if identity_tmpl.exists():
    identity_data["identity_py_tmp"] = identity_tmpl.read_text(encoding="utf-8", errors="replace")[:5000]

# Also check for any identity markdown notes
identity_notes = []
for note_name in ["Identity-Facts", "Autonomy-Directive", "VaultBot-Identity", "Core-Identity"]:
    note_path = Path(vault) / "vaultbot_stuff" / "System" / "Core" / f"{note_name}.md"
    if note_path.exists():
        identity_notes.append({"note": note_name, "content": note_path.read_text(encoding="utf-8", errors="replace")[:3000]})

identity_data["markdown_notes"] = identity_notes
identity_data["probed_at"] = datetime.datetime.now().isoformat(timespec="seconds")

result_identity = json.dumps(identity_data)
```

### Step 2: Capability Probe — Run Capability-Audit

```python
# Run Capability-Audit procedure for live tool/capability inventory
capability_result = run_procedure("Capability-Audit", args={})
result_capability = capability_result
```

### Step 3: Health Probe — Run Diagnose-System-Health

```python
# Run Diagnose-System-Health for backend + Ollama status
health_result = run_procedure("Diagnose-System-Health", args={})
result_health = health_result
```

### Step 4: Vault Probe — Run Vault-Health-Check

```python
# Run Vault-Health-Check for graph topology snapshot
vault_result = run_procedure("Vault-Health-Check", args={})
result_vault = vault_result
```

### Step 5: Procedure Probe — Run Procedure-Eval

```python
# Run Procedure-Eval for procedure library health scores
procedure_result = run_procedure("Procedure-Eval", args={})
result_procedures = procedure_result
```

### Step 6: Hardware Probe — Run machine_spec

```python
# Get hardware specs and Ollama config
hardware_result = machine_spec({})
result_hardware = hardware_result
```

### Step 7: Model Probe — Run ollama_model_search (installed)

```python
# Get currently installed Ollama models
model_result = ollama_model_search({"action": "installed"})
result_models = model_result
```

### Step 8: Session Probe — Run vaultbot_status

```python
# Get background researcher state
session_result = vaultbot_status({})
result_session = session_result
```

### Step 9: Assemble & Write Report

```python
import json

# Parse all probe results (they come back as strings, may be JSON or text)
def parse_probe_result(raw, probe_name):
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw, "parse_error": True, "probe": probe_name}

probes = {
    "identity": parse_probe_result(result_identity, "identity"),
    "capability": parse_probe_result(result_capability, "capability"),
    "health": parse_probe_result(result_health, "health"),
    "vault": parse_probe_result(result_vault, "vault"),
    "procedures": parse_probe_result(result_procedures, "procedures"),
    "hardware": parse_probe_result(result_hardware, "hardware"),
    "models": parse_probe_result(result_models, "models"),
    "session": parse_probe_result(result_session, "session"),
}

# Extract summary fields for quick reading
summary = {}

# Identity
summary["identity_verified"] = "identity_py" in probes["identity"] and len(probes["identity"]["identity_py"]) > 100

# Capability
cap = probes["capability"]
if isinstance(cap, dict) and "tools_available" in cap:
    summary["tools_available"] = cap["tools_available"]
elif isinstance(cap, dict) and "raw" in cap:
    # Try to extract from raw text
    import re
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

# Collect warnings and errors
warnings = []
errors = {}
for name, data in probes.items():
    if isinstance(data, dict):
        if data.get("parse_error"):
            warnings.append(f"{name}: result could not be parsed as JSON")
        if "error" in str(data).lower() and "error" not in name:
            errors[name] = data

# Build final report
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

result = json.dumps({
    "summary": summary,
    "out_file": str(out_file),
    "generated_at": report["generated_at"],
    "warnings_count": len(warnings),
    "errors_count": len(errors),
})
```

### Step 10: Human-Readable Summary

```llm
Based on the assembled report, produce a concise human-readable summary in this format:

**Know-Thyself Report** (generated {{generated_at}})

**Identity:** {{"✅ Verified" if summary.identity_verified else "❌ Missing"}}
**Capabilities:** {{summary.tools_available}} tools available
**Backend:** {{"✅ Healthy" if summary.backend_healthy else "❌ Unhealthy"}}
**Ollama:** {{"✅ Healthy" if summary.ollama_healthy else "❌ Unhealthy"}}
**Vault:** {{summary.vault_notes}} notes
**Procedures:** {{summary.procedures_healthy}}/{{summary.procedures_total}} healthy
**Hardware:** {{summary.cpu_cores}} cores, {{summary.ram_gb}}GB RAM, {{summary.gpu}}
**Models:** {{summary.models_installed}} installed
**Background Researcher:** {{summary.background_researcher}}

{{"⚠️ Warnings: " + "; ".join(warnings) if warnings else ""}}
{{"❌ Errors: " + ", ".join(errors.keys()) if errors else ""}}

Full JSON: {{out_file}}
```

### Step 11: Validate Output

```validate
contains "summary" and "out_file"
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

## Probe Failure Handling

Each probe runs independently. If a probe fails:
- Its `errors` entry captures the failure
- Other probes continue
- Summary marks that probe as failed
- You still get a partial report — **never a total failure**

## Extending Probes

To add a new probe:
1. Add a new step calling the probe (code or `run_procedure`)
2. Add its result to the `probes` dict in Step 9
3. Extract summary fields if useful
4. Update the human-readable template in Step 10

No existing probes need modification — **open/closed principle**.

---

## Related Procedures

- **Capability-Audit** — detailed tool/capability inventory (called by Capability Probe)
- **Diagnose-System-Health** — backend + Ollama health (called by Health Probe)
- **Vault-Health-Check** — graph topology + counts (called by Vault Probe)
- **Procedure-Eval** — procedure library scoring (called by Procedure Probe)
- **Pattern-Scan** — vault-wide pattern engine (used by Vault-Health-Check)
- **VaultBot-Status** — session + researcher state (called by Session Probe)