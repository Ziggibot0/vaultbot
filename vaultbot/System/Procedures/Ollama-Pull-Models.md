---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-07-31
description: Pull Ollama models by tag. Runs ollama pull for each model in the list.
when_to_use: When the user wants to download/pull new models onto the local Ollama instance.
allowed_tools:
  - code_run
summary: "Analyze this note and produce:
1. A one-sentence summary (max 120 chars) describing what the note SAYS, not just its title. Use a verb.
2. 3-5 topic tags (single words, lowercase, no # prefix, no spac"
tags:
  - procedure
  - procedures
---

# Ollama-Pull-Models

Pulls a list of Ollama models by tag. First lists what's already available, then pulls each requested model.

## Steps

1. ```python
   import subprocess
   
   # First, see what's already pulled
   result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
   print("=== CURRENTLY PULLED MODELS ===")
   print(result.stdout)
   if result.stderr:
       print("STDERR:", result.stderr)
   ```

2. [llm: Report what's already pulled to the user, then proceed to pull the missing models.]

3. ```python
   import subprocess
   import time
   
   models = [
       "lfm2:3b",
       "gemma3:2b",
       "nemotron-nano",
       "qwen3:0.6b",
       "qwen3:1.7b",
       "qwen3:4b",
   ]
   
   for model in models:
       print(f"\n=== PULLING {model} ===")
       result = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=600)
       print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
       if result.stderr:
           print("STDERR:", result.stderr[-500:])
       print(f"Exit code: {result.returncode}")
       time.sleep(1)
   ```

4. [llm: Report results for each model — which succeeded, which failed, and any error messages.]