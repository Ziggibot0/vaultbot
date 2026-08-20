"""Procedure Compiler -- parse markdown procedure notes into structured objects.

This is the "compile" half of the compile-then-execute pattern (see
[[Procedural-Bootstrap-and-Evolution-Plan]]). It takes a markdown note
with ``type: procedure`` (or ``exemplar_procedure: true``) in its
frontmatter and produces a structured :class:`Procedure` object with
typed :class:`Step` objects.

The parser is pure deterministic -- no LLM calls, no external dependencies.
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

**Spec v2.1** (PREFERRED -- human-readable): ``### Step N:`` headers with
a short summary, followed by a bare ```` ```python ```` fence or
``[llm: ...]`` tag on the next lines. This is the format all NEW procedures
should use -- the header's summary becomes the step's ``instruction``,
shown in progress callbacks and logs. Without it, steps have no
human-readable description::

    ## Steps

    ### Step 1: Search the vault for related notes

    ```python
    results = vault_search(query=claim, k=5)
    related_notes = [r["file_path"] for r in results]
    ```

    ### Step 2: Determine if the claim is supported

    [llm: Given the results, determine if the claim is supported.]

    ### Step 3: Log the verification result

    ```python
    log.append({"verdict": result})
    ```

The parser also handles the **mixed** format (``### Step N:`` header
followed by a matching ``N. `` ```` ```python ```` block on the next
line) -- it merges the header's instruction with the numbered code block
into a single step. This is the dominant format across ~117 existing
procedures.

v2 frontmatter adds ``description`` (one-line summary for retrieval
efficiency) and ``allowed_tools`` (permission scope for subprocess
execution).

Annotations (v1, still supported in v2 text steps):
  - ``[validate: ...]``  -> :attr:`Step.validation`
  - ``[condition: ...]`` -> :attr:`Step.condition`
  - ``[branch: step N]`` -> :attr:`Step.branch_target`

Step/Procedure dataclasses live in ``procedure_types.py`` (extracted
to avoid circular imports with ``procedure_step_compilers.py``).
The step compiler functions (``_compile_classify``, ``_compile_call``,
etc.) and the dispatch DSL parser live in ``procedure_step_compilers.py``.

See:
  - [[Procedural-Bootstrap-and-Evolution-Plan]]
  - [[Deterministic-Scaffolding-for-Small-Models]]
  - [[Procedure-Subprocess-Architecture]]
  - ``step_gate_runtime.py`` -- the execution half
  - ``procedure_tracker.py`` -- pass/fail logging
"""

from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path

from procedure_types import Procedure, Step  # noqa: F401 -- re-exported for callers

logger = logging.getLogger(__name__)

# PyYAML is optional -- only needed for ## Dispatch sections.
# If missing, dispatch sections are silently skipped (no DSL).
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    yaml = None  # type: ignore


# -- Regex patterns --------------------------------------------------------

# Inline annotations in square brackets (v1, still used in v2 text steps)
_VALIDATE_RE = re.compile(r"\[validate:\s*(.+?)\]", re.IGNORECASE)
_CONDITION_RE = re.compile(r"\[condition:\s*(.+?)\]", re.IGNORECASE)
_BRANCH_RE = re.compile(r"\[branch:\s*step\s+(\d+(?:\.\d+)?)\]", re.IGNORECASE)

# "## Steps" section header
_STEPS_HEADER_RE = re.compile(r"^##\s+Steps\s*$", re.MULTILINE | re.IGNORECASE)

# [llm: ...] tag (single-line form)
_LLM_RE = re.compile(r"\[llm:\s*(.+?)\]\s*$")


# -- Frontmatter parser (simple, no PyYAML dependency) ---------------------


def _parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """Parse YAML frontmatter from markdown text.

    Returns ``(frontmatter_dict, frontmatter_str, body_str)``.
    Handles flat key-value pairs and simple list values (``- item``).
    Does not support nested mappings -- procedures don't need them.
    """
    if not text.startswith("---"):
        return {}, "", text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, "", text

    fm_str = text[3:end].strip()
    body = text[end + 4 :].lstrip()  # skip past closing ---

    fm: dict = {}
    current_key: str | None = None
    current_list: list | None = None

    for line in fm_str.split("\n"):
        line = line.rstrip()
        if not line:
            continue

        # List item: "  - value"
        if line.startswith("  - ") and current_key:
            value = line[4:].strip().strip('"').strip("'")
            if current_list is None:
                current_list = []
                fm[current_key] = current_list
            current_list.append(value)
            continue

        # Key-value pair: "key: value"
        if ":" in line:
            current_list = None
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            if value:
                fm[key] = value
                current_key = key
            else:
                # Empty value -- might start a list
                current_key = key
                current_list = None

    return fm, fm_str, body


# -- Annotation extraction (for text steps) -------------------------------


def _extract_annotations(
    text: str,
) -> tuple[str, str | None, str | None, int | None]:
    """Extract inline annotations from a step instruction.

    Returns ``(clean_instruction, validation, condition, branch_target)``.
    Annotations are removed from the returned instruction text.
    """
    validation: str | None = None
    condition: str | None = None
    branch_target: float | None = None

    m = _VALIDATE_RE.search(text)
    if m:
        validation = m.group(1).strip()

    m = _CONDITION_RE.search(text)
    if m:
        condition = m.group(1).strip()

    m = _BRANCH_RE.search(text)
    if m:
        branch_target = float(m.group(1))

    # Strip annotations from instruction
    clean = text
    for pattern in (_VALIDATE_RE, _CONDITION_RE, _BRANCH_RE):
        clean = pattern.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    return clean, validation, condition, branch_target


# -- Step parser (handles v1 text + v2 code/llm) ---------------------------


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
        # NOTE: We do NOT pre-truncate at the next ## header here.
        # Multi-line [llm: ...] steps can contain ## headers as part of
        # the LLM instruction text (e.g. Code-Audit-Senior-Review step 3
        # has "## File Audited" inside the LLM prompt). Pre-truncating
        # would cut those instructions in half and lose the llm_instruction.
        # Instead, we pass the entire body after ## Steps to the line
        # parser, which stops at ## headers ONLY when not inside a code
        # block or collecting an LLM instruction (see _SECTION_BOUNDARY
        # check below).
        steps_text = body[steps_start:]

    steps: list[Step] = []
    lines = steps_text.split("\n")
    in_code_block = False
    code_lines: list[str] = []
    current_step: Step | None = None
    collecting_llm: bool = False
    llm_lines: list[str] = []
    seen_steps: bool = False  # have we parsed at least one step yet?
    current_from_header: bool = False  # current step came from "### Step N:" header

    i = 0
    while i < len(lines):
        line = lines[i]

        # Inside a code block -- collect until closing ```
        if in_code_block:
            if line.strip() == "```":
                in_code_block = False
                if current_step is not None:
                    current_step.code = textwrap.dedent("\n".join(code_lines))
            else:
                code_lines.append(line)
            i += 1
            continue

        # Collecting multi-line [llm: ...] text
        if collecting_llm:
            llm_lines.append(line.strip())
            joined = " ".join(llm_lines)
            if "]" in joined:
                # Extract up to the closing ]
                close_idx = joined.index("]")
                llm_text = joined[:close_idx].strip()
                if current_step is not None:
                    current_step.llm_instruction = llm_text
                collecting_llm = False
                llm_lines = []
            i += 1
            continue

        # Section boundary: once we've parsed at least one step, a ## header
        # at the top level (not inside a code block or LLM collection -- both
        # handled above) marks the end of the Steps section. This replaces
        # the old pre-truncation that broke multi-line LLM steps containing
        # ## headers in their instruction text.
        if seen_steps and re.match(r"^##\s+", line):
            break

        # Check for numbered step start (supports decimals: 1, 1.5, 2.5, etc.)
        step_match = re.match(r"^(\d+(?:\.\d+)?)\.\s+(.+)", line)

        # Bare ```python fence on the line(s) after a "### Step N:" header
        # (or after the header's instruction text) -> code step. This is the
        # dominant format across ~107 procedures.
        if (
            not step_match
            and line.strip().startswith("```python")
            and current_step is not None
            and current_from_header
            and not in_code_block
        ):
            in_code_block = True
            code_lines = []
            after_fence = line.strip()[len("```python") :].strip()
            if after_fence:
                code_lines.append(after_fence)
            current_step.step_type = "code"
            current_step.instruction = current_step.instruction or ""
            i += 1
            continue

        # Bare [llm: ...] on the line(s) after a "### Step N:" header
        # (or after the header's instruction text) -> llm step. This lets
        # procedures have a human-readable summary in the header AND an
        # LLM instruction below it:
        #   ### Step 1: Classify the results
        #   [llm: Classify each result as relevant or not.]
        if (
            not step_match
            and current_step is not None
            and current_from_header
            and not collecting_llm
            and line.strip().startswith("[llm:")
        ):
            llm_match = _LLM_RE.match(line.strip())
            if llm_match:
                # Single-line [llm: ...]
                current_step.step_type = "llm"
                current_step.llm_instruction = llm_match.group(1).strip()
                seen_steps = True
                i += 1
                continue
            else:
                # Multi-line [llm: ... -- collect until closing ]
                collecting_llm = True
                llm_lines = [line.strip()[5:]]  # skip "[llm:"
                current_step.step_type = "llm"
                seen_steps = True
                i += 1
                continue

        # Also accept "### Step N:" headers as step starts (the format used
        # by ~107 procedures, e.g. Know-Thyself). The instruction is the
        # text after the colon; a bare ```python fence on following lines
        # makes it a code step.
        header_match = None
        if not step_match:
            header_match = re.match(
                r"^#{2,4}\s+Step\s+(\d+(?:\.\d+)?)[:\.\)]?\s*(.*)$",
                line.strip(),
                re.IGNORECASE,
            )
            if header_match:
                num_h = float(header_match.group(1))
                rest_h = header_match.group(2).strip()
                if current_step is not None:
                    steps.append(current_step)
                # Check for [llm: ...] tag in the header's instruction text
                # (e.g. "### Step 1: [llm: You are a classifier...]")
                if rest_h.startswith("[llm:"):
                    llm_match = _LLM_RE.match(rest_h)
                    if llm_match:
                        # Single-line [llm: ...] on the header
                        current_step = Step(
                            number=num_h,
                            instruction="",
                            step_type="llm",
                            llm_instruction=llm_match.group(1).strip(),
                        )
                    else:
                        # Multi-line [llm: ... -- collect until closing ]
                        collecting_llm = True
                        llm_lines = [rest_h[5:]]  # skip "[llm:"
                        current_step = Step(
                            number=num_h,
                            instruction="",
                            step_type="llm",
                        )
                else:
                    current_step = Step(
                        number=num_h,
                        instruction=re.sub(r"\*\*(.+?)\*\*", r"\1", rest_h),
                        step_type="text",
                    )
                current_from_header = True
                seen_steps = True
                i += 1
                continue

        if step_match:
            num = float(step_match.group(1))
            rest = step_match.group(2).strip()

            # --- Merge: if the previous step was a header step with the
            # SAME number, upgrade it to code/llm instead of creating a
            # duplicate.  This handles the common pattern:
            #   ### Step 0: Scan
            #   0. ```python
            # Without this, the compiler creates two steps with the same
            # number (a text step from the header + a code step from the
            # numbered block), and only the text step executes.
            if (
                current_step is not None
                and current_from_header
                and current_step.number == num
            ):
                if rest.startswith("```python"):
                    in_code_block = True
                    code_lines = []
                    after_fence = rest[len("```python") :].strip()
                    if after_fence:
                        code_lines.append(after_fence)
                    current_step.step_type = "code"
                    current_step.instruction = current_step.instruction or ""
                    seen_steps = True
                    i += 1
                    continue
                elif rest.startswith("[llm:"):
                    llm_match = _LLM_RE.match(rest)
                    if llm_match:
                        current_step.step_type = "llm"
                        current_step.llm_instruction = llm_match.group(1).strip()
                        seen_steps = True
                        i += 1
                        continue
                    else:
                        collecting_llm = True
                        llm_lines = [rest[5:]]
                        current_step.step_type = "llm"
                        seen_steps = True
                        i += 1
                        continue
                # else: fall through -- treat as a new step

            # Save previous step (if not merged above)
            if current_step is not None:
                if current_from_header:
                    steps.append(current_step)
                    current_step = None
                    current_from_header = False
                else:
                    steps.append(current_step)
                    current_step = None

            current_from_header = False

            # Check for code block: "1. ```python"
            if rest.startswith("```python"):
                in_code_block = True
                code_lines = []
                # Capture any code on the same line after ```python
                after_fence = rest[len("```python") :].strip()
                if after_fence:
                    code_lines.append(after_fence)
                current_step = Step(
                    number=num,
                    instruction="",
                    step_type="code",
                )
                seen_steps = True
            # Check for LLM tag: "1. [llm: ...]"
            elif rest.startswith("[llm:"):
                llm_match = _LLM_RE.match(rest)
                if llm_match:
                    # Single-line [llm: ...]
                    current_step = Step(
                        number=num,
                        instruction="",
                        step_type="llm",
                        llm_instruction=llm_match.group(1).strip(),
                    )
                    seen_steps = True
                else:
                    # Multi-line [llm: ... -- collect until closing ]
                    collecting_llm = True
                    llm_lines = [rest[5:]]  # skip "[llm:"
                    current_step = Step(
                        number=num,
                        instruction="",
                        step_type="llm",
                    )
                    seen_steps = True
            else:
                # Text step (v1 format) -- may have annotations
                instruction = re.sub(r"\*\*(.+?)\*\*", r"\1", rest)
                clean, validation, condition, branch_target = _extract_annotations(
                    instruction
                )
                current_step = Step(
                    number=num,
                    instruction=clean,
                    step_type="text",
                    validation=validation,
                    condition=condition,
                    branch_target=branch_target,
                )
                seen_steps = True

        i += 1

    # Don't forget the last step
    if current_step is not None:
        steps.append(current_step)

    return steps


# -- Entry points ----------------------------------------------------------


def compile_from_text(note_name: str, text: str) -> Procedure | None:
    """Compile a procedure from raw markdown text.

    Returns a :class:`Procedure` if the text has ``type: procedure`` or
    ``exemplar_procedure: true`` in its frontmatter, otherwise ``None``.

    Args:
        note_name: The note stem (title without ``.md``).
        text: The full markdown text including frontmatter.
    """
    fm, _fm_str, body = _parse_frontmatter(text)
    if not fm:
        return None
    is_proc = fm.get("type", "").lower() == "procedure"
    is_exemplar = fm.get("exemplar_procedure", "").lower() in ("true", "yes", "1")
    if not (is_proc or is_exemplar):
        return None

    steps = _parse_steps(body)

    # Check for a ## Dispatch section (YAML DSL parent procedure).
    # If present, compile it into an additional code step.
    from procedure_step_compilers import _parse_dispatch_section

    dispatch_steps: list[Step] = []
    if _HAS_YAML and yaml is not None:
        m_dispatch = re.search(
            r"^##\s+Dispatch\s*$",
            body,
            re.MULTILINE | re.IGNORECASE,
        )
        if m_dispatch:
            dispatch_text = body[m_dispatch.end() :]
            # Truncate at the next ## header (if any)
            next_header = re.search(r"^##\s+", dispatch_text, re.MULTILINE)
            if next_header:
                dispatch_text = dispatch_text[: next_header.start()]
            dispatch_steps = _parse_dispatch_section(
                dispatch_text, fm.get("allowed_tools", [])
            )

    all_steps = steps + dispatch_steps

    return Procedure(
        name=note_name,
        file_path="",
        version=fm.get("version", "1.0.0"),
        activation=fm.get("activation", "always"),
        spec_version=fm.get("spec_version", "1"),
        steps=all_steps,
        raw_text=text,
        frontmatter=fm,
        description=fm.get("description", ""),
        allowed_tools=fm.get("allowed_tools", [])
        if isinstance(fm.get("allowed_tools"), list)
        else [],
        model_cartridge=fm.get("model_cartridge", "big"),
    )


def compile_procedure(file_path: str) -> Procedure | None:
    """Compile a procedure from a markdown file on disk.

    Returns a :class:`Procedure` if the file is a procedure note,
    otherwise ``None``.

    Args:
        file_path: Path to the ``.md`` file.
    """
    p = Path(file_path)
    if not p.exists() or p.suffix != ".md":
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 -- corrupt/locked file is non-fatal; caller treats None as "not a procedure"
        logger.debug("compile_procedure: could not read %s: %s", file_path, e)
        return None
    note_name = p.stem
    return compile_from_text(note_name, text)
