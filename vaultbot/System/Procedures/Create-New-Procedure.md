---
type: procedure
status: draft
baseline: true
created: 2023-10-25
summary: Generates a new markdown file for a standard operating procedure (SOP) or machine-executable procedure, ensuring it follows a consistent structure with required sections.
tags: [procedure, documentation, sop, vaultbot]
description: "This procedure generates a new markdown file for a standard operating procedure (SOP) or machine-executable procedure. It structures the file with required sections including frontmatter, trigger conditions, inputs, executable steps, rationale, and related notes."
when_to_use: "Run this procedure when creating a new standard operating procedure (SOP) or machine-executable procedure markdown file for the VaultBot system. This is triggered when: - A new task or workflow needs to be formalized into a repeatable procedure - An existing process has failed or shown gaps that require documentation - A user requests the creation of a procedure from a task description - A recurring operation needs to be standardized to ensure consistent execution"
falsifiable_if: "The generated markdown file is missing required frontmatter fields (type, status, created, summary, tags)."
allowed_tools: []
model_cartridge: big
---

## Description
This procedure generates a new markdown file for a standard operating procedure (SOP) or machine-executable procedure. It structures the file with required sections including frontmatter, trigger conditions, inputs, executable steps, rationale, and related notes.

## When to Use
Run this procedure when creating a new standard operating procedure (SOP) or machine-executable procedure markdown file for the VaultBot system. This is triggered when:

- A new task or workflow needs to be formalized into a repeatable procedure
- An existing process has failed or shown gaps that require documentation
- A user requests the creation of a procedure from a task description
- A recurring operation needs to be standardized to ensure consistent execution

## Why This Exists

New tasks and workflows needed to be formalized into repeatable procedures, but there was no consistent structure to generate them. This procedure generates a new markdown file with the required sections. The key tradeoff is that it enforces a fixed section structure (frontmatter, trigger conditions, inputs, steps, rationale, related notes) so every procedure is consistent.

## Falsifiable If
- The generated markdown file is missing required frontmatter fields (type, status, created, summary, tags).
- The generated procedure does not contain a "When to Use" section describing trigger situations.
- The steps in the generated procedure are not executable or lack proper headers and formatting.
- The generated file fails to render as valid markdown.

## Inputs
- procedure_name (string): The name of the procedure file to be created.
- task_description (string): The description of the task or workflow to be formalized.
- is_machine_executable (boolean): Whether the procedure should be machine-executable.

## Tools
- file.write
- file.read

## Model Cartridge
- small: For extracting inputs and classifying the type of procedure.
- big: For synthesizing the steps and rationale from the task description.

## Steps

### Step 1: Gather Inputs
Extract the required inputs from the user request.
```python
inputs = {
    'procedure_name': args.get('procedure_name'),
    'task_description': args.get('task_description'),
    'is_machine_executable': args.get('is_machine_executable', False)
}
```

### Step 2: Generate Procedure Structure
[llm: Use the big model cartridge to synthesize the procedure structure, including frontmatter, description, when to use, falsifiable if, inputs, tools, and steps based on the task_description. Ensure the output is valid markdown.]

### Step 3: Write Procedure File
Write the generated markdown to the appropriate file path.
```python
file_path = f"procedures/{inputs.get('procedure_name')}.md"
with open(file_path, 'w') as f:
    f.write(generated_markdown)
print(f"Procedure written to {file_path}")
```

### Step 4: Verify Output
Read the file back and verify it contains all required sections.
```python
with open(file_path, 'r') as f:
    content = f.read()
required_sections = ["## Description", "## When to Use", "## Falsifiable If", "## Inputs", "## Steps"]
for section in required_sections:
    assert section in content, f"Missing section: {section}"
```

## Related

- [[Build-Procedure]] — the full draft→review→test factory
- [[Draft-New-Procedure]] — drafts a procedure without the full pipeline
- [[How-to-Create-a-Procedure]] — human-facing guide to procedure creation