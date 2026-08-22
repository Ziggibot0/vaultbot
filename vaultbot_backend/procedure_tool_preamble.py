"""Tool preamble builder for procedure code steps.

When a procedure's ``allowed_tools`` frontmatter lists tools the step
code may call, the step-gate runtime needs to inject Python code into
the subprocess that imports the backend modules and creates wrapper
functions the step code can call directly.  That injected code is the
"tool preamble."

This module owns the preamble generator and its cache.  The preamble
is a large (~2-4 KB) string, identical for every code step in the same
procedure (it depends solely on ``allowed_tools``), so it is cached by
sorted-tuple key to avoid rebuilding it per step.

The imports inside the preamble strings (``from llm_client import ...``,
``from custom_tools.vault_delete import ...``, etc.) execute at RUNTIME
inside the subprocess — NOT at this module's load time.  So this module
itself only needs ``subprocess_utils`` for the types it references in
the ``run_procedure`` wrapper (``scrubbed_env`` / ``preexec_fn`` are
NOT needed here — they are used by the executor, not the preamble).

See:
  - ``procedure_step_executor.py`` — calls ``_build_tool_preamble``
  - ``step_gate_runtime.py`` — the orchestrator
  - [[Procedure-Subprocess-Architecture]]
"""

from __future__ import annotations

# ``_IGNORED_DIRS`` is referenced inside the preamble's generated code
# (the ``vault_search`` and ``vault_lint`` wrappers prune these dirs
# from ``os.walk``).  It is also duplicated as a literal set inside the
# subprocess wrapper in ``_run_code_step`` — kept here as the canonical
# source so both sites stay in sync.
_IGNORED_DIRS = {
    ".git",
    ".obsidian",
    ".venv",
    "vaultbot_venv",
    "vaultbot_index",
    "sessions",
    "partials",
    "__pycache__",
}

# --- Preamble cache (allowed_tools doesn't change between steps) ---
# Keyed by sorted tuple of allowed_tools.  The preamble is a large
# (~2-4 KB) string that's identical for all code steps in the same
# procedure (allowed_tools comes from frontmatter).  Without this cache
# the string is rebuilt from scratch for every code step.
_PREAMBLE_CACHE: dict[tuple[str, ...], str] = {}


def _build_tool_preamble(allowed_tools: list[str]) -> str:
    """Build the Python code that injects allowed tools into the namespace.

    This code runs in the subprocess before the step code. It imports
    backend modules and creates wrapper functions that the step code
    can call directly.
    """
    # Cache hit: the preamble depends solely on the allowed_tools list.
    # Sorted tuple key ensures order doesn't matter.
    cache_key = tuple(sorted(allowed_tools))
    cached = _PREAMBLE_CACHE.get(cache_key)
    if cached is not None:
        return cached

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
        "if not isinstance(args, dict):\n"
        "    args = {}\n"
        'namespace["args"] = args\n'
        'output = list(prior_results.values())[-1] if prior_results else ""\n'
        "if not isinstance(output, str):\n"
        "    try:\n"
        "        output = json.dumps(output, default=str)\n"
        "    except Exception:\n"
        "        output = str(output)\n"
        'namespace["output"] = output\n'
    )

    if "llm_generate" in allowed_tools:
        snippets.append(
            'if "llm_generate" in allowed:\n'
            "    from llm_client import get_llm_client, get_small_client, "
            "get_vision_client\n"
            '    _cartridge = os.environ.get("PROCEDURE_MODEL_CARTRIDGE", "big")\n'
            '    if _cartridge == "small":\n'
            "        _client = get_small_client() or get_llm_client()\n"
            '    elif _cartridge == "vision":\n'
            "        _client = get_vision_client() or get_llm_client()\n"
            "    else:\n"
            "        _client = get_llm_client()\n"
            "    # Small-cartridge procedures are bounded tasks (rerank, filter,\n"
            "    # summarize) — disable reasoning so a 0.8b model does not spend\n"
            "    # 60s thinking on a one-line judgment. Big-cartridge procedures\n"
            "    # keep reasoning (synthesis needs it).\n"
            '    _think = False if _cartridge == "small" else None\n'
            '    def llm_generate(prompt, system="You are a procedure executor. '
            'Follow the instruction. Output only the result."):\n'
            '        messages = [{"role": "system", "content": system}, '
            '{"role": "user", "content": prompt}]\n'
            "        result = _client.chat(messages=messages, stream=False, "
            "think=_think, max_predict=256, timeout=30)\n"
            '        return result.get("response", "")\n'
            '    namespace["llm_generate"] = llm_generate\n'
        )

    if "vault_search" in allowed_tools:
        snippets.append(
            'if "vault_search" in allowed:\n'
            "    def vault_search(query, k=5):\n"
            "        import faiss, numpy as np, requests, json\n"
            '        _vs = {"index": None, "metadata": {}, "loaded": False}\n'
            '        if not _vs["loaded"]:\n'
            '            _vs["loaded"] = True\n'
            '            idx_dir = Path(vault_path) / "vaultbot" / '
            '"vaultbot_backend" / "vaultbot_index"\n'
            '            idx_file = idx_dir / "index.faiss"\n'
            '            meta_file = idx_dir / "metadata.json"\n'
            "            if idx_file.exists() and meta_file.exists():\n"
            "                try:\n"
            '                    _vs["index"] = faiss.read_index(str(idx_file))\n'
            '                    with open(meta_file, encoding="utf-8") as f:\n'
            "                        raw = json.load(f)\n"
            '                    _vs["metadata"] = {int(fid): m for fid, m in '
            'raw.get("metadata", {}).items()}\n'
            "                except Exception:\n"
            "                    pass\n"
            '        if _vs["index"] is not None and _vs["metadata"]:\n'
            "            try:\n"
            "                resp = requests.post(\n"
            '                    "http://localhost:11434/api/embeddings",\n'
            '                    json={"model": "nomic-embed-text", "prompt": query},\n'
            "                    timeout=10\n"
            "                )\n"
            "                resp.raise_for_status()\n"
            '                emb = np.array(resp.json()["embedding"], '
            "dtype=np.float32).reshape(1, -1)\n"
            "                faiss.normalize_L2(emb)\n"
            '                distances, indices = _vs["index"].search(emb, k * 2)\n'
            "                results = []\n"
            "                for dist, idx in zip(distances[0], indices[0]):\n"
            '                    if idx < 0 or idx not in _vs["metadata"]:\n'
            "                        continue\n"
            '                    meta = _vs["metadata"][idx]\n'
            '                    fp = meta.get("file_path", "")\n'
            '                    results.append({"file_path": fp, "name": '
            'Path(fp).stem, "score": 1.0 / (1.0 + float(dist))})\n'
            "                return results[:k]\n"
            "            except Exception:\n"
            "                pass\n"
            "        vault = Path(vault_path)\n"
            "        query_terms = [t.lower() for t in query.split() if len(t) > 2]\n"
            "        results = []\n"
            "        for root, dirs, files in os.walk(str(vault)):\n"
            "            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]\n"
            "            for f in files:\n"
            '                if not f.endswith(".md"):\n'
            "                    continue\n"
            "                try:\n"
            '                    text = Path(root, f).read_text(encoding="utf-8", '
            'errors="replace")\n'
            "                    text_lower = text.lower()\n"
            "                    matches = sum(1 for t in query_terms if t "
            "in text_lower)\n"
            "                    if matches > 0:\n"
            '                        results.append({"file_path": str(Path(root, f)), '
            '"name": f[:-3], "score": matches / max(len(query_terms), 1)})\n'
            "                except Exception:\n"
            "                    continue\n"
            '        results.sort(key=lambda r: r["score"], reverse=True)\n'
            "        return results[:k]\n"
            '    namespace["vault_search"] = vault_search\n'
        )

    if "web_read_source" in allowed_tools:
        snippets.append(
            'if "web_read_source" in allowed:\n'
            "    def web_read_source(url=None, file=None):\n"
            '        web_dir = Path(vault_path).parent / "learningMaterial" / "web"\n'
            "        if file:\n"
            "            p = web_dir / file\n"
            "        elif url:\n"
            "            import hashlib\n"
            "            h = hashlib.md5(url.encode()).hexdigest()[:8]\n"
            '            candidates = list(web_dir.glob(f"*{h}*"))\n'
            "            p = candidates[0] if candidates else None\n"
            "        else:\n"
            "            return None\n"
            "        if p and p.exists():\n"
            '            return p.read_text(encoding="utf-8", errors="replace")\n'
            "        return None\n"
            '    namespace["web_read_source"] = web_read_source\n'
        )

    if "vault_lint" in allowed_tools:
        snippets.append(
            'if "vault_lint" in allowed:\n'
            "    def vault_lint(file_path):\n"
            "        p = Path(file_path)\n"
            "        if not p.exists():\n"
            '            return {"error": "file not found"}\n'
            '        text = p.read_text(encoding="utf-8", errors="replace")\n'
            "        issues = []\n"
            '        has_fm = text.startswith("---")\n'
            "        if not has_fm:\n"
            '            issues.append("missing frontmatter")\n'
            "        import re as _re\n"
            '        links = _re.findall(r"\\[\\[([^\\]]+)\\]\\]", text)\n'
            "        broken = []\n"
            "        vault = Path(vault_path)\n"
            "        # Build a stem map with a single pruned walk instead of\n"
            "        # one rglob per wikilink (O(vault) once, not O(n*vault)).\n"
            "        _stem_map = {}\n"
            "        for _root, _dirs, _files in os.walk(str(vault)):\n"
            "            _dirs[:] = [d for d in _dirs if d not in _IGNORED_DIRS]\n"
            "            for _f in _files:\n"
            '                if _f.endswith(".md"):\n'
            "                    _stem_map[Path(_f).stem] = Path(_root, _f)\n"
            "        for link in links:\n"
            "            link_stem = link.split(chr(124))[0]\n"
            "            if link_stem not in _stem_map:\n"
            "                broken.append(link)\n"
            "        if broken:\n"
            '            issues.append(f"{len(broken)} broken wikilinks: '
            '{broken[:5]}")\n'
            '        return {"has_frontmatter": has_fm, "broken_wikilinks": '
            'broken, "issues": issues}\n'
            '    namespace["vault_lint"] = vault_lint\n'
        )

    if "vault_append" in allowed_tools:
        snippets.append(
            'if "vault_append" in allowed:\n'
            "    def vault_append(file_path, content):\n"
            "        p = Path(file_path)\n"
            "        if not p.exists():\n"
            '            return {"error": "file not found"}\n'
            '        existing = p.read_text(encoding="utf-8")\n'
            '        p.write_text(existing + "\\n" + content, encoding="utf-8")\n'
            '        return {"appended": True, "chars_added": len(content)}\n'
            '    namespace["vault_append"] = vault_append\n'
        )

    if "vault_list" in allowed_tools:
        snippets.append(
            'if "vault_list" in allowed:\n'
            "    def vault_list(directory=None, tag=None):\n"
            "        vault = Path(vault_path)\n"
            "        if directory:\n"
            "            vault = vault / directory\n"
            "        results = []\n"
            "        for root, dirs, files in os.walk(str(vault)):\n"
            "            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]\n"
            "            for f in files:\n"
            '                if f.endswith(".md"):\n'
            "                    results.append(str(Path(root, f)))\n"
            "        return results\n"
            '    namespace["vault_list"] = vault_list\n'
        )

    if "code_read" in allowed_tools:
        snippets.append(
            'if "code_read" in allowed:\n'
            "    def code_read(file_path, start_line=None, end_line=None):\n"
            "        p = Path(file_path)\n"
            "        if not p.exists():\n"
            '            return {"error": "file not found"}\n'
            '        text = p.read_text(encoding="utf-8", errors="replace")\n'
            '        lines = text.split("\\n")\n'
            "        start_idx = (start_line or 1) - 1\n"
            "        end_idx = end_line if end_line is not None else None\n"
            "        lines = lines[start_idx:end_idx]\n"
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
            "    from subprocess_utils import run as _sp_run\n"
            "    import json as _json\n"
            '    _backend_dir = Path(os.environ.get("PYTHONPATH", ".").split('
            "os.pathsep)[0])\n"
            '    _venv_py = _backend_dir.parent / ".venv" / "Scripts" '
            '/ "python.exe"\n'
            "    if not _venv_py.exists():\n"
            "        _venv_py = Path(sys.executable)\n"
            '    _proc_self = os.environ.get("PROCEDURE_SELF_NAME", "")\n'
            '    _call_stack = _json.loads(os.environ.get("PROCEDURE_CALL_STACK", '
            '"[]"))\n'
            "    if _proc_self and _proc_self not in _call_stack:\n"
            "        _call_stack = _call_stack + [_proc_self]\n"
            "    def run_procedure(procedure_name, args=None):\n"
            '        """Run another procedure by note stem. Optionally pass a dict\n'
            "        of call-time arguments that the child reads via the injected\n"
            "        ``args`` variable. Returns a dict with {procedure,\n"
            "        overall_passed, steps_executed, final_output, child_procedures,\n"
            "        step_details}. Raises RuntimeError on cycle or depth exceeded\n"
            '        so the parent step fails loudly."""\n'
            '        cmd = [str(_venv_py), str(_backend_dir / "run_procedure.py"),\n'
            '               "--procedure-name", str(procedure_name),\n'
            '               "--vault-path", os.environ.get("VAULT_PATH", "."),\n'
            '               "--call-stack", _json.dumps(_call_stack),\n'
            '               "--procedure-args", _json.dumps(args or {}, default=str)]\n'
            "        # Forward the tracker log path so the child subprocess\n"
            "        # logs its own pass/fail + step results to the SAME log\n"
            "        # file (sub-procedure grading). See PROCEDURE_FIRST.\n"
            "        _child_env = dict(os.environ)\n"
            '        _tracker_log = os.environ.get("PROCEDURE_TRACKER_LOG", "")\n'
            "        if _tracker_log:\n"
            '            _child_env["PROCEDURE_TRACKER_LOG"] = _tracker_log\n'
            "        r = _sp_run(cmd, capture_output=True, text=True, timeout=300,\n"
            "                    env=_child_env)\n"
            "        if not r.stdout.strip():\n"
            '            raise RuntimeError("run_procedure produced no output; "\n'
            '                               "stderr: " + r.stderr[:500])\n'
            "        out = _json.loads(r.stdout)\n"
            '        if out.get("cycle_detected") or out.get("depth_exceeded"):\n'
            '            raise RuntimeError(out.get("error", "recursion error"))\n'
            '        if "error" in out and "overall_passed" not in out:\n'
            '            raise RuntimeError(out["error"])\n'
            "        return out\n"
            '    namespace["run_procedure"] = run_procedure\n'
        )

    if "vault_graph_analyzer" in allowed_tools:
        snippets.append(
            'if "vault_graph_analyzer" in allowed:\n'
            "    from custom_tools.vault_graph_analyzer import analyze_graph\n"
            "    def vault_graph_analyzer(exclude_patterns=None, max_hops=6):\n"
            "        result = analyze_graph(vault_path, exclude_patterns or "
            '["LICENSE.md"], max_hops)\n'
            '        return {"status": "success", "analysis": result}\n'
            '    namespace["vault_graph_analyzer"] = vault_graph_analyzer\n'
        )

    if "vault_delete" in allowed_tools:
        snippets.append(
            'if "vault_delete" in allowed:\n'
            "    from custom_tools.vault_delete import run as _vault_delete_run\n"
            "    def vault_delete(file_path):\n"
            '        return _vault_delete_run({"file_path": file_path})\n'
            '    namespace["vault_delete"] = vault_delete\n'
        )

    if "vault_safe_write" in allowed_tools:
        snippets.append(
            'if "vault_safe_write" in allowed:\n'
            "    from custom_tools.vault_safe_write import run as "
            "_vault_safe_write_run\n"
            "    def vault_safe_write(file_path, content):\n"
            '        return _vault_safe_write_run({"file_path": file_path, '
            '"content": content})\n'
            '    namespace["vault_safe_write"] = vault_safe_write\n'
        )

    if "vault_gaps" in allowed_tools:
        snippets.append(
            'if "vault_gaps" in allowed:\n'
            "    from knowledge_curriculum import KnowledgeCurriculum\n"
            "    from vault_graph import VaultGraph\n"
            "    _graph = VaultGraph(vault_path)\n"
            "    _curriculum = KnowledgeCurriculum(\n"
            "        vault_graph=_graph,\n"
            "        session_logger=None,\n"
            "    )\n"
            "    def vault_gaps():\n"
            "        gaps = _curriculum.propose_next_gaps(n=20)\n"
            '        return {"gaps": gaps, "count": len(gaps)}\n'
            '    namespace["vault_gaps"] = vault_gaps\n'
        )

    if "machine_spec" in allowed_tools:
        snippets.append(
            'if "machine_spec" in allowed:\n'
            "    import platform, psutil\n"
            "    def machine_spec(args=None):\n"
            "        try:\n"
            "            cpu = psutil.cpu_count(logical=True)\n"
            "            phys = psutil.cpu_count(logical=False)\n"
            "            ram = psutil.virtual_memory()\n"
            "            ram_gb = round(ram.total / (1024**3), 1)\n"
            '            gpu_info = "unknown"\n'
            "            try:\n"
            "                import subprocess as _sp\n"
            '                nvidia = _sp.run(["nvidia-smi", "--query-gpu=name", '
            '"--format=csv,noheader"],\n'
            "                                 capture_output=True, text=True, "
            "timeout=5)\n"
            "                if nvidia.returncode == 0 and nvidia.stdout.strip():\n"
            "                    gpu_info = nvidia.stdout.strip()\n"
            "            except Exception:\n"
            "                pass\n"
            "            return {\n"
            '                "cpu_cores": cpu,\n'
            '                "cpu_physical": phys,\n'
            '                "ram_gb": ram_gb,\n'
            '                "gpu": gpu_info,\n'
            '                "platform": platform.platform(),\n'
            "            }\n"
            "        except Exception as _e:\n"
            '            return {"error": str(_e)}\n'
            '    namespace["machine_spec"] = machine_spec\n'
        )

    if "ollama_model_search" in allowed_tools:
        snippets.append(
            'if "ollama_model_search" in allowed:\n'
            "    import subprocess as _sp\n"
            "    def ollama_model_search(args=None):\n"
            '        action = (args or {}).get("action", "installed")\n'
            "        try:\n"
            '            r = _sp.run(["ollama", "list"], capture_output=True, '
            "text=True, timeout=10)\n"
            "            if r.returncode == 0:\n"
            '                lines = r.stdout.strip().split("\\n")[1:]  # skip header\n'
            "                models = []\n"
            "                for line in lines:\n"
            "                    parts = line.split()\n"
            "                    if parts:\n"
            "                        models.append(parts[0])\n"
            '                return {"action": action, "models": models, '
            '"count": len(models)}\n'
            '            return {"error": r.stderr.strip()}\n'
            "        except Exception as _e:\n"
            '            return {"error": str(_e)}\n'
            '    namespace["ollama_model_search"] = ollama_model_search\n'
        )

    if "vaultbot_status" in allowed_tools:
        snippets.append(
            'if "vaultbot_status" in allowed:\n'
            "    def vaultbot_status(args=None):\n"
            "        try:\n"
            '            status = {"background_researcher": "unknown"}\n'
            "            # Check for researcher lock file\n"
            '            lock = Path(vault_path) / "vaultbot" / "Memory" / '
            '".researcher_lock"\n'
            "            if lock.exists():\n"
            '                status["background_researcher"] = "running"\n'
            "            else:\n"
            '                status["background_researcher"] = "idle"\n'
            "            return status\n"
            "        except Exception as _e:\n"
            '            return {"error": str(_e)}\n'
            '    namespace["vaultbot_status"] = vaultbot_status\n'
        )

    if "vault_research" in allowed_tools:
        snippets.append(
            'if "vault_research" in allowed:\n'
            "    import requests as _requests\n"
            '    def vault_research(topic, depth="deep", source_allowlist=None, '
            "source_denylist=None):\n"
            '        """Research a topic via the backend research engine.\n'
            "        Calls the /research_tool HTTP endpoint so the procedure\n"
            "        subprocess does not need to import the full engine.\n"
            "        Returns a dict with synthesis, sources, and note_path.\n"
            "        source_allowlist restricts sources to specific domains\n"
            "        (e.g. ['docs.python.org']) for authoritative-only digs.\n"
            '        On failure, returns {"error": ...} for graceful degradation.\n'
            '        """\n'
            "        try:\n"
            '            _payload = {"topic": topic, "depth": depth}\n'
            "            if source_allowlist:\n"
            '                _payload["source_allowlist"] = source_allowlist\n'
            "            if source_denylist:\n"
            '                _payload["source_denylist"] = source_denylist\n'
            "            _headers = {}\n"
            "            try:\n"
            "                from auth import read_token as _read_token\n"
            '                _tok = _read_token()\n'
            "                if _tok:\n"
            '                    _headers["X-VaultBot-Token"] = _tok\n'
            "            except Exception:\n"
            "                pass\n"
            "            resp = _requests.post(\n"
            '                "http://localhost:8000/research_tool",\n'
            "                json=_payload,\n"
            "                headers=_headers,\n"
            "                timeout=120\n"
            "            )\n"
            "            resp.raise_for_status()\n"
            "            return resp.json()\n"
            "        except Exception as _e:\n"
            '            return {"error": str(_e), "topic": topic}\n'
            '    namespace["vault_research"] = vault_research\n'
        )

    result = "\n".join(snippets)
    _PREAMBLE_CACHE[cache_key] = result
    return result
