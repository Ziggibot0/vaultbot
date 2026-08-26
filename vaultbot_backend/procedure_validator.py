"""Procedure Validator — pre-publication validation for procedure notes.

This module catches the friction points that were discovered during the
Dream-Pass creation process (see [[Dream-Pass]] and the chat log
"Chat-have-a-look-at-the-WOLE-process-of-creating-the-dream-pass-procedure").

Checks (all aligned with the unified procedure format from
[[Procedural-Bootstrap-and-Evolution-Plan#Part 3 Procedural Note Schema
(UNIFIED — 2026-08-10)]]):

1. **Frontmatter** — type, description, when_to_use, allowed_tools,
   falsifiable_if, status, created, summary, tags; cartridge selection belongs
   to individual steps and is forbidden at procedure level
   (Friction: procedures without these can't be retrieved or executed)
2. **Naming convention** — title must not use "How to" prefix
   (Friction: "How to" names sound like tutorials, not tools. Procedures
   are machine-executable protocols, not advice to read.)
3. **Compile test** — procedure compiler parses steps correctly
   (Friction: steps missing ``### Step N:`` headers — every step needs a
   human-readable header so people who can't read code can reason about it)
4. **Tool consistency** — all tool calls in code are in allowed_tools
   (Friction: code calls vault_delete but it's not in allowed_tools)
5. **Anti-patterns** — run_tool(), direct endpoint usage
   (Friction: run_tool() doesn't exist in the runtime; socket/localhost
   instead of get_llm_client())
6. **Validation predicates** — deterministic (at_least/contains/matches)
   (Friction: "make sure it's good" is not testable)
7. **Idempotency indicators** — link_exists, dedup, skip-if patterns
   (Friction: duplicate links on re-run)
8. **Syntax check** — each code step is valid Python
   (Friction: syntax errors only discovered at runtime)

The dry-run function executes each code step in a subprocess with mocked
tools and a timeout, catching runtime errors before the procedure goes
live. No side effects — all tools return dummy data.

See:
  - [[Procedure-Creator]] — the meta-procedure that uses this
  - [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework
  - ``procedure_compiler.py`` — the compiler used for step parsing
  - ``step_gate_runtime.py`` — the runtime that executes procedures
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from typing import Any

from procedure_compiler import compile_from_text
from subprocess_utils import run as _subprocess_run
from subprocess_utils import scrubbed_env

# ── Safe functions (builtins + stdlib, not tools) ─────────────────────

_SAFE_FUNCS = frozenset(
    {
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "sum",
        "min",
        "max",
        "abs",
        "round",
        "isinstance",
        "issubclass",
        "type",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "read",
        "write",
        "close",
        "json",
        "os",
        "re",
        "sys",
        "pathlib",
        "Path",
        "datetime",
        "date",
        "time",
        "textwrap",
        "collections",
        "itertools",
        "functools",
        "hashlib",
        "math",
        "statistics",
        "copy",
        "traceback",
        "logging",
        "subprocess",
        "import",
        "from",
        "def",
        "class",
        "if",
        "else",
        "elif",
        "for",
        "while",
        "try",
        "except",
        "finally",
        "with",
        "as",
        "return",
        "yield",
        "lambda",
        "None",
        "True",
        "False",
        "and",
        "or",
        "not",
        "in",
        "is",
        "pass",
        "break",
        "continue",
        "raise",
        "assert",
        "del",
        "global",
        "nonlocal",
        "self",
        "cls",
        "property",
        "staticmethod",
        "classmethod",
        "super",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "RuntimeError",
        "FileNotFoundError",
        "StopIteration",
        "ZeroDivisionError",
        "OverflowError",
        "ImportError",
        "ModuleNotFoundError",
        "NameError",
        "IndexError",
        "NotImplementedError",
        "Warning",
        "OSError",
        "PermissionError",
        "TimeoutError",
        "ConnectionError",
        "IOError",
        "LookupError",
        "ArithmeticError",
        "MemoryError",
        "RecursionError",
        "UnicodeError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "iter",
        "next",
        "format",
        "vars",
        "dir",
        "repr",
        "id",
        "hex",
        "oct",
        "bin",
        "chr",
        "ord",
        "ascii",
        "input",
        "exec",
        "eval",
        "compile",
        "globals",
        "locals",
        "callable",
        "complex",
        "bytes",
        "bytearray",
        "memoryview",
        "frozenset",
        "object",
        "help",
        "divmod",
        "pow",
        "all",
        "any",
        "slice",
    }
)

# Known tool names that the step-gate runtime can inject into code steps
# (the authoritative list is step_gate_runtime._build_tool_preamble — this
# frozenset MUST stay in sync with it). textbook_read_page and
# textbook_ingest are custom_tools exposed to the LLM directly, NOT
# injected into code-step subprocesses, so they are intentionally absent.
_KNOWN_TOOLS = frozenset(
    {
        "vault_search",
        "vault_list",
        "vault_append",
        "vault_delete",
        "vault_lint",
        "vault_graph_analyzer",
        "code_read",
        "llm_generate",
        "web_read_source",
        "run_procedure",
        "vault_safe_write",
        "vault_gaps",
        "machine_spec",
        "ollama_model_search",
        "vaultbot_status",
    }
)

# Idempotency indicator keywords
_IDEMPOTENCY_KEYWORDS = [
    "idempotent",
    "link_exists",
    "dedup",
    "already_exists",
    "skip if",
    "if not",
    "check before",
    "exists_check",
]

# Direct endpoint patterns (anti-pattern: should use get_llm_client)
_ENDPOINT_PATTERNS = [
    (r"socket\.", "socket"),
    (r"localhost", "localhost"),
    (r"127\.0\.0\.1", "127.0.0.1"),
    (r"urllib\.request", "urllib.request"),
    (r"requests\.(get|post|put|delete)", "requests"),
]


# ── Frontmatter parser (same logic as procedure_compiler) ─────────────


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter into a dict (flat key-value + lists)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_str = text[3:end].strip()
    fm: dict = {}
    current_key: str | None = None
    current_list: list | None = None
    for line in fm_str.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_key:
            value = line[4:].strip().strip('"').strip("'")
            if current_list is None:
                current_list = []
                fm[current_key] = current_list
            current_list.append(value)
            continue
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
                current_key = key
                current_list = None
    return fm


# ── Public API ───────────────────────────────────────────────────────


def validate_procedure_text(
    text: str,
    run_procedure_index: dict[str, Any] | None = None,
) -> dict:
    """Static validation of a procedure note's text.

    Returns a dict with:
    - ``passed``: bool — True if all ERROR-level checks pass
    - ``errors``: list[str] — must fix before publishing
    - ``warnings``: list[str] — should consider
    - ``checks_run``: list[str] — names of checks executed
    - ``compiled_steps``: int — steps the compiler found
    - ``step_types``: list[str] — type of each step
    - ``step_numbers``: list[int] — number of each step
    - ``allowed_tools``: list[str] — from frontmatter
    - ``tool_calls_found``: list[str] — tools called in code

    Args:
        text: The full markdown text of the procedure note.
        run_procedure_index: Optional stem -> {path, frontmatter} map from
            ``procedure_tracker.get_procedure_index()``.  When provided, the
            ``provides`` check verifies that every named sub-procedure
            actually exists in the vault.
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks_run: list[str] = []

    # --- 1. Frontmatter ---
    checks_run.append("frontmatter_exists")
    if not text.startswith("---"):
        errors.append("Missing frontmatter (must start with ---)")
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "checks_run": checks_run,
        }

    fm_end = text.find("\n---", 3)
    if fm_end == -1:
        errors.append("Frontmatter not closed (missing closing ---)")
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "checks_run": checks_run,
        }

    fm = _parse_frontmatter(text)

    checks_run.append("type_procedure")
    if fm.get("type", "").lower() != "procedure":
        errors.append(
            f"Frontmatter 'type' must be 'procedure', got '{fm.get('type', 'MISSING')}'"
        )

    checks_run.append("description_exists")
    if not fm.get("description"):
        errors.append(
            "Frontmatter missing 'description' (one-line summary for retrieval)"
        )

    checks_run.append("when_to_use_exists")
    if not fm.get("when_to_use") and not fm.get("when"):
        errors.append(
            "Frontmatter missing 'when_to_use' (or 'when') "
            "(the procedure is embedded by its description + when-to-use "
            "surface, NOT its body — without this, the procedure can't be "
            "discovered by intent)"
        )

    checks_run.append("allowed_tools_exists")
    allowed_tools = fm.get("allowed_tools", [])
    if isinstance(allowed_tools, str):
        allowed_tools = [allowed_tools]
    if not allowed_tools:
        errors.append(
            "Frontmatter missing 'allowed_tools' "
            "(permission scope for subprocess execution)"
        )

    checks_run.append("falsifiable_if_exists")
    if not fm.get("falsifiable_if"):
        errors.append(
            "Frontmatter missing 'falsifiable_if' "
            "(condition that would prove this procedure wrong)"
        )

    checks_run.append("status_exists")
    if not fm.get("status"):
        errors.append(
            "Frontmatter missing 'status' "
            "(must be one of: experimental, active, verified, archived)"
        )

    checks_run.append("model_cartridge_absent")
    if "model_cartridge" in fm:
        errors.append(
            "Frontmatter must not declare 'model_cartridge' "
            "(select the cartridge on each LLM step)"
        )

    checks_run.append("created_exists")
    if not fm.get("created"):
        errors.append("Frontmatter missing 'created' (date in YYYY-MM-DD format)")

    checks_run.append("summary_exists")
    if not fm.get("summary"):
        errors.append("Frontmatter missing 'summary' (short title for the procedure)")

    checks_run.append("tags_exists")
    if not fm.get("tags"):
        errors.append(
            "Frontmatter missing 'tags' (at minimum: [procedure, procedures])"
        )

    # --- 1c. provides field (optional composition declaration) ---
    # When a procedure composes sub-procedures, a ``provides:`` list lets
    # the surface renderer show the full capability set in one glance
    # (one level deep) without the model reading each child.  We warn
    # (not error) when provides is present but not a list, and warn when
    # any named sub-procedure isn't in the index (the caller passes it).
    checks_run.append("provides_shape")
    provides = fm.get("provides")
    if provides is not None:
        if isinstance(provides, str):
            # Inline single value — normalize for downstream consumers.
            provides = [provides] if provides.strip() else []
        if not isinstance(provides, list):
            errors.append(
                "Frontmatter 'provides' must be a YAML list "
                "(e.g. 'provides:\\n  - Dream-Scan') "
                f"or a single name, got {type(provides).__name__}"
            )
        elif provides and run_procedure_index is not None:
            checks_run.append("provides_names_exist")
            for name in provides:
                name_s = str(name).strip().strip('"').strip("'")
                if name_s and name_s not in run_procedure_index:
                    warnings.append(
                        f"provides references '{name_s}' but no "
                        f"procedure with that stem exists in the vault"
                    )
        elif provides:
            checks_run.append("provides_names_exist")
            # No index provided — can't validate names, just note it.
            warnings.append(
                "provides list present but no procedure index supplied "
                "— cannot verify that sub-procedure names exist"
            )

    # --- 1b. Naming convention (procedures are tools, not tutorials) ---
    checks_run.append("naming_convention")
    body_start = text.find("\n---", 3)
    body = text[body_start + 4 :].lstrip() if body_start != -1 else text
    title_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
    if title_match:
        proc_title = title_match.group(1).strip()
        if re.match(r"^how[\s-]+to", proc_title, re.IGNORECASE):
            errors.append(
                f"Procedure title '{proc_title}' uses 'How to' prefix — "
                f"procedures are tools, not tutorials. Use action-oriented "
                f"names like 'Dream-Pass', 'Verify-Claims', "
                f"'Procedure-Creator'."
            )
    else:
        warnings.append("No title heading found — cannot check naming convention")

    # --- 1d. Provenance (issue #64) ---
    # A procedure that cites no source, declares no dependency, and links to
    # no vault document is an unverifiable assertion. Warn when a procedure
    # has none of: sources:, depends_on:/research_sources:, or a
    # ``## Related`` section containing at least one wikilink.
    checks_run.append("provenance")
    has_sources = bool(fm.get("sources"))
    has_depends = bool(fm.get("depends_on") or fm.get("research_sources"))
    related_match = re.search(
        r"^##\s+Related\b.*?(?=^##\s|\Z)", body, re.MULTILINE | re.DOTALL
    )
    has_related_wikilink = bool(
        related_match and re.search(r"\[\[[^\]]+\]\]", related_match.group(0))
    )
    if not (has_sources or has_depends or has_related_wikilink):
        warnings.append(
            "No provenance — add at least one of: 'sources:' frontmatter, "
            "'depends_on:'/'research_sources:' frontmatter, or a "
            "'## Related' section with wikilinks (issue #64)"
        )

    # --- 1e. Rationale (issue #63) ---
    # A procedure that states *what* to do but never *why* is a black box.
    # Warn when there is no ``## Why This Exists`` section (or equivalent
    # rationale header) explaining the failure/gap that spawned it.
    checks_run.append("rationale")
    has_rationale = re.search(
        r"^##\s+Why This Exists\b", body, re.MULTILINE | re.IGNORECASE
    )
    if not has_rationale:
        warnings.append(
            "No '## Why This Exists' section — add the failure/gap that "
            "spawned this procedure and its key design tradeoffs (issue #63)"
        )

    # --- 2. Compile test (compiler is source of truth) ---
    checks_run.append("compile_test")
    proc = compile_from_text("draft", text)
    if proc is None:
        errors.append(
            "Procedure compiler returned None — check that "
            "'type: procedure' is in frontmatter"
        )
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "checks_run": checks_run,
        }

    compiled_steps = len(proc.steps)
    step_types = [s.step_type for s in proc.steps]
    # Keep raw float numbers — decimal steps (1.5, 2.5) are explicitly
    # allowed for inserting steps between existing ones without renumbering.
    # Truncating to int here would collapse 1.5 -> 1 and produce false
    # "duplicate step number" errors (see Dream-Pass, which uses 1.5/2.5/...).
    step_numbers = [s.number for s in proc.steps]

    if compiled_steps == 0:
        errors.append(
            "Compiler found 0 steps — every step needs a "
            "'### Step N: short-summary' header followed by a "
            "```python fence or [llm: ...] tag inside a ## Steps section"
        )
    else:
        checks_run.append("step_instruction_present")
        # Every step must carry a human-readable instruction header
        # (the "### Step N: short-summary" line). A step with an empty
        # instruction is opaque to a non-programmer — they can't tell
        # what it does. This is the enforcement of issue #62.
        headerless = [s.number for s in proc.steps if not s.instruction.strip()]
        if headerless:
            errors.append(
                f"Steps {headerless} have no human-readable instruction "
                "header — add a '### Step N: short-summary' line above "
                "each bare ```python fence or [llm: ...] tag"
            )

        checks_run.append("sequential_numbering")
        # Only check sequential for integer steps — decimal steps
        # (e.g. 1.5, 2.5) are explicitly allowed for inserting steps
        # between existing ones without renumbering.
        if all(
            isinstance(n, int) or (isinstance(n, float) and n == int(n))
            for n in step_numbers
        ):
            expected = list(
                range(int(step_numbers[0]), int(step_numbers[0]) + len(step_numbers))
            )
            if [int(n) for n in step_numbers] != expected:
                errors.append(
                    f"Step numbers not sequential: found {step_numbers}, "
                    f"expected {expected}"
                )
        if len(step_numbers) != len(set(step_numbers)):
            errors.append(f"Duplicate step numbers: {step_numbers}")

    # --- 3. Tool consistency ---
    code_blocks = re.findall(r"```python\n(.*?)```", text, re.DOTALL)
    tool_calls_found: set[str] = set()

    if code_blocks and allowed_tools:
        checks_run.append("tool_calls_in_allowed_tools")
        for code_block in code_blocks:
            calls = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code_block)
            for call in calls:
                if call in _SAFE_FUNCS:
                    continue
                if call in _KNOWN_TOOLS:
                    tool_calls_found.add(call)
                    if call not in allowed_tools:
                        errors.append(
                            f"Code calls '{call}()' but it's not in "
                            f"allowed_tools: {allowed_tools}"
                        )
                if call == "run_tool":
                    errors.append(
                        "Uses run_tool() — runtime injects tools by "
                        "name, not via run_tool()"
                    )

        checks_run.append("no_direct_endpoints")
        for i, code_block in enumerate(code_blocks):
            for pattern, name in _ENDPOINT_PATTERNS:
                if (
                    re.search(pattern, code_block)
                    and "get_llm_client" not in code_block
                ):
                    warnings.append(
                        f"Code block {i + 1} uses direct endpoint "
                        f"({name}) — should use get_llm_client() "
                        f"instead"
                    )

    # --- 4. Validation predicates ---
    checks_run.append("validation_predicates")
    validations = re.findall(r"\[validate:\s*(.+?)\]", text, re.IGNORECASE)
    for v in validations:
        v_stripped = v.strip()
        is_deterministic = (
            re.match(r"at_least\s+\d+", v_stripped, re.IGNORECASE)
            or re.match(r'contains\s+["\']', v_stripped, re.IGNORECASE)
            or re.match(r"matches\s+/", v_stripped, re.IGNORECASE)
            or re.match(r"islands_after\s*[<>=]", v_stripped, re.IGNORECASE)
            or re.match(r"connectivity_after\s*[<>=]", v_stripped, re.IGNORECASE)
        )
        if not is_deterministic:
            warnings.append(
                f"Non-deterministic validation: '{v_stripped}' — "
                f"consider using at_least/contains/matches"
            )

    # --- 5. Idempotency indicators ---
    checks_run.append("idempotency_indicators")
    has_idempotency = any(kw in text.lower() for kw in _IDEMPOTENCY_KEYWORDS)
    if not has_idempotency:
        warnings.append(
            "No idempotency indicators found — procedure may create "
            "duplicates on re-run"
        )

    # --- 6. Result variable check ---
    checks_run.append("result_variable")
    code_steps = [s for s in proc.steps if s.step_type == "code"]
    for step in code_steps:
        if step.code and (
            "result = " not in step.code
            and "result=" not in step.code
            and "vault_delete" not in step.code
            and "vault_append" not in step.code
        ):
            warnings.append(
                f"Step {step.number} (code) has no "
                f"'result = ' assignment — runtime expects "
                f"'result' in namespace"
            )

    # --- 7. Syntax check ---
    checks_run.append("syntax_check")
    for step in code_steps:
        if step.code:
            try:
                compile(step.code, f"<step_{step.number}>", "exec")
            except SyntaxError as e:
                errors.append(f"Step {step.number} syntax error: {e}")

    passed = len(errors) == 0
    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "checks_run": checks_run,
        "compiled_steps": compiled_steps,
        "step_types": step_types,
        "step_numbers": step_numbers,
        "allowed_tools": allowed_tools,
        "tool_calls_found": list(tool_calls_found),
    }


def dry_run_procedure(text: str, vault_path: str = ".", timeout: int = 10) -> dict:
    """Execute each code step in a subprocess with mocked tools.

    All tools are mocked — no side effects on the vault.
    Uses subprocess with per-step timeout to prevent hangs.

    Returns:
    - ``passed``: bool — True if all code steps executed without error
    - ``steps_tested``: int — number of code steps tested
    - ``results``: list[dict] — per-step results
    """
    proc = compile_from_text("draft", text)
    if proc is None:
        return {"passed": False, "error": "Could not compile procedure"}

    results: list[dict] = []
    prior_results: list = []
    all_passed = True

    mock_defs = (
        "import json, os, re, sys\n"
        "from pathlib import Path\n"
        "\n"
        "vault_path = os.environ.get('VAULT_PATH', '.')\n"
        "prior_results = json.loads(os.environ.get('PRIOR_RESULTS', '[]'))\n"
        '_IGNORED_DIRS = {".git", ".obsidian", ".venv", "vaultbot_venv", '
        '"vaultbot_index", "sessions", "partials", "__pycache__"}\n'
        "\n"
        "# Mock tools\n"
        "def vault_search(*a, **kw): return [{'filename': 'mock.md', "
        "'score': 0.9, 'content': 'mock'}]\n"
        "def vault_list(*a, **kw): return ['mock1.md', 'mock2.md']\n"
        "def vault_append(*a, **kw): return {'appended': True, "
        "'chars_added': 0}\n"
        "def vault_delete(*a, **kw): return {'deleted': True, "
        "'backed_up': True}\n"
        "def vault_lint(*a, **kw): return {'has_frontmatter': True, "
        "'broken_wikilinks': [], 'issues': []}\n"
        "def vault_graph_analyzer(*a, **kw): return {'status': "
        "'success', 'analysis': {'num_islands': 2, 'isolated_nodes': [], "
        "'connectivity_ratio': 0.99, 'largest_island_size': 100, "
        "'total_nodes': 100, 'total_edges': 200}}\n"
        "def code_read(*a, **kw): return 'mock file content'\n"
        "def llm_generate(*a, **kw): return 'MOCK LLM OUTPUT'\n"
        "def web_read_source(*a, **kw): return 'mock web content'\n"
        "def run_procedure(*a, **kw): return {'overall_passed': True, "
        "'steps_executed': 0}\n"
        "def textbook_read_page(*a, **kw): return 'mock textbook page'\n"
        "def textbook_ingest(*a, **kw): return {'status': 'success', "
        "'sections': 0}\n"
    )

    for step in proc.steps:
        if step.step_type != "code":
            results.append(
                {
                    "step": step.number,
                    "type": step.step_type,
                    "status": "skipped",
                }
            )
            continue

        if not step.code:
            results.append(
                {
                    "step": step.number,
                    "type": "code",
                    "status": "error",
                    "error": "no code in step",
                }
            )
            all_passed = False
            continue

        # Build sandbox script
        script = mock_defs + "\n" + textwrap.dedent(step.code) + "\n"
        script += (
            "\nimport json as _json\n"
            "try:\n"
            "    print(_json.dumps({'status': 'ok', 'result': "
            "result if 'result' in dir() else ''}))\n"
            "except:\n"
            "    print(_json.dumps({'status': 'ok', 'result': "
            "str(result) if 'result' in dir() else ''}))\n"
        )

        # Scrubbed env: dry-run procedure code is still LLM-authored and must
        # not see API keys/tokens/passwords from the parent process.
        env = {
            **scrubbed_env(),
            "VAULT_PATH": vault_path,
            "PRIOR_RESULTS": json.dumps(prior_results, default=str),
        }

        try:
            r = _subprocess_run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=vault_path,
                env=env,
            )
            if r.returncode != 0:
                results.append(
                    {
                        "step": step.number,
                        "type": "code",
                        "status": "error",
                        "error": (r.stderr[:300] if r.stderr else "non-zero exit"),
                    }
                )
                all_passed = False
            else:
                stdout = r.stdout.strip()
                if stdout:
                    try:
                        parsed = json.loads(stdout)
                        result_val = parsed.get("result", "")
                        if not isinstance(result_val, str):
                            result_val = json.dumps(result_val, default=str)
                        prior_results.append(result_val)
                    except json.JSONDecodeError:
                        prior_results.append(stdout[:500])
                results.append(
                    {
                        "step": step.number,
                        "type": "code",
                        "status": "passed",
                    }
                )
        except subprocess.TimeoutExpired:
            results.append(
                {
                    "step": step.number,
                    "type": "code",
                    "status": "timeout",
                    "timeout": timeout,
                }
            )
            all_passed = False
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            results.append(
                {
                    "step": step.number,
                    "type": "code",
                    "status": "error",
                    "error": str(e)[:300],
                }
            )
            all_passed = False

    return {
        "passed": all_passed,
        "steps_tested": len([r for r in results if r["status"] != "skipped"]),
        "results": results,
    }
