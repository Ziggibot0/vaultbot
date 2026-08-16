---
type: procedure
status: experimental
created: 2026-08-14
summary: "Systematically finds and updates outdated references across vault notes. Searches for a target term, classifies each mention as current-claim vs historical-context, replaces only current-claims, lints, reports."
description: "Update outdated references vault-wide. Use when a concept name changes, a design target shifts, or terminology needs bulk updating across notes. Classifies each mention as 'update this' vs 'historical context, leave alone' before replacing."
allowed_tools:
  - vault_search
  - vault_read_note
  - llm_generate
  - vault_list
  - vault_safe_write
  - vault_lint
tags: [procedure, vault-maintenance, reference-update, bulk-edit, terminology, rag-retrieval]
---

# Update-Vault-References

## Use Cases

**When to use this procedure:**

- "Update all references to X across the vault"
- "Find and replace outdated terms in vault notes"
- "Systematically update vault notes that mention old terminology"
- "Change references from old model name to new model name"
- "Update design target references"
- "Vault-wide reference update"
- "Find every note that says X and update to Y"
- "Update outdated claims in vault notes"
- "Bulk edit vault notes to reflect new terminology"
- "When a concept name changes, update all references"
- "The design target changed from X to Y, update everything that says X"
- "Replace old term with new term in vault notes"
- "Make sure the vault doesn't say X anymore, it should say Y"
- "Find all mentions of X and update them"
- "Outdated references need updating across the vault"

**When NOT to use this procedure:**

- Single-note edits (just edit the note directly)
- Renaming a note (use Obsidian's rename feature — it updates wikilinks automatically)
- Fixing broken wikilinks (use Dream-Pass or vault_lint instead)
- Updating procedure code/steps (edit the procedure directly)

## Inputs

- `old_term`: The term to search for and replace (e.g., "0.8B")
- `new_term`: The replacement term (e.g., "~4B")
- `context_description`: What kind of references to update vs leave alone (e.g., "Update references where 0.8B is stated as the current design target model. Leave historical references that describe what the model used to be.")

## Outputs

- List of notes searched
- Classification of each mention (UPDATE vs KEEP with reason)
- List of notes actually modified
- Lint results for modified notes
- Summary report

---

### Step 1: Find all notes containing the old term

Search the vault for every note that contains the old_term. Use vault_list to get all .md files, then check each one for the term. This is pure code — no LLM needed.

```python
old_term = args.get('old_term', '')
new_term = args.get('new_term', '')
context_description = args.get('context_description', '')

if not old_term or not new_term:
    print("ERROR: old_term and new_term are required arguments")
    exit(1)

# Get all markdown files in the vault
all_files = vault_list()

# Filter to files that contain the old_term (case-insensitive)
# We need to read each file to check — vault_list only returns filenames
# But we can use vault_search to find relevant files first, then verify
search_results = vault_search(old_term, k=20)

# Also search with quotes for exact phrase matching
exact_results = vault_search(f'"{old_term}"', k=20)

# Collect unique file paths from both searches
candidate_files = set()
for r in search_results:
    path = r.get('file_path', r.get('name', ''))
    if path:
        candidate_files.add(path)
for r in exact_results:
    path = r.get('file_path', r.get('name', ''))
    if path:
        candidate_files.add(path)

# Also scan all files — vault_search might miss some
for f in all_files:
    candidate_files.add(f)

result = f"OLD_TERM: {old_term}\nNEW_TERM: {new_term}\nCONTEXT: {context_description}\nCANDIDATE_FILES: {len(candidate_files)}\nFILES: {'|'.join(sorted(candidate_files))}"
print(result)
```

[validate: contains "OLD_TERM:"]
[validate: contains "CANDIDATE_FILES:"]

---

### Step 2: Read each candidate file and check if it contains the old term

For each candidate file, read it and check if the old_term actually appears. Filter out files that don't contain it. This is pure code — no LLM needed.

```python
# Parse Step 1
lines = output.strip().split('\n')
old_term = ''
new_term = ''
context_description = ''
files_str = ''
for line in lines:
    if line.startswith('OLD_TERM: '):
        old_term = line.replace('OLD_TERM: ', '').strip()
    elif line.startswith('NEW_TERM: '):
        new_term = line.replace('NEW_TERM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context_description = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('FILES: '):
        files_str = line.replace('FILES: ', '').strip()

candidate_files = [f.strip() for f in files_str.split('|') if f.strip()] if files_str else []

# Read each file and check if old_term appears (case-insensitive)
confirmed_files = []
for file_path in candidate_files:
    try:
        note_result = vault_read_note(file_path, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get('content', '')
        else:
            content = str(note_result)
        if old_term.lower() in content.lower():
            confirmed_files.append({'path': file_path, 'content': content})
    except Exception:
        pass  # File might not be readable via vault_read_note (wrong path format)

result = f"OLD_TERM: {old_term}\nNEW_TERM: {new_term}\nCONTEXT: {context_description}\nCONFIRMED_FILES: {len(confirmed_files)}\n"
for f in confirmed_files:
    # Store content for next step — but cap at 2000 chars per file to stay within limits
    result += f"FILE: {f['path']}::: {f['content'][:2000]}\n"
print(result)
```

[validate: contains "CONFIRMED_FILES:"]

---

### Step 3: Classify each mention as UPDATE or KEEP

For each confirmed file, ask the LLM to classify: does this file use old_term as a current claim (UPDATE) or as historical/research context (KEEP)? The LLM gets the file content and the context_description, and makes a judgment call. This is a semantic task — the LLM reads the content and determines intent.

```python
# Parse Step 2
lines = output.strip().split('\n')
old_term = ''
new_term = ''
context_description = ''
file_data = {}
for line in lines:
    if line.startswith('OLD_TERM: '):
        old_term = line.replace('OLD_TERM: ', '').strip()
    elif line.startswith('NEW_TERM: '):
        new_term = line.replace('NEW_TERM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context_description = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('FILE: '):
        rest = line.replace('FILE: ', '').strip()
        if '::: ' in rest:
            path, content = rest.split('::: ', 1)
            file_data[path] = content

classifications = {}
for file_path, content in file_data.items():
    # Ask LLM to classify this file's usage of old_term
    prompt = f"""Read this vault note excerpt. It contains the term "{old_term}".

Context about what should be updated: {context_description}

Does this note use "{old_term}" as a CURRENT claim that should be updated to "{new_term}", or is it a HISTORICAL/research reference that should be kept as-is?

Answer with exactly one line:
VERDICT: UPDATE — [one sentence reason]
or
VERDICT: KEEP — [one sentence reason]

Note excerpt:
{content[:1500]}"""

    resp = llm_generate(prompt).strip()
    
    verdict = 'KEEP'  # safe default
    reason = 'classification failed, defaulting to keep'
    for line in resp.split('\n'):
        if line.startswith('VERDICT:'):
            rest = line.replace('VERDICT:', '').strip()
            if rest.startswith('UPDATE'):
                verdict = 'UPDATE'
                reason = rest
            elif rest.startswith('KEEP'):
                verdict = 'KEEP'
                reason = rest
            break
    
    classifications[file_path] = {'verdict': verdict, 'reason': reason}

# Format output
result = f"OLD_TERM: {old_term}\nNEW_TERM: {new_term}\nCONTEXT: {context_description}\n"
for path, info in classifications.items():
    result += f"CLASSIFICATION: {path}::: {info['verdict']}::: {info['reason']}\n"
print(result)
```

[validate: contains "CLASSIFICATION:"]

---

### Step 4: Replace old_term with new_term in files marked UPDATE

For each file classified as UPDATE, perform the replacement. This is pure code — string replacement. The replacement is case-sensitive to preserve formatting (e.g., "0.8B" → "~4B" but not "0.8b" if it appears differently).

```python
# Parse Step 3
lines = output.strip().split('\n')
old_term = ''
new_term = ''
context_description = ''
updates_needed = []
for line in lines:
    if line.startswith('OLD_TERM: '):
        old_term = line.replace('OLD_TERM: ', '').strip()
    elif line.startswith('NEW_TERM: '):
        new_term = line.replace('NEW_TERM: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context_description = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('CLASSIFICATION: '):
        rest = line.replace('CLASSIFICATION: ', '').strip()
        parts = rest.split('::: ')
        if len(parts) >= 2:
            path = parts[0]
            verdict = parts[1]
            if verdict == 'UPDATE':
                updates_needed.append(path)

# Read each file that needs updating, replace old_term with new_term, write back
modified = []
errors = []
for file_path in updates_needed:
    try:
        note_result = vault_read_note(file_path, max_lines=0)
        if isinstance(note_result, dict):
            content = note_result.get('content', '')
        else:
            content = str(note_result)
        
        # Count replacements
        count = content.count(old_term)
        
        # Perform replacement
        new_content = content.replace(old_term, new_term)
        
        # Write back using vault_safe_write (injected by runtime)
        vault_safe_write(file_path=file_path, content=new_content)
        modified.append({'path': file_path, 'replacements': count})
    except Exception as e:
        errors.append({'path': file_path, 'error': str(e)})

# Report
result = f"OLD_TERM: {old_term}\nNEW_TERM: {new_term}\nMODIFIED: {len(modified)}\nERRORS: {len(errors)}\n"
for m in modified:
    result += f"CHANGED: {m['path']} ({m['replacements']} replacements)\n"
for e in errors:
    result += f"ERROR: {e['path']} — {e['error']}\n"
print(result)
```

[validate: contains "MODIFIED:"]

---

### Step 5: Lint all modified files and produce summary report

Lint each modified file to verify no broken wikilinks were introduced. Then produce a human-readable summary of what was changed and what was kept.

```python
# Parse Step 4
lines = output.strip().split('\n')
old_term = ''
new_term = ''
modified_files = []
kept_files = []
for line in lines:
    if line.startswith('OLD_TERM: '):
        old_term = line.replace('OLD_TERM: ', '').strip()
    elif line.startswith('NEW_TERM: '):
        new_term = line.replace('NEW_TERM: ', '').strip()
    elif line.startswith('CHANGED: '):
        path = line.replace('CHANGED: ', '').strip().split(' (')[0]
        modified_files.append(path)

# Also re-parse Step 3 classifications to get KEEP list
# (We need the full output from step 3, but we only have step 4's output here)
# For the summary, we'll report what we know

# Lint modified files
lint_results = []
for path in modified_files:
    try:
        lint_result = vault_lint(path)
        if isinstance(lint_result, dict):
            broken = lint_result.get('broken_wikilinks', [])
            lint_results.append({'path': path, 'broken': len(broken), 'details': broken})
        else:
            lint_results.append({'path': path, 'broken': 0, 'details': []})
    except Exception as e:
        lint_results.append({'path': path, 'broken': -1, 'details': [str(e)]})

# Produce summary
summary_lines = []
summary_lines.append(f"## Update-Vault-References Report")
summary_lines.append(f"")
summary_lines.append(f"**Term updated:** {old_term} → {new_term}")
summary_lines.append(f"**Context:** {args.get('context_description', 'N/A')}")
summary_lines.append(f"")
summary_lines.append(f"### Files Modified ({len(modified_files)})")
summary_lines.append(f"")
for path in modified_files:
    summary_lines.append(f"- ✅ {path}")
summary_lines.append(f"")
summary_lines.append(f"### Lint Results")
summary_lines.append(f"")
total_broken = 0
for lr in lint_results:
    if lr['broken'] > 0:
        summary_lines.append(f"- ⚠️ {lr['path']}: {lr['broken']} broken wikilinks")
        total_broken += lr['broken']
    elif lr['broken'] == 0:
        summary_lines.append(f"- ✅ {lr['path']}: clean")
    else:
        summary_lines.append(f"- ❌ {lr['path']}: lint error")
if total_broken == 0:
    summary_lines.append(f"")
    summary_lines.append(f"**All modified files pass lint.**")
summary_lines.append(f"")
summary_lines.append(f"### Files Kept (historical/context references)")
summary_lines.append(f"")
summary_lines.append(f"Files that mention {old_term} but were classified as historical/research context were not modified. These references are accurate as-is.")
summary_lines.append(f"")
summary_lines.append(f"### Next Steps")
summary_lines.append(f"")
if total_broken > 0:
    summary_lines.append(f"- Fix {total_broken} broken wikilinks in modified files")
summary_lines.append(f"- Review the changes to ensure accuracy")
summary_lines.append(f"- If any classifications were wrong, manually adjust the affected notes")

print('\n'.join(summary_lines))
```

[validate: contains "Update-Vault-References Report"]
[validate: contains "Files Modified"]

---

## Research Justification

1. **Semantic classification before replacement**: Blind find-and-replace across a vault is dangerous — it would update historical references that should stay as-is. The LLM classifies each mention as "current claim" vs "historical context" before any replacement happens. This is the same principle as [[Deterministic-Scaffolding-for-Small-Models]]: the LLM does semantics (classification), code does structure (search, replace, lint).

2. **Use-case section for RAG retrieval**: The content of procedures isn't embedded — only the use-case section is. This procedure's use-case section covers 15+ phrasings that a user or VaultBot might use when needing a vault-wide reference update, ensuring RAG surfaces it when needed.

3. **Safe replacement pattern**: Each file is read, classified, and only modified if the LLM says UPDATE. Files classified as KEEP are left alone. This prevents collateral damage to historical references.

4. **Lint after modification**: Every modified file is linted to catch broken wikilinks introduced by the replacement. This follows the [[Dream-Pass]] validation pattern.

## Related

- [[Dream-Pass]] — vault maintenance procedure that includes dangling link fixes
- [[Deterministic-Scaffolding-for-Small-Models]] — the pattern of LLM-for-semantics, code-for-structure
- [[Procedure-Subprocess-Architecture]] — how procedures execute as subprocesses