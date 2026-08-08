---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-06
description: "JSON array of model names to test (e.g. ['qwen3:1.7b', 'qwen3.5:0.8b', 'gemma3:1b']). If empty, tests all installed models under 4GB."
when_to_use: >
falsifiable_if: >
inputs:
  - "name: task_name"
datatype: string
required: false
allowed_tools:
  - code_run
  - llm_generate
  - vault_safe_write
  - ollama_model_search
tags:
  - procedure
  - model-evaluation
  - benchmarking
  - tournament
  - small-model
summary: Model-Tournament
---

# Model-Tournament

## How This Works

1. Pulls candidate models (or uses provided list)
2. Runs each model against every test case
3. Scores: correctness (did it get the right answer?), speed (how fast?), consistency (same answer every time?)
4. Ranks models by weighted score
5. Recommends the best model for the task

## Steps

0. ```python
import json, time, subprocess, re

task_name = args.get('task_name', 'unnamed-task')
task_description = args.get('task_description', '')
test_cases_raw = args.get('test_cases', '[]')
models_raw = args.get('models', '[]')

try:
    test_cases = json.loads(test_cases_raw)
except:
    test_cases = []

try:
    candidate_models = json.loads(models_raw)
except:
    candidate_models = []

# If no models specified, discover installed models under 4GB
if not candidate_models:
    try:
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split('\n')[1:]  # skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0]
                size_str = parts[-2] + ' ' + parts[-1]
                # Parse size: "1.4 GB" or "522 MB"
                if 'MB' in size_str:
                    size_gb = float(size_str.replace(' MB', '')) / 1024
                elif 'GB' in size_str:
                    size_gb = float(size_str.replace(' GB', ''))
                else:
                    continue
                # Only include models under 4GB and not embedding models
                if size_gb < 4.0 and 'embed' not in name.lower():
                    candidate_models.append(name)
    except Exception as e:
        candidate_models = []

result = json.dumps({
    "task_name": task_name,
    "task_description": task_description,
    "test_cases": test_cases,
    "candidate_models": candidate_models,
    "test_count": len(test_cases),
    "model_count": len(candidate_models)
})
print(result)
```

1. ```python
import json, time, subprocess

data = json.loads(prior_results[0].get('output', '{}') if isinstance(prior_results[0], dict) else '{}')
candidate_models = data.get('candidate_models', [])
test_cases = data.get('test_cases', [])
task_description = data.get('task_description', '')

# Pull any models not yet installed
pulled = []
for model in candidate_models:
    try:
        # Check if already installed
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=10)
        if model.split(':')[0] not in result.stdout:
            print(f"Pulling {model}...")
            subprocess.run(['ollama', 'pull', model], capture_output=True, text=True, timeout=300)
            pulled.append(model)
    except Exception as e:
        print(f"Failed to pull {model}: {e}")

result = json.dumps({
    "pulled": pulled,
    "ready_models": candidate_models,
    "test_cases": test_cases,
    "task_description": task_description
})
print(result)
```

2. ```python
import json, time, subprocess

data = json.loads(prior_results[1].get('output', '{}') if isinstance(prior_results[1], dict) else '{}')
models = data.get('ready_models', [])
test_cases = data.get('test_cases', [])
task_description = data.get('task_description', '')

# Run each model against each test case
# For each test, run 3 times to measure consistency
results = []

for model in models:
    model_results = {
        "model": model,
        "runs": [],
        "total_time_ms": 0,
        "correct_count": 0,
        "total_count": 0
    }
    
    for tc_idx, tc in enumerate(test_cases):
        test_input = tc.get('input', '')
        expected = tc.get('expected', '')
        tolerance = tc.get('tolerance', 'contains')
        
        # Build the prompt
        prompt = f"{task_description}\n\nInput: {test_input}\n\nOutput only the answer, nothing else."
        
        # Run 3 times for consistency
        run_outputs = []
        for run_num in range(3):
            start = time.time()
            try:
                result = subprocess.run(
                    ['ollama', 'run', model, prompt],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                elapsed_ms = (time.time() - start) * 1000
                output = result.stdout.strip()
                run_outputs.append(output)
                
                # Check correctness
                is_correct = False
                if tolerance == 'exact':
                    is_correct = output.strip().lower() == expected.strip().lower()
                elif tolerance == 'contains':
                    is_correct = expected.strip().lower() in output.strip().lower()
                elif tolerance == 'semantic':
                    # For semantic, we'll use a separate LLM check later
                    is_correct = None  # deferred
                
                model_results["runs"].append({
                    "test_case": tc_idx,
                    "run": run_num,
                    "output": output[:200],
                    "expected": expected[:200],
                    "time_ms": round(elapsed_ms, 1),
                    "correct": is_correct
                })
                model_results["total_time_ms"] += elapsed_ms
                model_results["total_count"] += 1
                if is_correct:
                    model_results["correct_count"] += 1
                    
            except subprocess.TimeoutExpired:
                model_results["runs"].append({
                    "test_case": tc_idx,
                    "run": run_num,
                    "output": "TIMEOUT",
                    "expected": expected[:200],
                    "time_ms": 60000,
                    "correct": False
                })
                model_results["total_time_ms"] += 60000
                model_results["total_count"] += 1
            except Exception as e:
                model_results["runs"].append({
                    "test_case": tc_idx,
                    "run": run_num,
                    "output": f"ERROR: {str(e)[:100]}",
                    "expected": expected[:200],
                    "time_ms": 0,
                    "correct": False
                })
                model_results["total_count"] += 1
    
    # Calculate consistency: how often did the model give the same answer?
    outputs_by_tc = {}
    for run in model_results["runs"]:
        tc = run["test_case"]
        if tc not in outputs_by_tc:
            outputs_by_tc[tc] = []
        outputs_by_tc[tc].append(run["output"])
    
    consistent_runs = 0
    total_runs = 0
    for tc, outputs in outputs_by_tc.items():
        total_runs += len(outputs)
        # All outputs identical = consistent
        if len(set(o.strip().lower() for o in outputs)) == 1:
            consistent_runs += len(outputs)
    
    model_results["consistency"] = round(consistent_runs / max(total_runs, 1), 3)
    model_results["accuracy"] = round(model_results["correct_count"] / max(model_results["total_count"], 1), 3)
    model_results["avg_time_ms"] = round(model_results["total_time_ms"] / max(model_results["total_count"], 1), 1)
    
    results.append(model_results)

result = json.dumps({"results": results, "test_cases": test_cases})
print(result)
```

3. ```python
import json

data = json.loads(prior_results[2].get('output', '{}') if isinstance(prior_results[2], dict) else '{}')
results = data.get('results', [])

# Score and rank models
# Weighted score: 50% accuracy, 30% consistency, 20% speed
# Speed is normalized: fastest model gets 1.0, others get proportion
if results:
    min_time = min(r["avg_time_ms"] for r in results)
    max_time = max(r["avg_time_ms"] for r in results)
    time_range = max(max_time - min_time, 1)  # avoid div by zero
    
    for r in results:
        accuracy_score = r["accuracy"]
        consistency_score = r["consistency"]
        # Speed score: 1.0 for fastest, 0.0 for slowest
        speed_score = 1.0 - ((r["avg_time_ms"] - min_time) / time_range)
        
        r["accuracy_score"] = round(accuracy_score, 3)
        r["consistency_score"] = round(consistency_score, 3)
        r["speed_score"] = round(speed_score, 3)
        r["weighted_score"] = round(
            0.50 * accuracy_score +
            0.30 * consistency_score +
            0.20 * speed_score,
            3
        )
    
    # Sort by weighted score descending
    results.sort(key=lambda r: r["weighted_score"], reverse=True)
    
    # Assign ranks
    for i, r in enumerate(results):
        r["rank"] = i + 1
    
    # Winner
    winner = results[0] if results else None
    
    # Generate recommendation
    if winner:
        print(f"WINNER: {winner['model']}")
        print(f"  Score: {winner['weighted_score']:.3f}")
        print(f"  Accuracy: {winner['accuracy']:.1%}")
        print(f"  Consistency: {winner['consistency']:.1%}")
        print(f"  Avg time: {winner['avg_time_ms']:.0f}ms")
        print()
        print("FULL RANKINGS:")
        for r in results:
            print(f"  {r['rank']}. {r['model']} — score={r['weighted_score']:.3f} (acc={r['accuracy']:.1%}, cons={r['consistency']:.1%}, speed={r['avg_time_ms']:.0f}ms)")
    
    result = json.dumps({
        "winner": winner,
        "rankings": results,
        "recommendation": f"Use {winner['model']} for {data.get('task_name', 'this task')}." if winner else "No models tested."
    })
else:
    result = json.dumps({"error": "No results to rank"})

print(result)
```

4. [llm: You are a model evaluator. Below are the tournament results for benchmarking local models.

## Task
{task_description}

## Tournament Results
{rankings_summary}

## Your Job
Write a brief recommendation (2-3 sentences) explaining:
1. Which model won and why (what it was best at)
2. Whether the winner is significantly better than #2, or if they're close
3. Any caveats (e.g. "winner is fast but inconsistent", "runner-up is more reliable but slower")

Keep it concise. This recommendation will be shown to the user.]
