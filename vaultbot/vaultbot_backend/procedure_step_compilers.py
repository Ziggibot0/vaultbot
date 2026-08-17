"""Step compilers for the procedure dispatch DSL.

Extracted from ``procedure_compiler.py`` to keep that file under the
line-count target.  Contains:

  - ``_resolve_template`` — resolve ``{{ var }}`` / ``{{ var | filter }}``.
  - ``_compile_classify``, ``_compile_call``, ``_compile_run``,
    ``_compile_extract``, ``_compile_dispatch``, ``_compile_condition``
    — compile dispatch DSL entries into ``Step`` objects.
  - ``_parse_dispatch_section`` — parse a ``## Dispatch`` YAML section
    into a single code ``Step``.

``Step`` is imported from ``procedure_types`` (no circular import).
"""

from __future__ import annotations

import json
import logging
import re

from procedure_types import Step

logger = logging.getLogger(__name__)

# PyYAML is optional — only needed for ## Dispatch sections.
# If missing, dispatch sections are silently skipped (no DSL).
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
    yaml = None  # type: ignore


# ── Regex patterns ────────────────────────────────────────────────────────

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
