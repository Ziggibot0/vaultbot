---
type: procedure
status: verified
baseline: true
created: 2023-10-25
summary: Drafts a new procedure markdown note from a provided task description, including frontmatter, description, steps, and falsifiability conditions.
tags: [procedure, meta, drafting]
last_reviewed: 2026-08-15
description: "Takes a task description and generates a structured, machine-executable procedure markdown note with frontmatter, description, falsifiable conditions, and executable steps."
when_to_use: "Run this procedure when you need to draft a new procedure markdown note from a task description. This is the starting point whenever a task or workflow needs to be formalized into a repeatable, machine-executable procedure within the vault. **Triggers:** - A task description has been provided and needs to be converted into a structured procedure - A recurring workflow needs to be documented as a repeatable procedure - An existing ad-hoc process needs to be formalized into the standard procedure format"
falsifiable_if: "The generated markdown note is missing the `---` frontmatter block."
allowed_tools: []
model_cartridge: big
---

## Description
Takes a task description and generates a structured, machine-executable procedure markdown note with frontmatter, description, falsifiable conditions, and executable steps.

## When to Run This

Run this procedure when you need to draft a new procedure markdown note from a task description. This is the starting point whenever a task or workflow needs to be formalized into a repeatable, machine-executable procedure within the vault.

**Triggers:**
- A task description has been provided and needs to be converted into a structured procedure
- A recurring workflow needs to be documented as a repeatable procedure
- An existing ad-hoc process needs to be formalized into the standard procedure format

## Why This Exists

A task description is unstructured, but a procedure note must carry frontmatter, a description, falsifiable conditions, and executable steps to be machine-runnable. This procedure exists to formalize a task or workflow into that standard repeatable format. The key tradeoff is that it uses the `big` cartridge — reasoning and synthesis are required to generate structured markdown from an unstructured description.

## Falsifiable If
- The generated markdown note is missing the `---` frontmatter block.
- The generated note lacks a `## Description` section that states what the procedure does.
- The generated note lacks a `## Falsifiable If` section with specific failure conditions.
- The generated note contains invalid tags like `[vllm:...]` or `[model_cartridge:...]` in the steps.

## Inputs
- `task_description` (string, required): The text describing the task or workflow to be formalized.
- `name` (string, optional): The name of the procedure to save.
- `content` (string, optional): The generated markdown content to save.

## Model Cartridge
`big` (Requires reasoning and synthesis to generate structured markdown from unstructured task descriptions).

## Steps

### Step 1: Extract procedure metadata
[llm: Extract a concise name, summary, and relevant tags from the input task_description. Format the name in PascalCase. Return a JSON object with keys: name, summary, tags.]

### Step 2: Generate the procedure markdown
[llm: Using the extracted metadata and the original task_description, generate a complete markdown procedure note. The note MUST start with `---` frontmatter containing `type: procedure`, `status: active`, `created: <current_date>`, `summary`, and `tags`. It must include `## Description`, `## When to Run This`, `## Falsifiable If`, `## Inputs`, `## Model Cartridge`, and `## Steps`. Ensure steps use `### Step N: ...` headers and `[llm: ...]` tags or ```python code blocks. Do NOT use `[vllm:...]` or `[model_cartridge:...]` tags inside steps.]

### Step 3: Save the procedure note
```python
import os

name = args.get("name", "Untitled-Procedure")
content = args.get("content", "")
filepath = f"procedures/{name}.md"
os.makedirs("procedures", exist_ok=True)
with open(filepath, "w") as f:
    f.write(content)
print(f"Saved procedure to {filepath}")
```

## Related

- [[Draft-New-Procedure]] — sibling procedure-drafting workflow
- [[Create-New-Procedure]] — the creation path this drafting feeds into
- [[How-to-Create-a-Procedure]] — the canonical procedure-authoring guide