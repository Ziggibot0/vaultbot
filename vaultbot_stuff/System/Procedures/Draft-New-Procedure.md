---
type: procedure
status: active
created: 2023-10-27
summary: Converts a task description or workflow gap into a structured, machine-executable procedure markdown file with frontmatter, falsifiable conditions, and executable steps.
tags: [procedure, meta, documentation]
---

## Description
This procedure converts a task description or identified workflow gap into a structured, machine-executable procedure markdown file. It generates the required frontmatter, trigger conditions, falsifiable failure conditions, required inputs, and step-by-step executable actions using allowed tools.

## When to Run This

Run this procedure when drafting a new machine-executable procedure note for the vault. Triggered when:

- A task description is provided and needs to be converted into a structured procedure markdown file.
- A recurring failure or gap has been identified that warrants a documented, repeatable procedure.
- A user explicitly requests creation of a new procedure from a task or workflow description.

## Falsifiable If

- The generated procedure markdown file fails to parse as valid YAML frontmatter.
- The generated steps do not contain required headers (`### Step N:`) or valid tool tags (`[llm: ...]` or ```python ... ```).
- The generated procedure does not include a `## When to Run This` section describing triggering situations.
- The generated procedure does not include a `## Falsifiable If` section with specific, observable failure conditions.

## Model Cartridge

- `big`: Required for reasoning and synthesizing the procedure structure, steps, and falsifiable conditions from a raw task description.

## Inputs

- `task_description` (str, optional): The description of the task to convert into a procedure.
- `failure_gap` (str, optional): The recurring failure or gap that warrants a procedure.
- `filename` (str, optional): The name of the file to save the procedure to. Defaults to 'new_procedure.md'.

## Steps

### Step 1: Gather Inputs

```python
import argparse

task_desc = args.get('task_description')
failure_gap = args.get('failure_gap')
filename = args.get('filename', 'new_procedure.md')

if not task_desc and not failure_gap:
    raise ValueError("Must provide either 'task_description' or 'failure_gap'.")
```

### Step 2: Draft Procedure Sections

[llm: gpt-4o] Generate the full markdown procedure based on the provided inputs. Ensure the output includes frontmatter, Description, When to Run This, Falsifiable If, Model Cartridge, Inputs, and Steps sections. Ensure steps use `### Step N:` headers, `[llm: ...]` tags, and ```python fences. Do not use `[vllm:...]` or `[model_cartridge:...]` tags in the steps.

### Step 3: Save Procedure File

```python
with open(filename, 'w') as f:
    f.write(generated_markdown)
print(f"Procedure saved to {filename}")
```