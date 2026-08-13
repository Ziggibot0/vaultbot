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

**Spec v2.1** (PREFERRED — human-readable): ``### Step N:`` headers with
a short summary, followed by a bare ```` ```python ```` fence or
``[llm: ...]`` tag on the next lines. This is the format all NEW procedures
should use — the header's summary becomes the step's ``instruction``,
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
line) — it merges the header's instruction with the numbered code block
into a single step. This is the dominant format across ~117 existing
procedures.

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

import json
import logging
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# PyYAML is optional — only needed for ## Dispatch sections.
# If missing, dispatch sections are silently skipped (no DSL).
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    yaml = None  # type: ignore


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

    number: float
    instruction: str
    step_type: str = "text"
    code: str | None = None
    llm_instruction: str | None = None
    validation: str | None = None
    condition: str | None = None
    branch_target: float | None = None


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
_VALIDATE_RE = re.compile(r"\[validate:\s*(.+?)\]", re.IGNORECASE)
_CONDITION_RE = re.compile(r"\[condition:\s*(.+?)\]", re.IGNORECASE)
_BRANCH_RE = re.compile(r"\[branch:\s*step\s+(\d+(?:\.\d+)?)\]", re.IGNORECASE)

# "## Steps" section header
_STEPS_HEADER_RE = re.compile(r"^##\s+Steps\s*$", re.MULTILINE | re.IGNORECASE)

# [llm: ...] tag (single-line form)
_LLM_RE = re.compile(r"\[llm:\s*(.+?)\]\s*$")


# ── Frontmatter parser (simple, no PyYAML dependency) ───────────────────


def _parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """Parse YAML frontmatter from markdown text.

    Returns ``(frontmatter_dict, frontmatter_str, body_str)``.
    Handles flat key-value pairs and simple list values (``- item``).
    Does not support nested mappings — procedures don't need them.
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
                # Empty value — might start a list
                current_key = key
                current_list = None

    return fm, fm_str, body


# ── Annotation extraction (for text steps) ──────────────────────────────


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

        # Inside a code block — collect until closing ```
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
        # at the top level (not inside a code block or LLM collection — both
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
                # Multi-line [llm: ... — collect until closing ]
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
                        # Multi-line [llm: ... — collect until closing ]
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
                # else: fall through — treat as a new step

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
                    # Multi-line [llm: ... — collect until closing ]
                    collecting_llm = True
                    llm_lines = [rest[5:]]  # skip "[llm:"
                    current_step = Step(
                        number=num,
                        instruction="",
                        step_type="llm",
                    )
                    seen_steps = True
            else:
                # Text step (v1 format) — may have annotations
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


# ── Dispatch DSL parser (YAML-based parent procedure orchestration) ──────

# Regex to find the ## Dispatch section in a procedure note.
_DISPATCH_HEADER_RE = re.compile(r"^##\s+Dispatch\s*$", re.MULTILINE | re.IGNORECASE)

# Template variable pattern: {{ variable_name }}
_TEMPLATE_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Jinja2-style filter: {{ var | filter_name }}
_TEMPLATE_FILTER_RE = re.compile(r"\{\{\s*(\w+)\s*\|\s*(\w+)\s*\}\}")


def _resolve_template(template: str, namespace: dict) -> str:
    """Resolve ``{{ var }}`` and ``{{ var | filter }}`` in a string.

    Simple Jinja2-subset: only ``{{ var }}`` and ``{{ var | length }}``
    are supported.  No loops, no conditionals — this is a template, not
    a programming language.
    """

    def _replacer(match: re.Match) -> str:
        var = match.group(1)
        val = namespace.get(var, "")
        if isinstance(val, (dict, list)):
            val = json.dumps(val, default=str)
        return str(val)

    def _filter_replacer(match: re.Match) -> str:
        var = match.group(1)
        filt = match.group(2).strip()
        val = namespace.get(var, "")
        if filt == "length":
            if isinstance(val, (list, dict, str)):
                return str(len(val))
            return "0"
        # Unknown filter — pass through raw
        return str(val)

    result = _TEMPLATE_FILTER_RE.sub(_filter_replacer, template)
    result = _TEMPLATE_RE.sub(_replacer, result)
    return result


def _compile_classify(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile a ``classify`` dispatch entry into a code Step.

    Generates Python that calls ``llm_generate`` with the classification
    prompt, parses the result, and stores it in the namespace.
    """
    prompt = entry.get("prompt", "")
    model = entry.get("model", "small")
    output_as = entry.get("output_as", "classification")

    code = (
        f"# Dispatch: classify (model={model})\n"
        f"_prompt = {json.dumps(prompt)}\n"
        f"_prompt = _resolve_template(_prompt, _dispatch_ns)\n"
        f"_response = llm_generate(_prompt)\n"
        f"_category = _response.strip().lower()\n"
        f'_dispatch_ns["{output_as}"] = _category\n'
        f'result = {{"category": _category, "raw": _response}}\n'
    )
    return Step(
        number=step_num,
        instruction=f"Classify using {model} model → {output_as}",
        step_type="code",
        code=code,
    )


def _compile_call(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile a ``call`` dispatch entry into a code Step.

    Generates Python that calls the named tool and stores the result.
    Supports optional ``retry`` (int, default 1) and ``on_error``
    (list of procedure names to fall back to) fields.

    The ``retry`` field specifies how many times to retry the tool call
    on failure. The ``on_error`` field specifies a fallback chain to
    execute if all retries are exhausted. This makes the DSL robust
    enough for production routing without Python escape hatches.
    """
    tool = entry.get("tool", "")
    output_as = entry.get("output_as", tool + "_result")
    args = entry.get("args", {})
    retry = int(entry.get("retry", 1))
    on_error = entry.get("on_error", [])

    if args:
        args_json = json.dumps(args)
        code = (
            f"# Dispatch: call {tool} (retry={retry}, on_error={json.dumps(on_error)})\n"
            f"_args = json.loads({json.dumps(args_json)})\n"
            f"# Resolve template variables in string args (e.g. {{{{ intent }}}})\n"
            f"for _k, _v in _args.items():\n"
            f"    if isinstance(_v, str):\n"
            f"        _args[_k] = _resolve_template(_v, _dispatch_ns)\n"
            f"_max_retries = {retry}\n"
            f"_last_error = None\n"
            f"for _attempt in range(_max_retries):\n"
            f"    try:\n"
            f"        _result = {tool}(**_args)\n"
            f'        if isinstance(_result, dict) and _result.get("error"):\n'
            f'            _last_error = _result["error"]\n'
            f"            if _attempt < _max_retries - 1:\n"
            f"                continue\n"
            f"        break\n"
            f"    except Exception as _e:\n"
            f"        _last_error = str(_e)\n"
            f"        if _attempt < _max_retries - 1:\n"
            f"            continue\n"
            f'        _result = {{"error": str(_e)}}\n'
            f'_dispatch_ns["{output_as}"] = _result\n'
            f"result = _result\n"
        )
    else:
        code = (
            f"# Dispatch: call {tool} (retry={retry}, on_error={json.dumps(on_error)})\n"
            f"_max_retries = {retry}\n"
            f"_last_error = None\n"
            f"for _attempt in range(_max_retries):\n"
            f"    try:\n"
            f"        _result = {tool}()\n"
            f'        if isinstance(_result, dict) and _result.get("error"):\n'
            f'            _last_error = _result["error"]\n'
            f"            if _attempt < _max_retries - 1:\n"
            f"                continue\n"
            f"        break\n"
            f"    except Exception as _e:\n"
            f"        _last_error = str(_e)\n"
            f"        if _attempt < _max_retries - 1:\n"
            f"            continue\n"
            f'        _result = {{"error": str(_e)}}\n'
            f'_dispatch_ns["{output_as}"] = _result\n'
            f"result = _result\n"
        )

    # If on_error is specified, append fallback logic after the retry loop
    if on_error:
        on_error_json = json.dumps(on_error)
        code += (
            f"# on_error fallback chain\n"
            f'if isinstance(result, dict) and result.get("error"):\n'
            f"    _fallback_chain = json.loads({json.dumps(on_error_json)})\n"
            f'    _dispatch_ns["{output_as}_error"] = result.get("error")\n'
            f'    _dispatch_ns["{output_as}_fallback_chain"] = _fallback_chain\n'
            f'    result["fallback_chain"] = _fallback_chain\n'
            f'    result["on_error_triggered"] = True\n'
        )

    return Step(
        number=step_num,
        instruction=f"Call {tool} → {output_as}"
        + (f" (retry={retry})" if retry > 1 else ""),
        step_type="code",
        code=code,
    )


def _compile_run(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile a ``run`` dispatch entry into a code Step.

    Calls ``run_procedure`` to execute another procedure as a subprocess,
    then auto-parses ``final_output`` as JSON and merges the parsed fields
    into the result dict. This lets subsequent conditions dot-walk into
    the sub-procedure's output: ``{{ authority_check.ruling }}``.

    The result dict contains:
    - ``procedure``: the procedure name
    - ``overall_passed``: whether all steps passed
    - ``final_output``: the raw string output
    - ``parsed``: the JSON-parsed output (if valid JSON), merged as top-level keys
    - ``steps_executed``: number of steps that ran
    - ``error``: if the sub-procedure failed (not found, cycle, etc.)
    """
    procedure_name = entry.get("procedure", "")
    proc_args = entry.get("args", {})
    output_as = entry.get(
        "output_as",
        procedure_name.replace("-", "_").lower() if procedure_name else "sub_result",
    )

    proc_args_json = json.dumps(proc_args)

    code = (
        f"# Dispatch: run procedure {procedure_name}\n"
        f"_proc_name = {json.dumps(procedure_name)}\n"
        f"_proc_args = json.loads({json.dumps(proc_args_json)})\n"
        f"# Resolve template variables in string args (e.g. {{{{ intent }}}})\n"
        f"for _k, _v in _proc_args.items():\n"
        f"    if isinstance(_v, str):\n"
        f"        _proc_args[_k] = _resolve_template(_v, _dispatch_ns)\n"
        f"try:\n"
        f"    _raw = run_procedure(_proc_name, args=_proc_args)\n"
        f'    if isinstance(_raw, dict) and _raw.get("error"):\n'
        f'        _dispatch_ns["{output_as}"] = _raw\n'
        f"        result = _raw\n"
        f"    else:\n"
        f"        # run_procedure returns ExecutionResult dict: "
        f"{{procedure, overall_passed, final_output, ...}}\n"
        f'        _result = dict(_raw) if isinstance(_raw, dict) else {{"final_output": str(_raw)}}\n'
        f'        _fo = _result.get("final_output", "")\n'
        f"        # Try to parse final_output as JSON and merge fields\n"
        f"        _parsed = {{}}\n"
        f"        if isinstance(_fo, str) and _fo.strip():\n"
        f"            try:\n"
        f"                _parsed = json.loads(_fo.strip())\n"
        f"                if isinstance(_parsed, dict):\n"
        f'                    _result["parsed"] = _parsed\n'
        f"                    # Merge parsed fields so conditions can dot-walk\n"
        f"                    for _k, _v in _parsed.items():\n"
        f"                        if _k not in _result:\n"
        f"                            _result[_k] = _v\n"
        f"            except (json.JSONDecodeError, ValueError):\n"
        f'                _result["parsed"] = {{}}\n'
        f'        _dispatch_ns["{output_as}"] = _result\n'
        f"        result = _result\n"
        f"except Exception as _e:\n"
        f'    _err = {{"error": str(_e), "procedure": _proc_name}}\n'
        f'    _dispatch_ns["{output_as}"] = _err\n'
        f"    result = _err\n"
    )
    return Step(
        number=step_num,
        instruction=f"Run procedure {procedure_name} \u2192 {output_as}",
        step_type="code",
        code=code,
    )


def _compile_extract(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile an ``extract`` dispatch entry into a code Step.

    Generates Python that filters and maps data from a prior result.
    """
    from_path = entry.get("from", "")
    where = entry.get("where", {})
    fields = entry.get("fields", {})
    output_as = entry.get("output_as", "extracted")

    # Build the filter condition
    filter_code = ""
    if where:
        field = where.get("field", "")
        equals = where.get("equals", "")
        if field and equals is not None:
            filter_code = (
                f"    if _item.get({json.dumps(field)}) == {json.dumps(equals)}:\n"
            )

    # Build the field mapping
    map_lines = []
    if fields:
        for target, source in fields.items():
            map_lines.append(
                f'        {json.dumps(target)}: _item.get({json.dumps(source)}, "")'
            )
    map_block = ",\n".join(map_lines) if map_lines else ""

    # Resolve the source path (e.g. "gaps_data.gaps" → namespace["gaps_data"]["gaps"])
    parts = from_path.split(".")
    source_var = parts[0]
    source_path = "".join(f"[{json.dumps(p)}]" for p in parts[1:])

    code = (
        f"# Dispatch: extract from {from_path}\n"
        f"_source = _dispatch_ns.get({json.dumps(source_var)}, {{}})\n"
        f"_items = _source{source_path} if isinstance(_source, dict) else []\n"
        f"_items = _items if isinstance(_items, list) else []\n"
        f"_extracted = []\n"
        f"for _item in _items:\n"
    )
    if filter_code:
        code += filter_code
        code += "        _extracted.append({\n"
    else:
        code += "    _extracted.append({\n"
    if map_block:
        code += map_block + "\n"
    else:
        code += "        **_item\n"
    code += (
        "    })\n"
        f'_dispatch_ns["{output_as}"] = _extracted\n'
        f'result = {{"{output_as}": _extracted, '
        f'"count": len(_extracted)}}\n'
    )
    return Step(
        number=step_num,
        instruction=f"Extract from {from_path} → {output_as}",
        step_type="code",
        code=code,
    )


def _compile_dispatch(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile a ``dispatch`` entry into a code Step.

    Generates Python that looks up a value in a branch table and returns
    the matching procedure chain.
    """
    on_field = entry.get("on_field", "")
    branches = entry.get("branches", {})
    default = entry.get("default", [])
    output_as = entry.get("output_as", "chain")

    # Resolve the template variable in on_field
    on_var = _TEMPLATE_RE.search(on_field)
    on_var_name = on_var.group(1) if on_var else on_field.strip()

    branches_json = json.dumps(branches)
    default_json = json.dumps(default)

    code = (
        f"# Dispatch: route based on {on_var_name}\n"
        f'_key = _dispatch_ns.get({json.dumps(on_var_name)}, "").strip().lower()\n'
        f"_branches = json.loads({json.dumps(branches_json)})\n"
        f"_default = json.loads({json.dumps(default_json)})\n"
        f"_chain = _branches.get(_key, _default)\n"
        f'_dispatch_ns["{output_as}"] = _chain\n'
        f'result = {{"chain": _chain, "key": _key}}\n'
    )
    return Step(
        number=step_num,
        instruction=f"Dispatch on {on_var_name} → {output_as}",
        step_type="code",
        code=code,
    )


def _compile_condition(entry: dict, step_num: int, allowed_tools: list[str]) -> Step:
    """Compile a ``condition`` entry into a code Step.

    Generates Python that evaluates a condition and sets a branch target.
    The condition is a Jinja2-style expression like
    ``{{ dangling_links | length > 0 }}``.
    """
    if_expr = entry.get("if", "")
    then_chain = entry.get("then", [])
    else_chain = entry.get("else", [])
    output_as = entry.get("output_as", "chain")

    # Parse the full condition expression: {{ var.sub.field | filter op value }}
    # The }} comes at the very end, after the operator and value.
    # Pattern: {{ var.sub.field | filter op value }}
    # The variable name can be dotted (e.g. gaps_data.count, _prev.status).
    _FULL_COND_RE = re.compile(
        r"\{\{\s*(\w+(?:\.\w+)*)\s*(?:\|\s*(\w+)\s*)?\s*"
        r"(>|<|>=|<=|==|!=)\s*(\S+)\s*\}\}"
    )

    m = _FULL_COND_RE.search(if_expr)
    if m:
        var_path = m.group(1)  # e.g. "gaps_data.count" or "chain"
        filt = (m.group(2) or "").strip()
        op = m.group(3)
        val_raw = m.group(4).strip()
        # Re-quote string values so generated code uses proper Python
        # literals.  "USER_DIRECTIVE_WINS" (bare) would be a NameError;
        # '"USER_DIRECTIVE_WINS"' (json.dumps) is a valid string.
        if (val_raw.startswith("'") and val_raw.endswith("'")) or (
            val_raw.startswith('"') and val_raw.endswith('"')
        ):
            val = json.dumps(val_raw[1:-1])
        elif val_raw in ("True", "False", "None"):
            val = val_raw  # Python literal
        else:
            try:
                float(val_raw)  # number?
                val = val_raw
            except ValueError:
                # Not a number, not quoted, not a Python literal —
                # treat as a bare string and quote it so generated code
                # uses a proper Python string literal.
                val = json.dumps(val_raw)
    else:
        # Fallback: simple {{ var }} (truthy check)
        m2 = _TEMPLATE_RE.search(if_expr)
        var_path = m2.group(1) if m2 else "result"
        filt = "truthy"
        op = "=="
        val = "True"

    then_json = json.dumps(then_chain)
    else_json = json.dumps(else_chain)

    # Use _resolve_dotted for dotted paths, direct lookup for simple vars
    if "." in var_path:
        code = (
            f"# Dispatch: condition on {var_path}\n"
            f"_val = _resolve_dotted(_dispatch_ns, {json.dumps(var_path)})\n"
        )
    else:
        code = (
            f"# Dispatch: condition on {var_path}\n"
            f"_val = _dispatch_ns.get({json.dumps(var_path)}, None)\n"
        )
    if filt == "length":
        code += "_check = len(_val) if isinstance(_val, (list, dict, str)) else 0\n"
    elif filt == "truthy":
        code += "_check = bool(_val)\n"
    elif filt:
        # Unknown filter — treat as identity
        code += "_check = _val\n"
    else:
        code += "_check = _val\n"

    code += (
        f"_then = json.loads({json.dumps(then_json)})\n"
        f"_else = json.loads({json.dumps(else_json)})\n"
        f"_chain = _then if (_check {op} {val}) else _else\n"
        f'_dispatch_ns["{output_as}"] = _chain\n'
        f'result = {{"chain": _chain, "condition_met": _check {op} {val}}}\n'
    )
    return Step(
        number=step_num,
        instruction=f"Condition: {if_expr[:60]} → {output_as}",
        step_type="code",
        code=code,
    )


# Map dispatch entry types to their compilers.
_DISPATCH_COMPILERS = {
    "classify": _compile_classify,
    "call": _compile_call,
    "run": _compile_run,
    "extract": _compile_extract,
    "dispatch": _compile_dispatch,
    "condition": _compile_condition,
}


def _parse_dispatch_section(body: str, allowed_tools: list[str]) -> list[Step]:
    """Parse a ``## Dispatch`` YAML section into a SINGLE code Step.

    All dispatch entries are compiled into one Python script that runs in
    a single subprocess.  A shared ``namespace`` dict carries data between
    entries.  Template variables ``{{ var }}`` are resolved at runtime
    against this namespace.

    Returns a single-element list (one code step) or an empty list if
    there is no ``## Dispatch`` section, if PyYAML is not installed, or
    if the YAML is malformed.
    """
    if not _HAS_YAML:
        logger.debug("dispatch_skip: PyYAML not installed, skipping")
        return []

    m = _DISPATCH_HEADER_RE.search(body)
    if not m:
        return []

    # Extract the YAML block: from ## Dispatch to the next ## header
    dispatch_start = m.end()
    dispatch_text = body[dispatch_start:]

    # Find the next ## header (but not inside a code block)
    next_header = re.search(r"\n##\s+", dispatch_text)
    if next_header:
        dispatch_text = dispatch_text[: next_header.start()]

    dispatch_text = dispatch_text.strip()
    if not dispatch_text:
        return []

    try:
        entries = yaml.safe_load(dispatch_text)
    except yaml.YAMLError as e:
        logger.warning("dispatch_yaml_error: %s", e)
        return []

    if not isinstance(entries, list):
        logger.warning(
            "dispatch_not_list: expected a YAML list, got %s", type(entries).__name__
        )
        return []

    # Compile each entry into a Python snippet, then join them all into
    # one code block that runs sequentially in a single subprocess.
    snippets: list[str] = []
    snippets.append(
        "# === Dispatch pipeline (auto-generated from ## Dispatch YAML) ===\n"
        "import json\n"
        "import re as _re\n"
        "# _dispatch_ns is the shared data dict between dispatch entries.\n"
        "# It is NOT the same as the runtime exec namespace (which contains\n"
        "# tools like llm_generate, vault_gaps, etc.).\n"
        "# Seed it with args so template variables like {{ intent }} resolve.\n"
        "_dispatch_ns = dict(args) if isinstance(args, dict) else {}\n"
        "\n"
        '# Dotted field resolver: walks nested dicts for "gaps_data.count"\n'
        "def _resolve_dotted(ns, path):\n"
        '    """Resolve a dotted path like "gaps_data.count" against a dict.\n'
        "    Returns the value at the path, or None if any key is missing.\n"
        '    Handles dicts and lists (list index must be an integer)."""\n'
        '    parts = path.split(".")\n'
        "    val = ns\n"
        "    for part in parts:\n"
        "        if isinstance(val, dict):\n"
        "            val = val.get(part)\n"
        "            if val is None:\n"
        "                return None\n"
        "        elif isinstance(val, list):\n"
        "            try:\n"
        "                idx = int(part)\n"
        "                val = val[idx]\n"
        "            except (ValueError, IndexError):\n"
        "                return None\n"
        "        else:\n"
        "            return None\n"
        "    return val\n"
        "\n"
        "# _prev: always points to the last entry's result (convenience for\n"
        "# conditions that want to branch on the prior step's output without\n"
        "# knowing its output_as name).\n"
        '_dispatch_ns["_prev"] = None\n'
        "\n"
        "# Template resolver: {{ var }} and {{ var | filter }}\n"
        '_TEMPLATE_RE = _re.compile(r"\\{\\{\\s*(\\w+)\\s*\\}\\}")\n'
        "_TEMPLATE_FILTER_RE = _re.compile(\n"
        '    r"\\{\\{\\s*(\\w+)\\s*\\|\\s*(\\w+)\\s*\\}\\}")\n'
        "def _resolve_template(template, ns):\n"
        "    def _replacer(m):\n"
        '        v = ns.get(m.group(1), "")\n'
        "        if isinstance(v, (dict, list)):\n"
        "            v = json.dumps(v, default=str)\n"
        "        return str(v)\n"
        "    def _filter_replacer(m):\n"
        '        v = ns.get(m.group(1), "")\n'
        "        f = m.group(2).strip()\n"
        '        if f == "length":\n'
        "            if isinstance(v, (list, dict, str)):\n"
        "                return str(len(v))\n"
        '            return "0"\n'
        "        return str(v)\n"
        "    result = _TEMPLATE_FILTER_RE.sub(_filter_replacer, template)\n"
        "    result = _TEMPLATE_RE.sub(_replacer, result)\n"
        "    return result\n"
        "\n"
    )

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or len(entry) != 1:
            logger.warning("dispatch_entry_%d: expected single-key dict", i)
            continue

        entry_type = list(entry.keys())[0]
        entry_data = entry[entry_type] or {}

        compiler = _DISPATCH_COMPILERS.get(entry_type)
        if compiler is None:
            logger.warning("dispatch_unknown_type: %s", entry_type)
            continue

        try:
            step = compiler(entry_data, i + 1, allowed_tools)
            # Strip the '# Dispatch: ...' comment and use the raw code
            code = step.code or ""
            # Remove the comment line
            code = re.sub(r"^# Dispatch:.*\n", "", code, count=1)
            snippets.append(f"# --- Entry {i + 1}: {entry_type} ---")
            snippets.append(code)
            # Track _prev: after each entry, store its full result dict so
            # the next condition can reference {{ _prev.field }} without
            # knowing the entry's output_as name.
            snippets.append('_dispatch_ns["_prev"] = result')
        except Exception as e:
            logger.warning("dispatch_compile_error[%s]: %s", entry_type, e)
            continue

    if len(snippets) <= 1:
        return []  # only the preamble, no actual entries

    # Export the full dispatch namespace as the final result so subsequent
    # steps can access dispatch outputs via prior_results.
    snippets.append(
        "# --- Export dispatch namespace as result ---\nresult = dict(_dispatch_ns)\n"
    )

    combined_code = "\n".join(snippets)

    return [
        Step(
            number=1,
            instruction="Run dispatch pipeline (YAML DSL)",
            step_type="code",
            code=combined_code,
        )
    ]


# ── Public API ────────────────────────────────────────────────────────────


def compile_procedure(file_path: str) -> Procedure | None:
    """Compile a markdown procedure note from disk.

    Returns a :class:`Procedure` if the note has ``type: procedure`` or
    ``exemplar_procedure: true`` in its frontmatter, otherwise ``None``.
    """
    path = Path(file_path)
    if not path.exists():
        return None

    text = path.read_text(encoding="utf-8", errors="replace")
    proc = compile_from_text(path.stem, text)
    if proc is not None:
        proc.file_path = str(path)
    return proc


def compile_from_text(note_name: str, text: str) -> Procedure | None:
    """Compile a procedure from raw markdown text.

    Same as :func:`compile_procedure` but works on in-memory text
    (e.g., from FUSED retrieval results that carry note content).
    """
    fm, _fm_str, body = _parse_frontmatter(text)

    is_procedure = (
        str(fm.get("type", "")).lower() == "procedure"
        or str(fm.get("exemplar_procedure", "")).lower() == "true"
        or fm.get("exemplar_procedure") is True
    )
    if not is_procedure:
        return None

    # Parse allowed_tools (may be a list or absent) — needed BEFORE dispatch
    # parsing so dispatch compilers can validate tool references.
    allowed = fm.get("allowed_tools", [])
    if isinstance(allowed, str):
        allowed = [allowed]

    steps = _parse_steps(body)

    # Parse ## Dispatch section (YAML DSL for parent procedure orchestration).
    # Dispatch compiles to a SINGLE code step that runs the full pipeline in
    # one subprocess. It is PREPENDED — regular steps get shifted by 1.
    dispatch_steps = _parse_dispatch_section(body, allowed)
    if dispatch_steps:
        offset = len(dispatch_steps)
        for s in steps:
            s.number += offset
        steps = dispatch_steps + steps

    # Loud warning: the procedure has content but compiled 0 steps. This
    # is almost always a format mismatch (e.g. plain prose with no step
    # markers at all). Log it so the issue is visible without guessing.
    if not steps and body.strip():
        _h3_steps = len(re.findall(r"^###\s+Step", body, re.MULTILINE))
        _num_steps = len(re.findall(r"^\d+\.\s+", body, re.MULTILINE))
        logger.warning(
            "compile_zero_steps: procedure '%s' has body content but "
            "parsed 0 steps. Found %d ### Step headers and %d numbered "
            "list items. The compiler recognizes '### Step N:' headers "
            "and 'N.' numbered lists inside a ## Steps section — check "
            "that steps use one of these formats.",
            note_name,
            _h3_steps,
            _num_steps,
        )

    return Procedure(
        name=note_name,
        file_path="",  # no file when compiling from text
        version=fm.get("version", "1.0.0"),
        activation=fm.get("activation", "always"),
        spec_version=str(fm.get("spec_version", "1")),
        steps=steps,
        raw_text=text,
        frontmatter=fm,
        description=fm.get("description", ""),
        allowed_tools=allowed,
        model_cartridge=str(fm.get("model_cartridge", "big")).strip().lower(),
    )
