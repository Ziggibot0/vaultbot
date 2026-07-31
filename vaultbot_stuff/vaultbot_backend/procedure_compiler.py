"""Procedure Compiler — parse markdown procedure notes into structured objects.

This is the "compile" half of the compile-then-execute pattern (see
[[Procedural-Bootstrap-and-Evolution-Plan]]). It takes a markdown note
with ``type: procedure`` (or ``exemplar_procedure: true``) in its
frontmatter and produces a structured :class:`Procedure` object with
typed :class:`Step` objects.

The parser is pure deterministic — no LLM calls, no external dependencies.
It handles two spec versions:

**Spec v1** (backward-compatible): numbered text steps with optional
inline annotations::

    ## Steps

    1. **Check authority.** Who wrote this? What are their credentials?

    2. Search the vault [validate: mention 2 note titles] [condition: if < 3 notes]

**Spec v2**: numbered steps with embedded code blocks and ``[llm:]`` tags::

    ## Steps

    1. ```python
       results = vault_search(query=claim, k=5)
       ```

    2. [llm: Given the results, determine if the claim is supported.]

    3. ```python
       log.append({"verdict": result})
       ```

v2 frontmatter adds ``description`` (one-line summary for retrieval
efficiency) and ``allowed_tools`` (permission scope for subprocess
execution).

Annotations (v1, still supported in v2 text steps):
  - ``[validate: ...]``  → :attr:`Step.validation`
  - ``[condition: ...]`` → :attr:`Step.condition`
  - ``[branch: step N]`` → :attr:`Step.branch_target`

See:
  - [[Procedural-Bootstrap-and-Evolution-Plan]]
  - [[Deterministic-Scaffolding-for-Small-Models]]
  - [[Procedure-Subprocess-Architecture]]
  - ``step_gate_runtime.py`` — the execution half
  - ``procedure_tracker.py`` — pass/fail logging
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class Step:
    """A single step in a procedure.

    Attributes:
        number: 1-indexed step number as written in the markdown.
        instruction: The instruction text (for text steps), stripped of
            bold markers and inline annotations.
        step_type: One of "text" (v1 default), "code" (v2 embedded
            Python), or "llm" (v2 [llm: ...] tag).
        code: Python code for code steps (``step_type == "code"``).
            None for other step types.
        llm_instruction: Instruction for LLM steps
            (``step_type == "llm"``).  None for other step types.
        validation: Validation criteria parsed from ``[validate: ...]``.
            None if no annotation present.
        condition: Execution condition parsed from ``[condition: ...]``.
            None if no annotation present (step always executes).
        branch_target: Step number to jump to, parsed from
            ``[branch: step N]``.  None if no branch annotation.
    """
    number: int
    instruction: str
    step_type: str = "text"
    code: Optional[str] = None
    llm_instruction: Optional[str] = None
    validation: Optional[str] = None
    condition: Optional[str] = None
    branch_target: Optional[int] = None


@dataclass
class Procedure:
    """A compiled procedure ready for step-gate execution.

    Attributes:
        name: Note stem (title without extension).
        file_path: Path to the source markdown, or '' if compiled from text.
        version: Semantic version from frontmatter (default '1.0.0').
        activation: When the procedure activates — 'always', 'on-match',
            or 'manual' (default 'always').
        spec_version: Format spec version (default '1').
        steps: Ordered list of Step objects.
        raw_text: Full markdown text (for fallback / debugging).
        frontmatter: Parsed frontmatter dict.
        description: One-line summary from frontmatter (v2). Empty string
            if not present. Used for retrieval efficiency — VaultBot
            reads THIS instead of the full procedure body to decide
            whether to invoke.
        allowed_tools: List of tool names the procedure is permitted to
            call (v2). Empty list if not present. The step-gate runtime
            injects only these tools into the subprocess namespace.
    """
    name: str
    file_path: str
    version: str
    activation: str
    spec_version: str
    steps: list[Step]
    raw_text: str
    frontmatter: dict
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model_cartridge: str = "big"  # "big", "small", or "vision"


# ── Regex patterns ────────────────────────────────────────────────────────

# Inline annotations in square brackets (v1, still used in v2 text steps)
_VALIDATE_RE = re.compile(r'\[validate:\s*(.+?)\]', re.IGNORECASE)
_CONDITION_RE = re.compile(r'\[condition:\s*(.+?)\]', re.IGNORECASE)
_BRANCH_RE = re.compile(r'\[branch:\s*step\s+(\d+)\]', re.IGNORECASE)

# "## Steps" section header
_STEPS_HEADER_RE = re.compile(r'^##\s+Steps\s*$', re.MULTILINE | re.IGNORECASE)

# [llm: ...] tag (single-line form)
_LLM_RE = re.compile(r'\[llm:\s*(.+?)\]\s*$')


# ── Frontmatter parser (simple, no PyYAML dependency) ───────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """Parse YAML frontmatter from markdown text.

    Returns ``(frontmatter_dict, frontmatter_str, body_str)``.
    Handles flat key-value pairs and simple list values (``- item``).
    Does not support nested mappings — procedures don't need them.
    """
    if not text.startswith('---'):
        return {}, '', text

    end = text.find('\n---', 3)
    if end == -1:
        return {}, '', text

    fm_str = text[3:end].strip()
    body = text[end + 4:].lstrip()  # skip past closing ---

    fm: dict = {}
    current_key: str | None = None
    current_list: list | None = None

    for line in fm_str.split('\n'):
        line = line.rstrip()
        if not line:
            continue

        # List item: "  - value"
        if line.startswith('  - ') and current_key:
            value = line[4:].strip().strip('"').strip("'")
            if current_list is None:
                current_list = []
                fm[current_key] = current_list
            current_list.append(value)
            continue

        # Key-value pair: "key: value"
        if ':' in line:
            current_list = None
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if value:
                fm[key] = value
                current_key = key
            else:
                # Empty value — might start a list
                current_key = key
                current_list = None

    return fm, fm_str, body


# ── Annotation extraction (for text steps) ──────────────────────────────

def _extract_annotations(
    text: str,
) -> tuple[str, Optional[str], Optional[str], Optional[int]]:
    """Extract inline annotations from a step instruction.

    Returns ``(clean_instruction, validation, condition, branch_target)``.
    Annotations are removed from the returned instruction text.
    """
    validation: Optional[str] = None
    condition: Optional[str] = None
    branch_target: Optional[int] = None

    m = _VALIDATE_RE.search(text)
    if m:
        validation = m.group(1).strip()

    m = _CONDITION_RE.search(text)
    if m:
        condition = m.group(1).strip()

    m = _BRANCH_RE.search(text)
    if m:
        branch_target = int(m.group(1))

    # Strip annotations from instruction
    clean = text
    for pattern in (_VALIDATE_RE, _CONDITION_RE, _BRANCH_RE):
        clean = pattern.sub('', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    return clean, validation, condition, branch_target


# ── Step parser (handles v1 text + v2 code/llm) ─────────────────────────

def _parse_steps(body: str) -> list[Step]:
    """Parse numbered steps from the body of a procedure note.

    Handles three step types:
    - **text** (v1): ``1. instruction text [validate: ...]``
    - **code** (v2): ``1. \\`\\`\\`python\\n code \\`\\`\\```
    - **llm** (v2): ``1. [llm: instruction]``

    Looks for a ``## Steps`` section first; if found, only steps within
    that section are parsed (up to the next ``##`` header). If no
    ``## Steps`` header exists, falls back to scanning the entire body.
    """
    steps_text = body

    m = _STEPS_HEADER_RE.search(body)
    if m:
        steps_start = m.end()
        # Find the next ## section header
        next_section = re.search(r'^##\s+', body[steps_start:], re.MULTILINE)
        if next_section:
            steps_text = body[steps_start:steps_start + next_section.start()]
        else:
            steps_text = body[steps_start:]

    steps: list[Step] = []
    lines = steps_text.split('\n')
    in_code_block = False
    code_lines: list[str] = []
    current_step: Step | None = None
    collecting_llm: bool = False
    llm_lines: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Inside a code block — collect until closing ```
        if in_code_block:
            if line.strip() == '```':
                in_code_block = False
                if current_step is not None:
                    current_step.code = textwrap.dedent('\n'.join(code_lines))
            else:
                code_lines.append(line)
            i += 1
            continue

        # Collecting multi-line [llm: ...] text
        if collecting_llm:
            llm_lines.append(line.strip())
            joined = ' '.join(llm_lines)
            if ']' in joined:
                # Extract up to the closing ]
                close_idx = joined.index(']')
                llm_text = joined[:close_idx].strip()
                if current_step is not None:
                    current_step.llm_instruction = llm_text
                collecting_llm = False
                llm_lines = []
            i += 1
            continue

        # Check for numbered step start
        step_match = re.match(r'^(\d+)\.\s+(.+)', line)

        if step_match:
            # Save previous step
            if current_step is not None:
                steps.append(current_step)

            num = int(step_match.group(1))
            rest = step_match.group(2).strip()

            # Check for code block: "1. ```python"
            if rest.startswith('```python'):
                in_code_block = True
                code_lines = []
                # Capture any code on the same line after ```python
                after_fence = rest[len('```python'):].strip()
                if after_fence:
                    code_lines.append(after_fence)
                current_step = Step(
                    number=num,
                    instruction='',
                    step_type='code',
                )
            # Check for LLM tag: "1. [llm: ...]"
            elif rest.startswith('[llm:'):
                llm_match = _LLM_RE.match(rest)
                if llm_match:
                    # Single-line [llm: ...]
                    current_step = Step(
                        number=num,
                        instruction='',
                        step_type='llm',
                        llm_instruction=llm_match.group(1).strip(),
                    )
                else:
                    # Multi-line [llm: ... — collect until closing ]
                    collecting_llm = True
                    llm_lines = [rest[5:]]  # skip "[llm:"
                    current_step = Step(
                        number=num,
                        instruction='',
                        step_type='llm',
                    )
            else:
                # Text step (v1 format) — may have annotations
                instruction = re.sub(r'\*\*(.+?)\*\*', r'\1', rest)
                clean, validation, condition, branch_target = (
                    _extract_annotations(instruction)
                )
                current_step = Step(
                    number=num,
                    instruction=clean,
                    step_type='text',
                    validation=validation,
                    condition=condition,
                    branch_target=branch_target,
                )

        i += 1

    # Don't forget the last step
    if current_step is not None:
        steps.append(current_step)

    return steps


# ── Public API ────────────────────────────────────────────────────────────

def compile_procedure(file_path: str) -> Optional[Procedure]:
    """Compile a markdown procedure note from disk.

    Returns a :class:`Procedure` if the note has ``type: procedure`` or
    ``exemplar_procedure: true`` in its frontmatter, otherwise ``None``.
    """
    path = Path(file_path)
    if not path.exists():
        return None

    text = path.read_text(encoding='utf-8', errors='replace')
    proc = compile_from_text(path.stem, text)
    if proc is not None:
        proc.file_path = str(path)
    return proc


def compile_from_text(note_name: str, text: str) -> Optional[Procedure]:
    """Compile a procedure from raw markdown text.

    Same as :func:`compile_procedure` but works on in-memory text
    (e.g., from FUSED retrieval results that carry note content).
    """
    fm, _fm_str, body = _parse_frontmatter(text)

    is_procedure = (
        str(fm.get('type', '')).lower() == 'procedure'
        or str(fm.get('exemplar_procedure', '')).lower() == 'true'
        or fm.get('exemplar_procedure') is True
    )
    if not is_procedure:
        return None

    steps = _parse_steps(body)

    # Parse allowed_tools (may be a list or absent)
    allowed = fm.get('allowed_tools', [])
    if isinstance(allowed, str):
        allowed = [allowed]

    return Procedure(
        name=note_name,
        file_path='',  # no file when compiling from text
        version=fm.get('version', '1.0.0'),
        activation=fm.get('activation', 'always'),
        spec_version=str(fm.get('spec_version', '1')),
        steps=steps,
        raw_text=text,
        frontmatter=fm,
        description=fm.get('description', ''),
        allowed_tools=allowed,
        model_cartridge=str(fm.get('model_cartridge', 'big')).strip().lower(),
    )