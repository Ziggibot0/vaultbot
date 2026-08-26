---
type: procedure
status: experimental
baseline: true
created: 2026-08-11
summary: "Introspection procedure that lets VaultBot ask and answer ANY question about its own code, procedures, and architecture. Deterministic code reading + vault search for locating and tracing dependencies, with the big LLM only for optional final synthesis. Every finding includes provenance (file path + line numbers)."
description: "Classify question -> locate target files -> read source -> trace dependencies -> extract relevant sections -> optional big-LLM synthesis with provenance. The meta-reasoning layer."
allowed_tools:
  - code_read
  - vault_search
  - vault_list
  - vault_lint
  - llm_generate
  - run_procedure
tags: [procedure, introspection, meta-reasoning, self-knowledge, provenance, deterministic]
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Introspection: Meta-Reasoning Layer

## Purpose

This procedure lets VaultBot ask and answer ANY question about its own code, procedures, and architecture. It is the **meta-reasoning layer** — the mechanism by which VaultBot can know itself.

The core principle: **deterministic code reading does the work, not the LLM.** This is because the vault and backend are physical files on disk — reading them is a mechanical operation, not a reasoning task. The LLM is only used for:
1. Question classification (semantic task — understanding what kind of question is being asked, because classification requires understanding meaning)
2. Optional final synthesis (weaving findings into a coherent answer, because natural language generation is what LLMs do best)

Everything else — locating files, reading source code, tracing imports, finding procedure calls, extracting relevant sections — is done by deterministic Python code with `code_read`, `vault_list`, and `vault_search`. Therefore, even a 0.8B model can use this procedure effectively, because the model never needs to reason about code — it just interprets what the deterministic steps already found.

## Design Principle: Code for Discovery, LLM for Interpretation

Following [[Deterministic-Scaffolding-for-Small-Models]]: "The AI proposes; the scaffolding disposes." The deterministic layer handles:
- File discovery (which files exist, where are they)
- Source reading (what does the code say)
- Dependency tracing (what does this import/call/reference)
- Section extraction (which lines are relevant to the question)
- Provenance tracking (every finding cites its exact source)

The LLM handles:
- Question classification (what kind of introspection is this?)
- Final synthesis (optional — weave findings into a coherent answer)

## Question Types

| Type | Description | Primary Tool |
|------|-------------|--------------|
| code | How does a specific backend module/function work? | code_read |
| procedure | How does a specific procedure work? What are its steps? | code_read + vault_list |
| architecture | How do components connect? What's the overall design? | vault_search + code_read |
| vault_knowledge | What does the vault know about topic X? | vault_search |
| tool | What tools are available? How does tool X work? | code_read (step_gate_runtime.py) |

## Why This Exists

VaultBot needs a way to answer questions about its own code, procedures, and
architecture without the LLM reasoning over raw files. This procedure makes
deterministic code reading do the discovery work, reserving the LLM for
classification and optional synthesis. The tradeoff: every finding must cite
provenance (file path + line numbers), so unsourced claims are rejected.

## Inputs

- `question`: The introspection question to answer
- `context`: Additional context (optional)

## Outputs

- Question classification (type + target)
- Located files (with paths)
- Source code excerpts (with file paths + line numbers)
- Dependency traces (imports, calls, references)
- Optional synthesis with provenance

---

## Steps

### Step 1: Classify the question (triple-try)

Classify what kind of introspection question this is and what target (file, procedure, concept) it's about. This is a semantic task — the LLM understands what the question is asking.

```python
from collections import Counter

question = args.get('question', '')
context = args.get('context', '')

if not question:
    print("ERROR: no question provided")
    import sys
    sys.exit(1)

# Triple-try classification
classify_prompt = f"""Classify this introspection question about VaultBot's own system.

Question: {question}

Context: {context}

Respond in this EXACT format:
TYPE: [code | procedure | architecture | vault_knowledge | tool]
TARGET: [the specific file name, procedure name, module name, or concept the question is about]
SEARCH_TERMS: [2-3 terms to search for in the vault or file system]

Rules:
- TYPE=code: asking about how a specific backend Python file/function works
- TYPE=procedure: asking about how a specific procedure note works
- TYPE=architecture: asking about how components connect or the overall design
- TYPE=vault_knowledge: asking what the vault knows about a topic
- TYPE=tool: asking about what tools are available or how a specific tool works"""

responses = []
for _ in range(3):
    resp = llm_generate(classify_prompt).strip()
    responses.append(resp)

# Parse each response
def parse_classification(text):
    qtype = 'architecture'  # safe default
    target = ''
    search_terms = ''
    for line in text.split('\n'):
        if line.startswith('TYPE:'):
            val = line.replace('TYPE:', '').strip().lower()
            if val in ['code', 'procedure', 'architecture', 'vault_knowledge', 'tool']:
                qtype = val
        elif line.startswith('TARGET:'):
            target = line.replace('TARGET:', '').strip()
        elif line.startswith('SEARCH_TERMS:'):
            search_terms = line.replace('SEARCH_TERMS:', '').strip()
    return (qtype, target, search_terms)

parses = [parse_classification(r) for r in responses]

# Majority vote on type
type_votes = [p[0] for p in parses]
type_winner = Counter(type_votes).most_common(1)[0][0]

# Use the most common target (or first non-empty)
targets = [p[1] for p in parses if p[1]]
target = Counter(targets).most_common(1)[0][0] if targets else ''

# Use the most common search terms (or first non-empty)
terms = [p[2] for p in parses if p[2]]
search_terms = Counter(terms).most_common(1)[0][0] if terms else question

result = f"TYPE: {type_winner}\nTARGET: {target}\nSEARCH_TERMS: {search_terms}\nQUESTION: {question}\nCONTEXT: {context}"
print(result)
```

[validate: contains "TYPE:"]
[validate: contains "TARGET:"]

---

### Step 2: Locate target files (DETERMINISTIC — zero LLM cost)

Based on the question type and target, find the relevant files. This is pure deterministic file system operations.

```python
import os
from pathlib import Path

# Parse Step 1
lines = output.strip().split('\n')
qtype = 'architecture'
target = ''
search_terms = ''
question = ''
context = ''

for line in lines:
    if line.startswith('TYPE: '):
        qtype = line.replace('TYPE: ', '').strip()
    elif line.startswith('TARGET: '):
        target = line.replace('TARGET: ', '').strip()
    elif line.startswith('SEARCH_TERMS: '):
        search_terms = line.replace('SEARCH_TERMS: ', '').strip()
    elif line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()

vault_path = Path(os.environ.get('VAULT_PATH', '.'))
backend_dir = vault_path / 'vaultbot' / 'vaultbot_backend'
procedures_dir = vault_path / 'vaultbot' / 'System' / 'Procedures'

located_files = []
location_notes = []

# --- TYPE=code: search for Python files matching the target ---
if qtype == 'code':
    # Search backend .py files
    target_lower = target.lower().replace('.py', '').replace('-', '_')
    for f in os.listdir(str(backend_dir)):
        if f.endswith('.py'):
            fname_lower = f.lower().replace('.py', '').replace('-', '_')
            if target_lower in fname_lower or fname_lower in target_lower:
                located_files.append(str(backend_dir / f))
                location_notes.append(f"Found backend file: {f}")

    # If no exact match, search file contents for the target
    if not located_files:
        for f in os.listdir(str(backend_dir)):
            if f.endswith('.py'):
                try:
                    text = (backend_dir / f).read_text(encoding='utf-8', errors='replace')
                    if target_lower in text.lower():
                        located_files.append(str(backend_dir / f))
                        location_notes.append(f"Found target mentioned in: {f}")
                except:
                    pass

# --- TYPE=procedure: search for procedure notes matching the target ---
elif qtype == 'procedure':
    target_lower = target.lower().replace(' ', '-')
    for root, dirs, files in os.walk(str(procedures_dir)):
        for f in files:
            if f.endswith('.md'):
                fname_stem = f[:-3].lower()
                if target_lower in fname_stem or fname_stem in target_lower:
                    located_files.append(str(Path(root) / f))
                    location_notes.append(f"Found procedure note: {f}")

    # Also search vault-wide for procedure notes
    if not located_files:
        results = vault_search(f"{target} procedure", k=5)
        for r in results:
            located_files.append(r.get('file_path', ''))
            location_notes.append(f"Vault search found: {r.get('name', '')}")

# --- TYPE=tool: read the step_gate_runtime.py tool registry ---
elif qtype == 'tool':
    tool_file = str(backend_dir / 'step_gate_runtime.py')
    located_files.append(tool_file)
    location_notes.append("Tool registry is in step_gate_runtime.py (_build_tool_preamble)")

    # Also check agent_tools.py if it exists
    agent_tools = str(backend_dir / 'agent_tools.py')
    if os.path.exists(agent_tools):
        located_files.append(agent_tools)
        location_notes.append("Agent tools defined in agent_tools.py")

# --- TYPE=architecture: search vault for architecture notes + key backend files ---
elif qtype == 'architecture':
    # Search vault for architecture-related notes
    results = vault_search(f"{search_terms} architecture", k=5)
    for r in results:
        located_files.append(r.get('file_path', ''))
        location_notes.append(f"Vault search found: {r.get('name', '')}")

    # Also include key backend files
    key_files = ['main.py', 'chat_handler.py', 'step_gate_runtime.py', 'procedure_compiler.py']
    for kf in key_files:
        p = str(backend_dir / kf)
        if os.path.exists(p) and p not in located_files:
            located_files.append(p)
            location_notes.append(f"Key backend file: {kf}")

# --- TYPE=vault_knowledge: search vault for knowledge notes ---
elif qtype == 'vault_knowledge':
    results = vault_search(search_terms, k=10)
    for r in results:
        located_files.append(r.get('file_path', ''))
        location_notes.append(f"Vault search found: {r.get('name', '')} (score: {r.get('score', 0):.2f})")

# Deduplicate
seen = set()
unique_files = []
for f in located_files:
    if f and f not in seen:
        seen.add(f)
        unique_files.append(f)

located_files = unique_files

if not located_files:
    # Fallback: broad vault search
    results = vault_search(search_terms, k=5)
    for r in results:
        fp = r.get('file_path', '')
        if fp and fp not in located_files:
            located_files.append(fp)
            location_notes.append(f"Fallback vault search: {r.get('name', '')}")

result = f"TYPE: {qtype}\nTARGET: {target}\nQUESTION: {question}\nCONTEXT: {context}\nLOCATED_FILES: {' | '.join(located_files)}\nLOCATION_NOTES: {' | '.join(location_notes)}"
print(result)
```

[validate: contains "LOCATED_FILES"]

---

### Step 3: Read source code and extract relevant sections (DETERMINISTIC — zero LLM cost)

Read each located file and extract sections relevant to the question. For code files, look for function/class definitions and references to the target. For markdown files, extract the full content (procedures are usually short enough).

```python
import os
import re
from pathlib import Path

# Parse Step 2
lines = output.strip().split('\n')
qtype = 'architecture'
target = ''
question = ''
context = ''
located_files_str = ''

for line in lines:
    if line.startswith('TYPE: '):
        qtype = line.replace('TYPE: ', '').strip()
    elif line.startswith('TARGET: '):
        target = line.replace('TARGET: ', '').strip()
    elif line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('LOCATED_FILES: '):
        located_files_str = line.replace('LOCATED_FILES: ', '').strip()

located_files = [f.strip() for f in located_files_str.split('|') if f.strip()]

# Read each file and extract relevant sections
file_contents = {}  # file_path -> {total_lines, excerpts: [{start, end, content, reason}]}

target_lower = target.lower()
question_lower = question.lower()
# Extract key terms from the question for content matching
question_terms = [t.lower() for t in re.findall(r'\b[a-zA-Z_]{4,}\b', question) if t.lower() not in {
    'that', 'this', 'with', 'from', 'what', 'how', 'does', 'work', 'about',
    'your', 'yourself', 'procedure', 'system', 'vaultbot', 'tell', 'please',
    'which', 'where', 'when', 'would', 'could', 'should', 'there', 'their',
}]

for file_path in located_files[:8]:  # cap at 8 files to avoid token explosion
    try:
        p = Path(file_path)
        if not p.exists():
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        lines_list = text.split('\n')
        total_lines = len(lines_list)

        if file_path.endswith('.py'):
            # For Python files: extract function/class definitions and lines matching target/terms
            excerpts = []

            # Find all function/class definitions
            for i, line in enumerate(lines_list):
                stripped = line.strip()
                if re.match(r'^(def |class |async def )', stripped):
                    # Extract the definition + body (until next def/class at same or lower indent)
                    start = i + 1  # 1-indexed
                    indent = len(line) - len(line.lstrip())
                    end = start
                    for j in range(i + 1, min(i + 80, total_lines)):
                        next_line = lines_list[j]
                        next_stripped = next_line.strip()
                        if next_stripped and not next_stripped.startswith('#'):
                            next_indent = len(next_line) - len(next_line.lstrip())
                            if next_indent <= indent and re.match(r'^(def |class |async def )', next_stripped):
                                break
                        end = j + 1

                    # Check if this definition matches the target or question terms
                    def_text = '\n'.join(lines_list[i:end]).lower()
                    is_relevant = False
                    if target_lower and target_lower in stripped.lower():
                        is_relevant = True
                    elif any(t in def_text for t in question_terms):
                        is_relevant = True

                    if is_relevant:
                        content = '\n'.join(lines_list[i:min(end, i+60)])
                        excerpts.append({
                            'start': start,
                            'end': min(end, i + 60),
                            'content': content,
                            'reason': f"Function/class definition: {stripped[:80]}"
                        })

            # Also search for lines mentioning the target directly
            if target_lower and not excerpts:
                for i, line in enumerate(lines_list):
                    if target_lower in line.lower():
                        start = max(0, i - 3)
                        end = min(total_lines, i + 8)
                        content = '\n'.join(lines_list[start:end])
                        excerpts.append({
                            'start': start + 1,
                            'end': end,
                            'content': content,
                            'reason': f"Line mentioning '{target}'"
                        })
                        break  # just first match

            # If still no excerpts, read the first 60 lines (imports + module docstring)
            if not excerpts:
                content = '\n'.join(lines_list[:60])
                excerpts.append({
                    'start': 1,
                    'end': min(60, total_lines),
                    'content': content,
                    'reason': 'File header (imports + docstring)'
                })

            file_contents[file_path] = {
                'total_lines': total_lines,
                'excerpts': excerpts[:5]  # cap at 5 excerpts per file
            }

        elif file_path.endswith('.md'):
            # For markdown files: extract the full content (procedures are usually < 800 lines)
            if total_lines <= 200:
                file_contents[file_path] = {
                    'total_lines': total_lines,
                    'excerpts': [{
                        'start': 1,
                        'end': total_lines,
                        'content': text,
                        'reason': 'Full note content'
                    }]
                }
            else:
                # For long notes: extract frontmatter + first section + sections matching target
                excerpts = []
                # Frontmatter + first 50 lines
                excerpts.append({
                    'start': 1,
                    'end': min(50, total_lines),
                    'content': '\n'.join(lines_list[:50]),
                    'reason': 'Note header (frontmatter + intro)'
                })
                # Search for sections matching target terms
                for i, line in enumerate(lines_list):
                    if line.startswith('#') and target_lower and target_lower in line.lower():
                        start = i
                        end = min(i + 40, total_lines)
                        excerpts.append({
                            'start': start + 1,
                            'end': end,
                            'content': '\n'.join(lines_list[start:end]),
                            'reason': f"Section: {line.strip()[:80]}"
                        })
                file_contents[file_path] = {
                    'total_lines': total_lines,
                    'excerpts': excerpts[:4]
                }

    except Exception as e:
        file_contents[file_path] = {
            'total_lines': 0,
            'excerpts': [{
                'start': 0,
                'end': 0,
                'content': f'ERROR reading file: {str(e)}',
                'reason': 'Read error'
            }]
        }

# Build output
result_lines = [f"TYPE: {qtype}", f"TARGET: {target}", f"QUESTION: {question}", f"CONTEXT: {context}"]
for fp, info in file_contents.items():
    result_lines.append(f"FILE: {fp}")
    result_lines.append(f"TOTAL_LINES: {info['total_lines']}")
    for ex in info['excerpts']:
        result_lines.append(f"EXCERPT [{ex['start']}-{ex['end']}] ({ex['reason']}):")
        result_lines.append(ex['content'])
        result_lines.append("---EXCERPT_END---")

result = '\n'.join(result_lines)
print(result)
```

[validate: contains "FILE:"]
[validate: contains "EXCERPT"]

---

### Step 4: Trace dependencies (DETERMINISTIC — zero LLM cost)

For code files, trace imports and function calls. For procedure files, trace run_procedure calls and wikilinks. This builds the dependency graph around the target.

```python
import os
import re
from pathlib import Path

# Parse Step 3 output to get file paths
lines = output.strip().split('\n')
qtype = 'architecture'
target = ''
question = ''
context = ''
file_paths = []
current_file = None

for line in lines:
    if line.startswith('TYPE: '):
        qtype = line.replace('TYPE: ', '').strip()
    elif line.startswith('TARGET: '):
        target = line.replace('TARGET: ', '').strip()
    elif line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('FILE: '):
        current_file = line.replace('FILE: ', '').strip()
        if current_file not in file_paths:
            file_paths.append(current_file)

# Also pull LOCATED_FILES from the preserved data
vault_path = Path(os.environ.get('VAULT_PATH', '.'))
backend_dir = vault_path / 'vaultbot' / 'vaultbot_backend'

dependencies = []  # list of "source -> dependency (type, line)"

for fp in file_paths:
    try:
        p = Path(fp)
        if not p.exists():
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        lines_list = text.split('\n')

        if fp.endswith('.py'):
            # Trace imports
            for i, line in enumerate(lines_list):
                stripped = line.strip()
                # import statements
                if re.match(r'^(import |from .+ import )', stripped):
                    dep = stripped
                    dependencies.append(f"{p.name}:{i+1} -> IMPORT: {dep}")
                # run_procedure calls
                if 'run_procedure(' in stripped:
                    m = re.search(r'run_procedure\(["\']([^"\']+)["\']', stripped)
                    if m:
                        proc_name = m.group(1)
                        dependencies.append(f"{p.name}:{i+1} -> CALLS PROCEDURE: {proc_name}")
                # function calls to other backend modules
                if re.match(r'^\w+\.', stripped) and not stripped.startswith('#'):
                    # Check if it's a call to a known backend module
                    for other_f in os.listdir(str(backend_dir)):
                        if other_f.endswith('.py'):
                            mod_name = other_f[:-3]
                            if stripped.startswith(f'{mod_name}.') and mod_name != p.stem:
                                dependencies.append(f"{p.name}:{i+1} -> USES MODULE: {mod_name}")

        elif fp.endswith('.md'):
            # Trace wikilinks
            wikilinks = re.findall(r'\[\[([^\]]+)\]', text)
            for wl in wikilinks:
                wl_clean = wl.split('|')[0].split('#')[0].strip()
                dependencies.append(f"{p.name} -> WIKILINK: [[{wl_clean}]]")

            # Trace run_procedure calls in code blocks
            for i, line in enumerate(lines_list):
                if 'run_procedure(' in line:
                    m = re.search(r'run_procedure\(["\']([^"\']+)["\']', line)
                    if m:
                        proc_name = m.group(1)
                        dependencies.append(f"{p.name}:{i+1} -> CALLS PROCEDURE: {proc_name}")

    except Exception:
        pass

# Deduplicate dependencies
seen_deps = set()
unique_deps = []
for d in dependencies:
    if d not in seen_deps:
        seen_deps.add(d)
        unique_deps.append(d)

# Build output
result_lines = [
    f"TYPE: {qtype}",
    f"TARGET: {target}",
    f"QUESTION: {question}",
    f"CONTEXT: {context}",
    f"FILES_ANALYZED: {' | '.join(file_paths)}",
    f"DEPENDENCIES_FOUND: {len(unique_deps)}",
]
for d in unique_deps[:30]:  # cap at 30
    result_lines.append(f"DEP: {d}")

# Re-include excerpts from Step 3 for synthesis step
# Parse them from the output
excerpt_lines = []
in_excerpt = False
for line in lines:
    if line.startswith('EXCERPT '):
        in_excerpt = True
        excerpt_lines.append(line)
    elif line == '---EXCERPT_END---':
        in_excerpt = False
        excerpt_lines.append(line)
    elif in_excerpt:
        excerpt_lines.append(line)

result_lines.append("EXCERPTS_FROM_STEP_3:")
result_lines.extend(excerpt_lines)

result = '\n'.join(result_lines)
print(result)
```

[validate: contains "DEPENDENCIES_FOUND"]

---

### Step 5: Optional big-LLM synthesis with provenance

Synthesize the findings into a coherent answer. The big LLM weaves the deterministic findings (code excerpts, dependency traces) into a natural-language answer. Every claim must cite its source file and line numbers.

**This step uses the big LLM cartridge** — the deterministic steps above have already done the hard work of locating, reading, and tracing. The big model just interprets.

```python
import re as _re

# Parse Step 4 output
lines = output.strip().split('\n')
qtype = ''
target = ''
question = ''
context = ''
dependencies = []
excerpts_text = []
in_excerpts = False

for line in lines:
    if line.startswith('TYPE: '):
        qtype = line.replace('TYPE: ', '').strip()
    elif line.startswith('TARGET: '):
        target = line.replace('TARGET: ', '').strip()
    elif line.startswith('QUESTION: '):
        question = line.replace('QUESTION: ', '').strip()
    elif line.startswith('CONTEXT: '):
        context = line.replace('CONTEXT: ', '').strip()
    elif line.startswith('DEP: '):
        dependencies.append(line.replace('DEP: ', '').strip())
    elif line.startswith('EXCERPTS_FROM_STEP_3:'):
        in_excerpts = True
    elif in_excerpts:
        excerpts_text.append(line)

# Build the synthesis prompt with full provenance
excerpts_str = '\n'.join(excerpts_text[:200])  # cap to avoid token explosion
deps_str = '\n'.join(dependencies[:20])

synthesis_prompt = f"""You are an introspection synthesis system. Given deterministic findings about VaultBot's own code, procedures, and architecture, synthesize a coherent answer to the question.

Question: {question}

Context: {context}

Code excerpts and file contents found (with file paths and line numbers):
{excerpts_str}

Dependencies traced:
{deps_str}

Provide a structured answer in this EXACT format:

ANSWER:
[2-5 paragraphs answering the question, citing specific file paths and line numbers]

KEY_FINDINGS:
- [finding 1, with file path + line number]
- [finding 2, with file path + line number]
- [additional findings as needed]

PROVENANCE:
- [file path:line range] -> [what it shows]
- [file path:line range] -> [what it shows]

CONFIDENCE: [high|medium|low]
CONFIDENCE_REASON: [why this confidence level]

GAPS:
- [what could not be determined from the available files, or "none"]

Rules:
- Every claim MUST cite a specific file path and line number range
- Only state findings supported by the code excerpts or dependency traces
- If the available files don't fully answer the question, say so in GAPS
- Do not invent file paths or line numbers
- Be specific: quote function names, variable names, and exact code patterns"""

# Single big LLM call with structured validation
def validate_synthesis(text):
    issues = []
    required_sections = ['ANSWER:', 'KEY_FINDINGS:', 'PROVENANCE:', 'CONFIDENCE:', 'GAPS:']
    for section in required_sections:
        if section not in text:
            issues.append(f"missing section: {section}")
    # Check for at least one file path reference (provenance requirement)
    has_path = bool(_re.search(r'[/\\]\w+\.py|[/\\]\w+\.md', text))
    if not has_path:
        issues.append("no file path references found (provenance requirement)")
    # Check confidence is valid
    has_confidence = False
    for line in text.split('\n'):
        if line.startswith('CONFIDENCE:'):
            conf_val = line.replace('CONFIDENCE:', '').strip().lower()
            if conf_val in ['high', 'medium', 'low']:
                has_confidence = True
            break
    if not has_confidence:
        issues.append("missing or invalid confidence level")
    return len(issues) == 0, issues

synthesis = llm_generate(synthesis_prompt)
is_valid, issues = validate_synthesis(synthesis)

if not is_valid:
    # One retry
    fix_prompt = f"""Your previous response was missing: {'; '.join(issues)}

Please regenerate the synthesis, fixing these issues. Original prompt:

{synthesis_prompt}"""
    synthesis = llm_generate(fix_prompt)
    is_valid, issues = validate_synthesis(synthesis)

if not is_valid:
    # Deterministic fallback: construct answer from raw findings
    fallback_lines = ["SYNTHESIS (fallback - LLM validation failed):"]
    fallback_lines.append(f"\nQuestion: {question}")
    fallback_lines.append(f"\nCode excerpts found:")
    fallback_lines.append(excerpts_str[:2000])
    fallback_lines.append(f"\nDependencies traced:")
    fallback_lines.append(deps_str[:1000])
    fallback_lines.append(f"\nConfidence: low (LLM synthesis failed validation)")
    fallback_lines.append(f"Validation issues: {'; '.join(issues)}")
    synthesis = '\n'.join(fallback_lines)

result = f"SYNTHESIS:\n{synthesis}"
print(result)
```

[validate: contains "SYNTHESIS:"]
[validate: contains "CONFIDENCE:"]

---

## Research Justification

1. **Deterministic Scaffolding** ([[Deterministic-Scaffolding-for-Small-Models]]): "The AI proposes; the scaffolding disposes." Steps 2-4 are pure deterministic code — file discovery, source reading, dependency tracing. The LLM only handles classification (Step 1) and synthesis (Step 5). This follows the sandwich pattern exactly.

2. **Provenance requirement**: Every finding cites its exact source (file path + line numbers). This follows the [[Cite-Provenance]] principle — no unsourced claims. The synthesis validation checks for file path references.

3. **Triple-try consistency** ([[Deterministic-Scaffolding-for-Small-Models]]): Step 1 (classification) uses triple-try with majority vote. Classification is a semantic task where the small model can be inconsistent.

4. **Bite-sized steps**: Each step does ONE thing — classify, locate, read, trace, synthesize. No step combines multiple semantic judgments.

5. **Fail safe**: If the LLM synthesis fails validation, a deterministic fallback constructs an answer from the raw findings. The system degrades to "here's what the code says" not to uncontrolled AI output.

6. **Self-knowledge architecture**: This procedure is the meta-reasoning layer described in [[cognitive-psychology-metacognition-thinking-dispositions-need-for-cognition-refl]] — metacognition (thinking about thinking) improves reasoning quality. VaultBot can now reason about its own mechanisms.

## Related

- [[Think]] — parent reasoning procedure (can dispatch to Introspection for self-knowledge questions)
- [[Deterministic-Scaffolding-for-Small-Models]] — sandwich pattern, triple-try, fail safe
- [[Cite-Provenance]] — provenance requirement for all findings
- [[Procedure-Subprocess-Architecture]] — how procedures execute (code steps run in subprocess with tool injection)
- [[Knowledge-Triad-Ontology-Epistemology-Hermeneutics]] — Ontology (what kind of question?) -> Epistemology (read the code) -> Hermeneutics (synthesize)