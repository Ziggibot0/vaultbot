---\ntype: procedure
status: verified
created: 2026-07-31
description: "Remove all masking fallbacks from the framework. 10 fixes that replace silent except-return-empty patterns with loud failures."
when: "When the framework has silent try-except-return-empty patterns that mask failures"
allowed_tools: [code_read]
---

# Remove-Masking-Fallbacks

Remove all masking fallbacks from the framework. Each step is a self-contained Python code block that reads the target file, verifies the old string exists, replaces it with the new string, validates syntax with py_compile, and writes the result back. If any step fails, it fails loud — no silent degradation.

## Steps

1. ```python
   import py_compile, tempfile, os, shutil
   f = "vaultbot_backend/fused_retrieval.py"
   content = open(f, encoding="utf-8").read()
   old = '        except Exception as e:\n            self._log("vector.error", f"{type(e).__name__}: {e}")\n            return [], {}'
   new = '        except Exception:\n            raise  # FAIL LOUD: vector search failure must surface, not return empty'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   # Validate syntax
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 2a applied: {f} — vector search except now re-raises"
   ```

2. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/fused_retrieval.py"
   content = open(f, encoding="utf-8").read()
   old = '        except Exception as e:\n            self._log("graph.error", f"{type(e).__name__}: {e}")\n        return candidates'
   new = '        except Exception:\n            raise  # FAIL LOUD: graph channel failure must surface'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 2b applied: {f} — graph channel except now re-raises"
   ```

3. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/fused_retrieval.py"
   content = open(f, encoding="utf-8").read()
   old = '        except Exception as e:\n            self._log("backlink.error", f"{type(e).__name__}: {e}")\n        return candidates'
   new = '        except Exception:\n            raise  # FAIL LOUD: backlink channel failure must surface'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 2c applied: {f} — backlink channel except now re-raises"
   ```

4. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/fused_retrieval.py"
   content = open(f, encoding="utf-8").read()
   # Fix _safe_neighbors — remove try/except, let it raise
   old = '    def _safe_neighbors(self, name: str, direction: str = "both") -> list[str]:\n        """Call vault_graph.neighbors but never raise."""\n        try:\n            norm = self._normalize_name(name)\n            if not norm:\n                return []\n            return list(self.vault_graph.neighbors(norm, direction=direction) or [])\n        except Exception as e:\n            self._log("neighbors.error", f"{type(e).__name__}: {e}")\n            return []'
   new = '    def _safe_neighbors(self, name: str, direction: str = "both") -> list[str]:\n        """Call vault_graph.neighbors. Raises on failure — no silent empty return."""\n        norm = self._normalize_name(name)\n        if not norm:\n            return []\n        return list(self.vault_graph.neighbors(norm, direction=direction) or [])'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   # Fix _file_path_for_node — remove try/except, let it raise
   old2 = '    def _file_path_for_node(self, name: str) -> str:\n        """Resolve a normalized graph node name to its file_path."""\n        try:\n            node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))\n            if node and node.get("file_path"):\n                return node["file_path"]\n        except Exception as e:\n            self._log("resolve.error", f"{type(e).__name__}: {e}")\n        return ""'
   new2 = '    def _file_path_for_node(self, name: str) -> str:\n        """Resolve a normalized graph node name to its file_path. Raises on failure."""\n        node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))\n        if node and node.get("file_path"):\n            return node["file_path"]\n        return ""'
   assert old2 in content, f"OLD STRING 2 NOT FOUND in {f}"
   content = content.replace(old2, new2, 1)
   # Fix _content_for_node — remove try/except, let it raise
   old3 = '    def _content_for_node(self, name: str) -> str:\n        """Fetch the stored content for a graph node."""\n        try:\n            node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))\n            if node:\n                return node.get("content", "") or ""\n        except Exception as e:\n            self._log("content.error", f"{type(e).__name__}: {e}")\n        return ""'
   new3 = '    def _content_for_node(self, name: str) -> str:\n        """Fetch the stored content for a graph node. Raises on failure."""\n        node = (self.vault_graph.nodes or {}).get(self._normalize_name(name))\n        if node:\n            return node.get("content", "") or ""\n        return ""'
   assert old3 in content, f"OLD STRING 3 NOT FOUND in {f}"
   content = content.replace(old3, new3, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 2d-f applied: {f} — _safe_neighbors, _file_path_for_node, _content_for_node all re-raise now"
   ```

5. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/fused_retrieval.py"
   content = open(f, encoding="utf-8").read()
   old = '    def _name_from_hit(self, hit: dict[str, Any], fp: str) -> str:\n        """Get the normalized name from a vector hit, falling back to the graph."""\n        name = hit.get("name")\n        if name:\n            return self._normalize_name(name)\n        try:\n            for n, node in (self.vault_graph.nodes or {}).items():\n                if node.get'
   new = '    def _name_from_hit(self, hit: dict[str, Any], fp: str) -> str:\n        """Get the normalized name from a vector hit, or from the graph."""\n        name = hit.get("name")\n        if name:\n            return self._normalize_name(name)\n        for n, node in (self.vault_graph.nodes or {}).items():\n            if node.get'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   # Also remove the except block at the end of _name_from_hit
   old2 = '        except Exception as e:\n            self._log("name_from_hit.error", f"{type(e).__name__}: {e}")\n        return ""'
   new2 = '        return ""'
   assert old2 in content, f"OLD STRING 2 NOT FOUND in {f}"
   content = content.replace(old2, new2, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 2g applied: {f} — _name_from_hit re-raises"
   ```

6. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/compactor.py"
   content = open(f, encoding="utf-8").read()
   old = '            except Exception as exc:\n                logger.warning("summarization failed, falling back to extractive: %s", exc)\n                try:\n                    self._log_exc(exc, context="summarize_middle")\n                except Exception:\n                    pass\n                summary = self._extractive_summary(middle)'
   new = '            except Exception as exc:\n                logger.error("summarization failed — FAILING LOUD, no fallback to extractive: %s", exc)\n                raise'
   assert old in content, f"OLD STRING NOT FOUND in {f}"
   content = content.replace(old, new, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 6 applied: {f} — compaction failure now raises instead of falling back to extractive"
   ```

7. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/llm_client.py"
   content = open(f, encoding="utf-8").read()
   # Fix 1: log_tool_call except: pass
   old1 = '        except Exception:\n            pass\n\n    # -- LLMClient surface -------------------------------------------------'
   new1 = '        except Exception as e:\n            raise RuntimeError(f"log_tool_call failed: {e}") from e\n\n    # -- LLMClient surface -------------------------------------------------'
   assert old1 in content, f"OLD STRING 1 NOT FOUND in {f}"
   content = content.replace(old1, new1, 1)
   # Fix 2: model_changed except: pass
   old2 = '            try:\n                self.session_logger.log("model_changed", {"model": model})\n            except Exception:\n                pass'
   new2 = '            self.session_logger.log("model_changed", {"model": model})'
   assert old2 in content, f"OLD STRING 2 NOT FOUND in {f}"
   content = content.replace(old2, new2, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 9 applied: {f} — log_tool_call and model_changed except:pass removed, now re-raises"
   ```

8. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/chat_handler.py"
   content = open(f, encoding="utf-8").read()
   # Fix checkpoint save — remove try/except, let it raise
   old1 = '                try:\n                    _cp.save({\n                        "user_message": user_message,\n                        "round_idx": round_idx,\n                        "accumulated": final_answer,\n                        "thinking": thinking_text,\n                        "tool_history": _turn_tool_history,\n                        "working_memory": snapshot_working_memory(wm),\n                    })\n                except Exception as e:\n                    session_logger.log("chat_checkpoint_save_failed", {"error": str(e)})'
   new1 = '                _cp.save({\n                    "user_message": user_message,\n                    "round_idx": round_idx,\n                    "accumulated": final_answer,\n                    "thinking": thinking_text,\n                    "tool_history": _turn_tool_history,\n                    "working_memory": snapshot_working_memory(wm),\n                })'
   assert old1 in content, f"OLD STRING 1 NOT FOUND in {f}"
   content = content.replace(old1, new1, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 7a applied: {f} — checkpoint save except removed, now raises on failure"
   ```

9. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/chat_handler.py"
   content = open(f, encoding="utf-8").read()
   # Fix history persist — remove try/except, let it raise
   old1 = '        except Exception as e:\n            session_logger.log("history_persist_failed", {"error": str(e)})'
   new1 = '        except Exception as e:\n            raise RuntimeError(f"history persist failed: {e}") from e'
   assert old1 in content, f"OLD STRING 1 NOT FOUND in {f}"
   content = content.replace(old1, new1, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 7b applied: {f} — history persist except now re-raises"
   ```

10. ```python
   import py_compile, tempfile, os
   f = "vaultbot_backend/chat_handler.py"
   content = open(f, encoding="utf-8").read()
   # Fix partial cleanup — remove try/except, let it raise
   old1 = '                try:\n                    if partial_path.exists():\n                        partial_path.unlink()\n                except Exception as e:\n                    session_logger.log("partial_cleanup_failed", {"error": str(e)})'
   new1 = '                if partial_path.exists():\n                    partial_path.unlink()'
   assert old1 in content, f"OLD STRING 1 NOT FOUND in {f}"
   content = content.replace(old1, new1, 1)
   # Fix chat note creation — remove try/except, let it raise
   old2 = '            try:\n                note_path = await loop.run_in_executor(None, svc.note_creator.create_note_from_chat, user_message, final_answer, thinking_text)\n                session_logger.log("chat_note_created", {"note_path": note_path})\n            except Exception as e:\n                session_logger.log_exception(e, context="note_creator.create_note_from_chat")\n                print(f"Error creating chat note: {e}")'
   new2 = '            note_path = await loop.run_in_executor(None, svc.note_creator.create_note_from_chat, user_message, final_answer, thinking_text)\n            session_logger.log("chat_note_created", {"note_path": note_path})'
   assert old2 in content, f"OLD STRING 2 NOT FOUND in {f}"
   content = content.replace(old2, new2, 1)
   tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
   tmp.write(content)
   tmp.close()
   py_compile.compile(tmp.name, doraise=True)
   os.unlink(tmp.name)
   open(f, "w", encoding="utf-8").write(content)
   result = f"Fix 10 applied: {f} — partial cleanup and chat note creation except removed, now raise on failure"
   ```