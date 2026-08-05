"""Step-Gate Runtime — execute compiled procedures step by step.

This is the "execute" half of the compile-then-execute pattern (see
[[Procedural-Bootstrap-and-Evolution-Plan]] and
[[Procedure-Subprocess-Architecture]]).

Three step types are supported:

1. **Code steps** (v2, ``step_type == "code"``): Python code blocks that
   run in a subprocess with scoped tool injection. Zero LLM cost. The
   subprocess receives prior step results as an environment variable and
   returns its result as JSON on stdout. Loud failures include the full
   traceback.

2. **LLM steps** (v2, ``step_type == "llm"``): ``[llm:]`` tags that
   compile to a stripped-down LLM call via ``get_llm_client()``. Minimal
   context — only prior step results + the instruction, not VaultBot's
   full system prompt + vault context. The procedure-bot is NOT
   VaultBot.

3. **Text steps** (v1, ``step_type == "text"``): Backward-compatible
   with existing v1 procedures. Uses the active-frame approach (current
   step first, full procedure overview, vault context) with the main LLM
   client. Based on checkpointing from "Attention Deficits" (arXiv
   2602.19239) and the full-program cursor from "Compile, Then Page"
   (arXiv 2607.11346).

Tool injection for code steps is driven by the procedure's
``allowed_tools`` frontmatter field. Only the listed tools are injected
into the subprocess namespace. This is the permission scope — a
procedure that verifies claims gets ``vault_search`` and
``llm_generate``, not ``safe_write`` or ``vault_delete``.

See:
  - ``procedure_compiler.py`` — the compile half
  - ``procedure_tracker.py`` — pass/fail logging (step-level via
    ``log_step_result``)
  - [[Procedural-Bootstrap-and-Evolution-Plan]]
  - [[Procedure-Subprocess-Architecture]]
  - [[Deterministic-Scaffolding-for-Small-Models]]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from subprocess_utils import run as _subprocess_run, scrubbed_env, preexec_fn
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from procedure_compiler import Procedure, Step


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Outcome of executing a single step.

    Attributes:
        step_number: Which step was executed.
        step_type: "code", "llm", or "text".
        passed: Whether the output passed validation.
        output: The step's output (LLM text, code result, or text output).
        validation_error: If validation failed, what was missing.
            None if passed or no validation criteria.
        error: If the step crashed (code error, LLM error), the error
            message. None if the step succeeded.
        traceback: Full traceback if the step crashed. None on success.
    """
    step_number: int
    step_type: str
    passed: bool
    output: str
    validation_error: str | None = None
    error: str | None = None
    traceback: str | None = None


@dataclass
class ExecutionResult:
    """Outcome of executing an entire procedure.

    Attributes:
        procedure_name: Name of the procedure that was executed.
        steps: Per-step results, in execution order.
        overall_passed: True if every step passed validation.
        final_output: Concatenation of all step outputs (the
            complete answer).
        failed_step: Step number that caused the procedure to stop,
            or None if all steps completed.
    """
    procedure_name: str
    steps: list[StepResult]
    overall_passed: bool
    final_output: str
    failed_step: int | None = None
    child_procedures: list[dict] = field(default_factory=list)
        # Each entry: {"name": str, "overall_passed": bool,
        # "steps_executed": int}. Populated when a step invokes
        # ``run_procedure`` to run another procedure recursively.


# ── Stop words for validation (text steps) ──────────────────────────────

_STOP_WORDS = frozenset({
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
    'could', 'may', 'might', 'must', 'can', 'shall', 'to', 'of', 'in',
    'on', 'at', 'by', 'for', 'with', 'about', 'as', 'into', 'through',
    'during', 'before', 'after', 'above', 'below', 'from', 'up', 'down',
    'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once',
    'and', 'or', 'but', 'if', 'than', 'that', 'this', 'these', 'those',
    'it', 'its', 'your', 'our', 'their', 'his', 'her', 'my', 'me', 'you',
    'he', 'she', 'they', 'we', 'them', 'us', 'i', 'him',
    'output', 'contain', 'include', 'mention',
    'least', 'more', 'most', 'some', 'any', 'all', 'each', 'every',
    'not', 'no', 'nor', 'so', 'too', 'very', 'just', 'only', 'also',
    'what', 'which', 'who', 'whom', 'whose', 'when', 'where', 'why', 'how',
    'own', 'words',
})


# ── Validation (for text steps) ──────────────────────────────────────────

# Structured validation predicates (Phase 4).  Three opt-in forms are
# recognised; free-text validation falls back to the word-overlap
# heuristic below.  See [[Procedure-Subprocess-Architecture]].
#
#   at_least N <unit>     count <unit> in output, compare >= N
#   contains "literal"   substring check
#   matches /regex/      regex search
#
# Units mirror ``_count_thing`` in the condition evaluator: notes/titles
# (wikilinks), sources/urls/links (http(s)), items/lines (bullets), or
# generic tokens for anything else.
_AT_LEAST_RE = re.compile(
    r'at_least\s+(\d+)\s*(?P<unit>\w+)?', re.IGNORECASE)
_VCONTAINS_RE = re.compile(
    r'contains\s+(?P<q>["\'])(?P<lit>.*?)(?P=q)', re.IGNORECASE)
_VMATCHES_RE = re.compile(
    r'matches\s+/(?P<pattern>.*)/', re.IGNORECASE)


def _parse_validation(text: str) -> dict | None:
    """Parse a validation string into a structured predicate, or None.

    Returns one of:
    - {"form": "at_least", "n": int, "unit": str|None}
    - {"form": "contains", "literal": str}
    - {"form": "matches", "pattern": str}
    - None (free-text → use the word-overlap fallback)
    """
    t = text.strip()
    m = _AT_LEAST_RE.search(t)
    if m:
        return {"form": "at_least", "n": int(m.group(1)),
                "unit": m.group("unit")}
    m = _VCONTAINS_RE.search(t)
    if m:
        return {"form": "contains", "literal": m.group("lit")}
    m = _VMATCHES_RE.search(t)
    if m:
        return {"form": "matches", "pattern": m.group("pattern")}
    return None


def _validate_word_overlap(output: str, validation: str | None) -> tuple[bool, str | None]:
    """Deterministic validation using word-overlap heuristic.

    Extracts content words from the validation criteria (filtering
    stop words) and checks what fraction appear in the output.
    Passes if >= 50% of content words are found (case-insensitive).
    If no content words can be extracted, always passes.
    """
    if validation is None:
        return True, None

    words = re.findall(r'[a-zA-Z]+', validation.lower())
    content_words = [w for w in words if w not in _STOP_WORDS and len(w) > 1]

    if not content_words:
        return True, None

    output_lower = output.lower()
    found = sum(1 for w in content_words if w in output_lower)
    coverage = found / len(content_words)

    if coverage >= 0.5:
        return True, None

    missing = [w for w in content_words if w not in output_lower]
    return False, f"Validation terms not found in output: {', '.join(missing[:5])}"


def _validate_structured(output: str, validation: str) -> tuple[bool, str | None]:
    """Run a structured validation predicate. Returns (passed, error).

    Falls back to word-overlap if the string can't be parsed into a
    known form — backward-compatible with existing free-text validation.
    """
    pred = _parse_validation(validation)
    if pred is None:
        return _validate_word_overlap(output, validation)

    form = pred["form"]
    if form == "at_least":
        got = _count_thing(output, pred.get("unit") or "")
        if got >= pred["n"]:
            return True, None
        return False, f"at_least {pred['n']} {pred.get('unit') or ''}: found {got}".strip()
    if form == "contains":
        lit = pred["literal"]
        if lit in output:
            return True, None
        return False, f"contains {lit!r}: not found"
    if form == "matches":
        try:
            if re.search(pred["pattern"], output):
                return True, None
            return False, f"matches /{pred['pattern']}/: no match"
        except re.error as e:
            return False, f"matches: invalid regex /{pred['pattern']}/: {e}"
    return _validate_word_overlap(output, validation)


def _validate_step(output: str, validation: str | None) -> tuple[bool, str | None]:
    """Dispatch validation: structured predicates first, word-overlap fallback."""
    if validation is None:
        return True, None
    return _validate_structured(output, validation)


# ── Tool registry for code steps ─────────────────────────────────────────

_IGNORED_DIRS = {
    ".git", ".obsidian", ".venv", "vaultbot_venv", "vaultbot_index",
    "sessions", "partials", "__pycache__",
}


def _build_tool_preamble(allowed_tools: list[str]) -> str:
    """Build the Python code that injects allowed tools into the namespace.

    This code runs in the subprocess before the step code. It imports
    backend modules and creates wrapper functions that the step code
    can call directly.
    """
    snippets: list[str] = []

    # --- Universal context variables (always injected) ---
    # ``args``: the call-time tool arguments the model passed to
    #   execute_procedure (minus procedure_name). Historically many
    #   procedure notes referenced ``args.get(...)`` but the subprocess
    #   never defined it, so those steps crashed with NameError. Inject
    #   it unconditionally (empty dict when no args were supplied).
    # ``output``: alias for the previous step's output (== prior_results[-1]
    #   or "" when empty). Several procedures (Extract-Claims, Judge-Plan,
    #   Summarize-Conversation, Refine-Concept-Card, ...) reference a bare
    #   ``output`` after an [llm:] step to post-process the model's text.
    snippets.append(
        'args = json.loads(os.environ.get("PROCEDURE_ARGS", "{}"))\n'
        'if not isinstance(args, dict):\n'
        '    args = {}\n'
        'namespace["args"] = args\n'
        'output = prior_results[-1] if prior_results else ""\n'
        'if not isinstance(output, str):\n'
        '    try:\n'
        '        output = json.dumps(output, default=str)\n'
        '    except Exception:\n'
        '        output = str(output)\n'
        'namespace["output"] = output\n'
    )

    if "llm_generate" in allowed_tools:
        snippets.append(
            'if "llm_generate" in allowed:\n'
            '    from llm_client import get_llm_client, get_small_client, get_vision_client\n'
            '    _cartridge = os.environ.get("PROCEDURE_MODEL_CARTRIDGE", "big")\n'
            '    if _cartridge == "small":\n'
            '        _client = get_small_client() or get_llm_client()\n'
            '    elif _cartridge == "vision":\n'
            '        _client = get_vision_client() or get_llm_client()\n'
            '    else:\n'
            '        _client = get_llm_client()\n'
            '    # Small-cartridge procedures are bounded tasks (rerank, filter,\n'
            '    # summarize) — disable reasoning so a 0.8b model does not spend\n'
            '    # 60s thinking on a one-line judgment. Big-cartridge procedures\n'
            '    # keep reasoning (synthesis needs it).\n'
            '    _think = False if _cartridge == "small" else None\n'
            '    def llm_generate(prompt, system="You are a procedure executor. Follow the instruction. Output only the result."):\n'
            '        result = _client.generate(prompt=prompt, system=system, stream=False, think=_think)\n'
            '        return result.get("response", "")\n'
            '    namespace["llm_generate"] = llm_generate\n'
        )

    if "vault_search" in allowed_tools:
        snippets.append(
            'if "vault_search" in allowed:\n'
            '    def vault_search(query, k=5):\n'
            '        vault = Path(vault_path)\n'
            '        query_terms = [t.lower() for t in query.split() if len(t) > 2]\n'
            '        results = []\n'
            '        for root, dirs, files in os.walk(str(vault)):\n'
            '            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]\n'
            '            for f in files:\n'
            '                if not f.endswith(".md"):\n'
            '                    continue\n'
            '                try:\n'
            '                    text = Path(root, f).read_text(encoding="utf-8", errors="replace")\n'
            '                    text_lower = text.lower()\n'
            '                    matches = sum(1 for t in query_terms if t in text_lower)\n'
            '                    if matches > 0:\n'
            '                        results.append({"file_path": str(Path(root, f)), "name": f[:-3], "score": matches / max(len(query_terms), 1)})\n'
            '                except Exception:  # noqa: BLE001 — best-effort, returns error to caller\n'
            '                    continue\n'
            '        results.sort(key=lambda r: r["score"], reverse=True)\n'
            '        return results[:k]\n'
            '    namespace["vault_search"] = vault_search\n'
        )

    if "web_read_source" in allowed_tools:
        snippets.append(
            'if "web_read_source" in allowed:\n'
            '    def web_read_source(url=None, file=None):\n'
            '        web_dir = Path(vault_path) / "vaultbot_stuff/learningMaterial" / "web"\n'
            '        if file:\n'
            '            p = web_dir / file\n'
            '        elif url:\n'
            '            import hashlib\n'
            '            h = hashlib.md5(url.encode()).hexdigest()[:8]\n'
            '            candidates = list(web_dir.glob(f"*{h}*"))\n'
            '            p = candidates[0] if candidates else None\n'
            '        else:\n'
            '            return None\n'
            '        if p and p.exists():\n'
            '            return p.read_text(encoding="utf-8", errors="replace")\n'
            '        return None\n'
            '    namespace["web_read_source"] = web_read_source\n'
        )

    if "vault_lint" in allowed_tools:
        snippets.append(
            'if "vault_lint" in allowed:\n'
            '    def vault_lint(file_path):\n'
            '        p = Path(file_path)\n'
            '        if not p.exists():\n'
            '            return {"error": "file not found"}\n'
            '        text = p.read_text(encoding="utf-8", errors="replace")\n'
            '        issues = []\n'
            '        has_fm = text.startswith("---")\n'
            '        if not has_fm:\n'
            '            issues.append("missing frontmatter")\n'
            '        import re as _re\n'
            '        links = _re.findall(r"\\[\\[([^\\]]+)\\]\\]", text)\n'
            '        broken = []\n'
            '        vault = Path(vault_path)\n'
            '        for link in links:\n'
            '            found = list(vault.rglob(f"{link.split(chr(124))[0]}.md"))\n'
            '            if not found:\n'
            '                broken.append(link)\n'
            '        if broken:\n'
            '            issues.append(f"{len(broken)} broken wikilinks: {broken[:5]}")\n'
            '        return {"has_frontmatter": has_fm, "broken_wikilinks": broken, "issues": issues}\n'
            '    namespace["vault_lint"] = vault_lint\n'
        )

    if "vault_append" in allowed_tools:
        snippets.append(
            'if "vault_append" in allowed:\n'
            '    def vault_append(file_path, content):\n'
            '        p = Path(file_path)\n'
            '        if not p.exists():\n'
            '            return {"error": "file not found"}\n'
            '        existing = p.read_text(encoding="utf-8")\n'
            '        p.write_text(existing + "\\n" + content, encoding="utf-8")\n'
            '        return {"appended": True, "chars_added": len(content)}\n'
            '    namespace["vault_append"] = vault_append\n'
        )

    if "vault_list" in allowed_tools:
        snippets.append(
            'if "vault_list" in allowed:\n'
            '    def vault_list(directory=None, tag=None):\n'
            '        vault = Path(vault_path)\n'
            '        if directory:\n'
            '            vault = vault / directory\n'
            '        results = []\n'
            '        for root, dirs, files in os.walk(str(vault)):\n'
            '            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]\n'
            '            for f in files:\n'
            '                if f.endswith(".md"):\n'
            '                    results.append(str(Path(root, f)))\n'
            '        return results\n'
            '    namespace["vault_list"] = vault_list\n'
        )

    if "code_read" in allowed_tools:
        snippets.append(
            'if "code_read" in allowed:\n'
            '    def code_read(file_path, start_line=None, end_line=None):\n'
            '        p = Path(file_path)\n'
            '        if not p.exists():\n'
            '            return {"error": "file not found"}\n'
            '        text = p.read_text(encoding="utf-8", errors="replace")\n'
            '        lines = text.split("\\n")\n'
            '        start_idx = (start_line or 1) - 1\n'
            '        end_idx = end_line if end_line is not None else None\n'
            '        lines = lines[start_idx:end_idx]\n'
            '        return "\\n".join(lines)\n'
            '    namespace["code_read"] = code_read\n'
        )

    if "run_procedure" in allowed_tools:
        # Recursive procedure execution: shell out to the synchronous
        # CLI (run_procedure.py) which calls asyncio.run(execute_procedure).
        # The wrapper passes the current call stack + procedure name so
        # the child can detect cycles and enforce MAX_PROC_DEPTH.  See
        # [[Procedure-Subprocess-Architecture]] and run_procedure.py.
        snippets.append(
            'if "run_procedure" in allowed:\n'
            '    from subprocess_utils import run as _sp_run\n'
            '    import json as _json\n'
            '    _backend_dir = Path(os.environ.get("PYTHONPATH", ".").split(os.pathsep)[0])\n'
            '    _venv_py = _backend_dir.parent.parent / ".venv" / "Scripts" / "python.exe"\n'
            '    if not _venv_py.exists():\n'
            '        _venv_py = Path(sys.executable)\n'
            '    _proc_self = os.environ.get("PROCEDURE_SELF_NAME", "")\n'
            '    _call_stack = _json.loads(os.environ.get("PROCEDURE_CALL_STACK", "[]"))\n'
            '    if _proc_self and _proc_self not in _call_stack:\n'
            '        _call_stack = _call_stack + [_proc_self]\n'
            '    def run_procedure(procedure_name, args=None):\n'
            '        """Run another procedure by note stem. Optionally pass a dict\n'
            '        of call-time arguments that the child reads via the injected\n'
            '        ``args`` variable. Returns a dict with {procedure,\n'
            '        overall_passed, steps_executed, final_output, child_procedures,\n'
            '        step_details}. Raises RuntimeError on cycle or depth exceeded\n'
            '        so the parent step fails loudly."""\n'
            '        cmd = [str(_venv_py), str(_backend_dir / "run_procedure.py"),\n'
            '               "--procedure-name", str(procedure_name),\n'
            '               "--vault-path", os.environ.get("VAULT_PATH", "."),\n'
            '               "--call-stack", _json.dumps(_call_stack),\n'
            '               "--procedure-args", _json.dumps(args or {}, default=str)]\n'
            '        r = _sp_run(cmd, capture_output=True, text=True, timeout=120)\n'
            '        if not r.stdout.strip():\n'
            '            raise RuntimeError("run_procedure produced no output; "\n'
            '                               "stderr: " + r.stderr[:500])\n'
            '        out = _json.loads(r.stdout)\n'
            '        if out.get("cycle_detected") or out.get("depth_exceeded"):\n'
            '            raise RuntimeError(out.get("error", "recursion error"))\n'
            '        if "error" in out and "overall_passed" not in out:\n'
            '            raise RuntimeError(out["error"])\n'
            '        return out\n'
            '    namespace["run_procedure"] = run_procedure\n'
        )

    if "vault_graph_analyzer" in allowed_tools:
        snippets.append(
            'if "vault_graph_analyzer" in allowed:\n'
            '    from custom_tools.vault_graph_analyzer import analyze_graph\n'
            '    def vault_graph_analyzer(exclude_patterns=None, max_hops=6):\n'
            '        result = analyze_graph(vault_path, exclude_patterns or ["LICENSE.md"], max_hops)\n'
            '        return {"status": "success", "analysis": result}\n'
            '    namespace["vault_graph_analyzer"] = vault_graph_analyzer\n'
        )

    if "vault_delete" in allowed_tools:
        snippets.append(
            'if "vault_delete" in allowed:\n'
            '    from custom_tools.vault_delete import run as _vault_delete_run\n'
            '    def vault_delete(file_path):\n'
            '        return _vault_delete_run({"file_path": file_path})\n'
            '    namespace["vault_delete"] = vault_delete\n'
        )

    return "\n".join(snippets)


# ── Subprocess wrapper for code steps ───────────────────────────────────

def _run_code_step(
    step: Step,
    allowed_tools: list[str],
    vault_path: str,
    prior_results: list[Any],
    timeout: int = 120,
    procedure_name: str = "",
    call_stack: list[str] | None = None,
    model_cartridge: str = "big",
    procedure_args: dict | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """Execute a code step in a subprocess.

    Returns ``(success, output, error, traceback)``.

    ``procedure_name`` and ``call_stack`` are passed to the subprocess
    so the injected ``run_procedure`` tool can detect cycles and enforce
    MAX_PROC_DEPTH when this step recurses into another procedure.
    """
    if step.code is None:
        return False, "", "code step has no code", ""

    tool_preamble = _build_tool_preamble(allowed_tools)

    # Build the wrapper script using string replacement (not .format()
    # to avoid conflicts with { and } in Python code).
    wrapper = (
        'import sys, json, os, traceback\n'
        'from pathlib import Path\n'
        '\n'
        'vault_path = os.environ.get("VAULT_PATH", ".")\n'
        'prior_results = json.loads(os.environ.get("PRIOR_RESULTS", "[]"))\n'
        'allowed = json.loads(os.environ.get("PROCEDURE_ALLOWED_TOOLS", "[]"))\n'
        '_IGNORED_DIRS = {".git", ".obsidian", ".venv", "vaultbot_venv", "vaultbot_index", "sessions", "partials", "__pycache__"}\n'
        '\n'
        'namespace = {\n'
        '    "__builtins__": __builtins__,\n'
        '    "prior_results": prior_results,\n'
        '    "Path": Path,\n'
        '    "json": json,\n'
        '    "os": os,\n'
        '    "vault_path": vault_path,\n'
        '    "_IGNORED_DIRS": _IGNORED_DIRS,\n'
        '}\n'
        '\n'
        '# --- Tool injection ---\n'
        + tool_preamble +
        '\n'
        '# --- Step code ---\n'
        'step_code = ' + repr(step.code) + '\n'
        '\n'
        'try:\n'
        '    exec(step_code, namespace)\n'
        '    result = namespace.get("result")\n'
        '    if result is None and "result" not in namespace:\n'
        '        result = ""\n'
        '    try:\n'
        '        json.dumps(result)\n'
        '    except (TypeError, ValueError):\n'
        '        result = str(result)\n'
        '    print(json.dumps({"status": "ok", "result": result}))\n'
        'except Exception as e:  # noqa: BLE001 — best-effort, returns error to caller\n'
        '    print(json.dumps({\n'
        '        "status": "error",\n'
        '        "error": str(e),\n'
        '        "traceback": traceback.format_exc(),\n'
        '    }))\n'
    )

    # Find the venv python
    backend_dir = Path(__file__).parent.resolve()
    venv_python = str(backend_dir.parent.parent / ".venv" / "Scripts" / "python.exe")
    if not Path(venv_python).exists():
        venv_python = sys.executable

    # Prepare environment — scrubbed of secrets (API keys/tokens/passwords)
    # so LLM-authored procedure code cannot read or exfiltrate them. Only the
    # non-secret PROCEDURE_* overrides and PYTHONPATH/VAULT_PATH are added back.
    env = {
        **scrubbed_env(),
        "PYTHONPATH": str(backend_dir),
        "VAULT_PATH": vault_path,
        "PROCEDURE_ALLOWED_TOOLS": json.dumps(allowed_tools),
        "PRIOR_RESULTS": json.dumps(prior_results, default=str),
        "PROCEDURE_SELF_NAME": procedure_name,
        "PROCEDURE_CALL_STACK": json.dumps(call_stack or []),
        "PROCEDURE_MODEL_CARTRIDGE": model_cartridge,
        "PROCEDURE_ARGS": json.dumps(procedure_args or {}, default=str),
    }

    try:
        proc = _subprocess_run(
            [venv_python, "-c", wrapper],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(vault_path).resolve()),
            env=env,
            # Resource limits (POSIX): mem/CPU/fork caps. None on Windows.
            preexec_fn=preexec_fn,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"subprocess timeout after {timeout}s", ""
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, "", f"subprocess error: {e}", traceback.format_exc()

    # Parse output
    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr.strip()[:2000]
        return False, "", f"no stdout from subprocess. stderr: {stderr}", ""

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return False, stdout[:2000], f"invalid JSON from subprocess: {stdout[:200]}", ""

    if result.get("status") == "error":
        return False, "", result.get("error", "unknown error"), result.get("traceback", "")

    output = result.get("result", "")
    if not isinstance(output, str):
        output = json.dumps(output, default=str)

    return True, output, None, None


# ── LLM step execution ──────────────────────────────────────────────────

def _run_llm_step(
    step: Step,
    prior_results: list[tuple[int, str]],
    llm_client: Any = None,
) -> tuple[bool, str, str | None]:
    """Execute an LLM step via the cartridge-selected client with minimal context.

    Returns ``(success, output, error)``.

    The LLM gets:
    - System: "You are a procedure executor. Follow the instruction."
    - Prompt: prior step results + the LLM instruction

    No vault context, no system prompt, no identity — the procedure-bot
    is NOT VaultBot.

    Args:
        llm_client: The cartridge-selected LLM client (big/small/vision).
            If None, falls back to get_llm_client() (the big model).
    """
    if step.llm_instruction is None:
        return False, "", "LLM step has no instruction"

    # Build minimal context from prior results
    prior_context = ""
    if prior_results:
        prior_lines = []
        for num, out in prior_results:
            snippet = out[:2000] + ("..." if len(out) > 2000 else "")
            prior_lines.append(f"Step {num} output:\n{snippet}")
        prior_context = "\n\n".join(prior_lines)

    prompt_parts = []
    if prior_context:
        prompt_parts.append("## Prior Step Results\n")
        prompt_parts.append(prior_context)
        prompt_parts.append("\n\n---\n\n")
    prompt_parts.append(step.llm_instruction)

    prompt = "\n".join(prompt_parts)

    system = (
        "You are a procedure executor. Follow the instruction exactly. "
        "Output only the result. Do not add commentary or explanation "
        "unless the instruction asks for it."
    )

    try:
        client = llm_client
        if client is None:
            from llm_client import get_llm_client
            client = get_llm_client()
        result = client.generate(
            prompt=prompt,
            system=system,
            stream=False,
        )
        output = result.get("response", "")
        if not output:
            return False, "", "LLM returned empty response"
        return True, output, None
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False, "", f"LLM error: {e}"


# ── Active frame builder (for v1 text steps) ─────────────────────────────

def _build_active_frame(
    step: Step,
    procedure: Procedure,
    context: str,
    step_outputs: list[tuple[int, str]],
) -> list[dict[str, str]]:
    """Build the active frame for the LLM (v1 text steps).

    The active frame puts the CURRENT STEP FIRST (checkpointing —
    resets evidence distance), then prior step outputs, then the full
    procedure overview (full-program cursor), then the vault context.
    """
    overview_lines = []
    for s in procedure.steps:
        marker = " >>> " if s.number == step.number else "     "
        overview_lines.append(f"{marker}Step {s.number}: {s.instruction}")
    overview = "\n".join(overview_lines)

    prior_outputs = ""
    if step_outputs:
        prior_lines = []
        for num, out in step_outputs:
            snippet = out[:500] + ("..." if len(out) > 500 else "")
            prior_lines.append(f"Step {num} output: {snippet}")
        prior_outputs = "\n\n".join(prior_lines)

    prompt_parts = [
        f"## CURRENT STEP (Step {step.number})",
        f"{step.instruction}",
        "",
    ]

    if step.validation:
        prompt_parts.append(f"Validation criteria: {step.validation}")
        prompt_parts.append("")

    if prior_outputs:
        prompt_parts.append("## PRIOR STEP OUTPUTS")
        prompt_parts.append(prior_outputs)
        prompt_parts.append("")

    prompt_parts.append("## FULL PROCEDURE OVERVIEW")
    prompt_parts.append(overview)
    prompt_parts.append("")

    if context:
        prompt_parts.append("## VAULT CONTEXT")
        prompt_parts.append(context)
        prompt_parts.append("")

    prompt_parts.append("Execute the current step. Output only the result of this step.")

    user_content = "\n".join(prompt_parts)

    system_content = (
        "You are VaultBot executing a procedure step-by-step. "
        "Follow the current step exactly. Do not skip ahead. "
        "Do not combine steps. Output only what the current step asks for."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


# ── Step-gate runtime ────────────────────────────────────────────────────

# Maximum recursion depth for procedures calling procedures via
# run_procedure.  3 lets a verify-procedure call a source-credibility
# procedure without runaway token spend.  See [[Procedure-Subprocess-Architecture]].
MAX_PROC_DEPTH = 3

# --- Condition evaluation (free-text predicates) -------------------------
#
# Conditions are free text (e.g. ``[condition: if < 3 notes]``) but the
# vault uses three recurrent forms.  We evaluate those deterministically;
# anything unparseable is treated as "skip the step" (fail-safe: a
# precondition we can't verify must not let the step run).

# Count comparisons: "< 3 notes", ">= 2 titles", "!= 0 errors"
_COUNT_RE = re.compile(
    r'(?P<op><=|>=|==|!=|<|>)\s*(?P<n>\d+)\s*(?P<unit>\w+)?',
    re.IGNORECASE,
)
# Presence: 'contains "literal"' / "contains 'literal'"
_CONTAINS_RE = re.compile(
    r'contains\s+(?P<q>["\'])(?P<lit>.*?)(?P=q)', re.IGNORECASE,
)
# Boolean status: "passed" / "failed"
_BOOL_RE = re.compile(r'^(passed|failed)$', re.IGNORECASE)


def _count_thing(output: str, unit: str) -> int:
    """Count occurrences of a ``unit`` class in ``output``.

    Recognised units (case-insensitive):
    - notes / note / titles / title → count ``[[...]]`` wikilinks
    - sources / source / urls / url / links / link → count http(s) URLs
    - items / item / lines / line → count non-empty bullet/numbered lines

    Any unrecognised unit falls back to counting whitespace-separated
    tokens (a generic "things" count).  This keeps the predicate usable
    for ad-hoc units without raising.
    """
    u = (unit or "").lower().rstrip("s")  # normalise plural
    if u in {"note", "title"}:
        return len(re.findall(r'\[\[([^\]]+)\]\]', output))
    if u in {"source", "url", "link"}:
        return len(re.findall(r'https?://\S+', output))
    if u in {"item", "line"}:
        return sum(1 for ln in output.split("\n")
                   if ln.strip() and re.match(r'\s*([-*]|\d+[.)])\s+', ln))
    # Fallback: count non-empty whitespace tokens.
    return len([t for t in output.split() if t])


def _evaluate_condition(
    condition: str,
    prior_results: list[Any],
    step_outputs: list[tuple[int, str]],
) -> tuple[bool, str]:
    """Evaluate a free-text condition predicate deterministically.

    Returns ``(should_run, reason)``.  ``should_run=False`` means the
    step must be skipped (its precondition did not hold, or could not be
    parsed — fail-safe skip).  ``reason`` is a short diagnostic logged
    to the session logger.

    Recognised forms (case-insensitive):
    1. **Count comparison**: ``< 3 notes``, ``>= 2 titles``, ``!= 0 errors``
       — compares ``_count_thing`` of the concatenated prior outputs
       against the integer.
    2. **Presence**: ``contains "literal"`` — substring check against
       the concatenated prior outputs.
    3. **Boolean status**: ``passed`` / ``failed`` — true if the last
       prior step passed (resp. failed).

    Any other form → ``(False, "unparseable")`` so the step is skipped
    loudly rather than run with an unverified precondition.
    """
    cond = condition.strip().lower()

    # Strip a leading "if " if present (common in vault notes).
    if cond.startswith("if "):
        cond = cond[3:].strip()

    joined = "\n".join(str(o) for _, o in step_outputs) + "\n" + json.dumps(prior_results, default=str)

    m = _COUNT_RE.search(cond)
    if m:
        op, n, unit = m.group("op"), int(m.group("n")), m.group("unit")
        got = _count_thing(joined, unit or "")
        checks = {
            "<": got < n, "<=": got <= n, ">": got > n,
            ">=": got >= n, "==": got == n, "!=": got != n,
        }
        ok = checks.get(op, False)
        return ok, f"count {got} {op} {n} {unit or ''}".strip()

    m = _CONTAINS_RE.search(cond)
    if m:
        lit = m.group("lit")
        return (lit in joined), f"contains {lit!r}"

    m = _BOOL_RE.match(cond)
    if m:
        want = m.group(1).lower()
        if not step_outputs:
            return (want == "failed"), f"bool {want} (no prior steps)"
        # The last step's pass/fail isn't directly available here; we
        # approximate by checking the last entry in prior_results has
        # content (treat as passed) — callers that need precise
        # pass/fail should use a count predicate instead.
        last_out = step_outputs[-1][1]
        ok = (want == "passed") if last_out else (want == "failed")
        return ok, f"bool {want}"

    return False, "unparseable"


async def execute_procedure(
    procedure: Procedure,
    context: str,
    llm_client: Any,
    vault_path: str = ".",
    session_logger: Any = None,
    progress_callback: Callable | None = None,
    procedure_tracker: Any = None,
    call_stack: list[str] | None = None,
    procedure_args: dict | None = None,
) -> ExecutionResult:
    """Execute a compiled procedure one step at a time with gating.

    Handles all three step types:
    - **code**: subprocess with tool injection (zero LLM cost)
    - **llm**: stripped-down LLM call via get_llm_client() (minimal context)
    - **text**: active-frame LLM call (v1 backward compat)

    The runtime never raises — errors are captured in StepResult and
    the procedure stops gracefully with a loud failure report.

    Args:
        procedure: Compiled Procedure object from procedure_compiler.
        context: Vault context string (used only for v1 text steps).
        llm_client: Main LLM client (used only for v1 text steps).
        vault_path: Path to the vault root (used for tool injection).
        session_logger: Optional session logger for structured logging.
        progress_callback: Optional async callback ``(step_number,
            total_steps, output)`` for progress updates.
        procedure_tracker: Optional ProcedureTracker for step-level logging.
        call_stack: List of procedure names already in flight (for cycle
            detection when this procedure is invoked recursively via
            ``run_procedure``).  This procedure's name is appended before
            recursing.  See [[Procedure-Subprocess-Architecture]].
        procedure_args: Optional dict of call-time arguments forwarded to
            every code step via the injected ``args`` variable (env var
            ``PROCEDURE_ARGS``). Defaults to ``{}``.
    """
    call_stack = list(call_stack or [])
    if not procedure.steps:
        # Distinguish two cases:
        # 1. Empty body (no content after ## Steps) → legitimately 0 steps, pass.
        # 2. Body has content but 0 parsed steps → format mismatch, FAIL.
        #    The most common cause is ### Step N: headers instead of
        #    numbered list steps (1. ...) which _parse_steps doesn't
        #    recognize. Tell the caller EXACTLY what went wrong.
        _body = (procedure.raw_text or "").strip()
        # Strip frontmatter to check the actual body content.
        if _body.startswith("---"):
            _fm_end = _body.find("\n---", 3)
            if _fm_end > 0:
                _body = _body[_fm_end + 4:].strip()
        # Strip the ## Steps header line itself — what matters is whether
        # there's content UNDER it, not the header itself.
        import re as _re
        _steps_match = _re.search(r'^##\s+Steps\s*$', _body, _re.MULTILINE | _re.IGNORECASE)
        if _steps_match:
            _body = _body[_steps_match.end():].strip()
        if not _body:
            # Empty body — legitimately 0 steps.
            return ExecutionResult(
                procedure_name=procedure.name,
                steps=[],
                overall_passed=True,
                final_output="",
            )
        # Body has content but 0 parsed steps — loud failure with diagnosis.
        _diagnosis = (
            "PROCEDURE COMPILED 0 STEPS. The procedure compiler "
            "(procedure_compiler.py _parse_steps) only recognizes "
            "numbered list steps inside a ## Steps section:\n"
            "  1. instruction text\n"
            "  2. ```python\n     code here\n     ```\n"
            "It does NOT parse ### Step N: headers. If your procedure uses "
            "### headers, rewrite them as numbered list items. Check the "
            "procedure's ## Steps section format."
        )
        _body_snippet = (procedure.raw_text or "")[:200]
        if session_logger:
            session_logger.log("procedure_zero_steps", {
                "procedure": procedure.name,
                "diagnosis": _diagnosis,
                "body_snippet": _body_snippet,
            })
        return ExecutionResult(
            procedure_name=procedure.name,
            steps=[],
            overall_passed=False,
            final_output=_diagnosis,
            failed_step=0,
        )

    step_results: list[StepResult] = []
    step_outputs: list[tuple[int, str]] = []
    all_outputs: list[str] = []
    prior_results: list[Any] = []

    # Build step lookup map
    step_map = {s.number: s for s in procedure.steps}
    executed_steps: set[int] = set()
    current_step_num = procedure.steps[0].number
    max_iterations = len(procedure.steps) * 3
    failed_step: int | None = None
    child_procedures: list[dict] = []

    iterations = 0
    while current_step_num is not None and iterations < max_iterations:
        iterations += 1

        if current_step_num in executed_steps:
            break

        step = step_map.get(current_step_num)
        if step is None:
            break

        executed_steps.add(current_step_num)

        if progress_callback:
            await progress_callback(step.number, len(procedure.steps), "")

        # --- Condition gate: skip the step if its precondition fails ---
        # Fail-safe: an unparseable condition also skips (we never run a
        # step whose precondition we cannot verify).  Logged loudly.
        if step.condition is not None:
            should_run, reason = _evaluate_condition(
                step.condition, prior_results, step_outputs)
            if not should_run:
                sr = StepResult(
                    step_number=step.number,
                    step_type=step.step_type,
                    passed=True,  # skipped ≠ failed
                    output=f"[skipped: condition '{reason}' not met]",
                )
                step_results.append(sr)
                step_outputs.append((step.number, sr.output))
                if session_logger:
                    session_logger.log("step_gate_condition_skip", {
                        "procedure": procedure.name,
                        "step": step.number,
                        "condition": step.condition,
                        "reason": reason,
                    })
                # Skip to next step without executing.
                step_numbers = sorted(step_map.keys())
                idx = step_numbers.index(current_step_num)
                if idx + 1 < len(step_numbers):
                    current_step_num = step_numbers[idx + 1]
                else:
                    current_step_num = None
                continue

        # --- Execute based on step type ---
        if step.step_type == "code":
            # Use 300s timeout for steps that may call llm_generate (synthesis can be slow)
            _step_timeout = 300 if "llm_generate" in procedure.allowed_tools else 120
            success, output, error, tb = _run_code_step(
                step, procedure.allowed_tools, vault_path, prior_results,
                timeout=_step_timeout,
                procedure_name=procedure.name,
                call_stack=call_stack,
                model_cartridge=getattr(procedure, "model_cartridge", "big"),
                procedure_args=procedure_args,
            )
            if success:
                # Capture any child procedures the step spawned (the
                # injected run_procedure tool returns a dict with a
                # ``child_procedures`` field when it recurses).
                try:
                    parsed = json.loads(output) if output.strip().startswith("{") else None
                    if isinstance(parsed, dict):
                        for child in parsed.get("child_procedures", []):
                            if isinstance(child, dict) and child.get("name"):
                                child_procedures.append(child)
                except (json.JSONDecodeError, AttributeError):
                    pass
                sr = StepResult(
                    step_number=step.number,
                    step_type="code",
                    passed=True,
                    output=output,
                )
            else:
                sr = StepResult(
                    step_number=step.number,
                    step_type="code",
                    passed=False,
                    output="",
                    error=error,
                    traceback=tb,
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log("step_gate_code_error", {
                        "procedure": procedure.name,
                        "step": step.number,
                        "error": error,
                        "traceback": tb[:500] if tb else "",
                    })
                break

        elif step.step_type == "llm":
            success, output, error = _run_llm_step(step, step_outputs, llm_client)
            if success:
                sr = StepResult(
                    step_number=step.number,
                    step_type="llm",
                    passed=True,
                    output=output,
                )
            else:
                sr = StepResult(
                    step_number=step.number,
                    step_type="llm",
                    passed=False,
                    output="",
                    error=error,
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log("step_gate_llm_error", {
                        "procedure": procedure.name,
                        "step": step.number,
                        "error": error,
                    })
                break

        else:  # text step (v1)
            messages = _build_active_frame(step, procedure, context, step_outputs)
            try:
                result = llm_client.chat(messages, temperature=0.3, stream=False)
                output = result.get("response", "")
                passed, val_error = _validate_step(output, step.validation)
                sr = StepResult(
                    step_number=step.number,
                    step_type="text",
                    passed=passed,
                    output=output,
                    validation_error=val_error,
                )
                # Validation failure stops the procedure loudly.
                if not passed:
                    step_results.append(sr)
                    failed_step = step.number
                    if session_logger:
                        session_logger.log("step_gate_validation_fail", {
                            "procedure": procedure.name,
                            "step": step.number,
                            "validation_error": val_error,
                        })
                    break
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                sr = StepResult(
                    step_number=step.number,
                    step_type="text",
                    passed=False,
                    output="",
                    error=f"LLM error: {e}",
                )
                step_results.append(sr)
                failed_step = step.number
                if session_logger:
                    session_logger.log("step_gate_llm_error", {
                        "procedure": procedure.name,
                        "step": step.number,
                        "error": str(e),
                    })
                break

        step_results.append(sr)
        step_outputs.append((step.number, sr.output))
        all_outputs.append(sr.output)
        prior_results.append(sr.output)

        # Step-level logging
        if procedure_tracker:
            try:
                procedure_tracker.log_step_result(
                    procedure.name, step.number, sr.passed,
                    sr.error or sr.validation_error or "",
                )
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass

        if session_logger:
            session_logger.log("step_gate_result", {
                "procedure": procedure.name,
                "step": step.number,
                "step_type": sr.step_type,
                "passed": sr.passed,
                "error": sr.error or "",
                "output_length": len(sr.output),
            })

        if progress_callback:
            await progress_callback(step.number, len(procedure.steps), sr.output)

        # --- Branch jump: if the step has a branch_target and passed ---
        # Jump to the named step instead of the linear next step.  The
        # ``executed_steps`` set + ``max_iterations`` guard prevents
        # infinite loops on a branch cycle.
        if step.branch_target is not None and sr.passed:
            target = step.branch_target
            if target in step_map:
                if session_logger:
                    session_logger.log("step_gate_branch", {
                        "procedure": procedure.name,
                        "from_step": step.number,
                        "to_step": target,
                    })
                current_step_num = target
                continue
            # Branch target doesn't exist — log loudly and fall through.
            if session_logger:
                session_logger.log("step_gate_branch_missing", {
                    "procedure": procedure.name,
                    "from_step": step.number,
                    "to_step": target,
                })

        # Advance to next step
        step_numbers = sorted(step_map.keys())
        idx = step_numbers.index(current_step_num)
        if idx + 1 < len(step_numbers):
            current_step_num = step_numbers[idx + 1]
        else:
            current_step_num = None

    overall_passed = all(r.passed for r in step_results) if step_results else True
    final_output = "\n\n".join(all_outputs)

    if session_logger:
        session_logger.log("step_gate_complete", {
            "procedure": procedure.name,
            "steps_executed": len(step_results),
            "overall_passed": overall_passed,
            "failed_step": failed_step,
            "final_output_length": len(final_output),
        })

    # Procedure-level logging
    if procedure_tracker:
        try:
            procedure_tracker.log_result(
                procedure=procedure.name,
                task="procedure_execution",
                validation_result="pass" if overall_passed else "fail",
                validation_tool="step_gate",
                error_details=f"failed at step {failed_step}" if failed_step else "",
                category="validation_error",
            )
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass

    return ExecutionResult(
        procedure_name=procedure.name,
        steps=step_results,
        overall_passed=overall_passed,
        final_output=final_output,
        failed_step=failed_step,
        child_procedures=child_procedures,
    )
