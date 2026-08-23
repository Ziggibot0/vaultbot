"""Regression tests for project_for_provider (session eb8143f7 fix).

The degenerate looping + 2-minute latency in session eb8143f7 was caused
by ``thinking`` and other non-spec fields leaking into the messages sent
to Ollama. ``project_for_provider`` is the universal fix: strip everything
that isn't part of the OpenAI message spec before sending to ANY provider.
"""

from chat_context import project_for_provider, sanitize_tool_history

# ── Native projection (all providers except glm-via-Ollama) ──────────────


def test_native_strips_thinking():
    """thinking must never reach the provider — it corrupts generation."""
    conv = [
        {"role": "assistant", "content": "hello", "thinking": "I think..."},
        {"role": "user", "content": "hi"},
    ]
    projected = project_for_provider(conv)
    for m in projected:
        assert "thinking" not in m, f"thinking leaked: {m}"


def test_native_strips_timestamp():
    """timestamp is internal bookkeeping, not a spec field."""
    conv = [
        {"role": "user", "content": "hi", "timestamp": 12345.0},
        {"role": "assistant", "content": "hello", "timestamp": 12346.0},
    ]
    projected = project_for_provider(conv)
    for m in projected:
        assert "timestamp" not in m, f"timestamp leaked: {m}"


def test_native_strips_digested_fields():
    """digested / original_chars are code_read bookkeeping."""
    conv = [
        {"role": "tool", "content": "x", "digested": True, "original_chars": 500},
    ]
    projected = project_for_provider(conv)
    for m in projected:
        assert "digested" not in m
        assert "original_chars" not in m


def test_native_keeps_tool_calls():
    """Native protocol: tool_calls must survive (model needs them)."""
    conv = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "vault_search",
                        "arguments": "{}",
                    },
                }
            ],
            "thinking": "I should search",
        },
        {
            "role": "tool",
            "content": "result",
            "tool_call_id": "call_1",
            "tool_name": "vault_search",
        },
    ]
    projected = project_for_provider(conv)
    assistant = projected[0]
    assert "tool_calls" in assistant, "tool_calls must survive in native mode"
    assert "thinking" not in assistant
    tool = projected[1]
    assert tool["role"] == "tool", "tool role must survive in native mode"
    assert tool["tool_call_id"] == "call_1"


def test_native_strips_tool_name():
    """tool_name is internal; the provider uses tool_call_id for pairing."""
    conv = [
        {
            "role": "tool",
            "content": "result",
            "tool_call_id": "c1",
            "tool_name": "vault_search",
        },
    ]
    projected = project_for_provider(conv)
    assert "tool_name" not in projected[0]


def test_native_preserves_system_content():
    """System messages must pass through with content intact."""
    conv = [{"role": "system", "content": "You are VaultBot."}]
    projected = project_for_provider(conv)
    assert projected[0]["role"] == "system"
    assert projected[0]["content"] == "You are VaultBot."


def test_native_empty_content():
    """Assistant messages with empty content (tool-only rounds) pass through."""
    conv = [{"role": "assistant", "content": "", "thinking": "hmm"}]
    projected = project_for_provider(conv)
    assert projected[0]["content"] == ""
    assert "thinking" not in projected[0]


def test_native_does_not_mutate_original():
    """The original conversation must not be modified."""
    conv = [
        {"role": "assistant", "content": "hi", "thinking": "thought", "timestamp": 1.0},
    ]
    project_for_provider(conv)
    assert conv[0]["thinking"] == "thought", "original was mutated!"
    assert conv[0]["timestamp"] == 1.0, "original was mutated!"


# ── Flattened projection (glm-via-Ollama) ────────────────────────────────


def test_flattened_removes_tool_role():
    """glm returns empty on tool-role messages; flatten to system."""
    conv = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "vault_search",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "result",
            "tool_call_id": "c1",
            "tool_name": "vault_search",
        },
    ]
    projected = project_for_provider(conv, flatten_tool_calls=True)
    for m in projected:
        assert m["role"] != "tool", f"tool role survived flattening: {m}"


def test_flattened_strips_tool_calls():
    """glm returns empty on tool_calls; strip them."""
    conv = [
        {
            "role": "assistant",
            "content": "text",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "vault_search",
                        "arguments": "{}",
                    },
                }
            ],
        },
    ]
    projected = project_for_provider(conv, flatten_tool_calls=True)
    assert "tool_calls" not in projected[0]


def test_flattened_strips_thinking():
    """Flattened mode must also strip thinking."""
    conv = [
        {
            "role": "assistant",
            "content": "x",
            "thinking": "hmm",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
        },
    ]
    projected = project_for_provider(conv, flatten_tool_calls=True)
    assert "thinking" not in projected[0]


def test_flattened_merges_consecutive_tool_results():
    """Multiple tool results merge into one system message."""
    conv = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "t1", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "t2", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "content": "r1", "tool_call_id": "c1", "tool_name": "t1"},
        {"role": "tool", "content": "r2", "tool_call_id": "c2", "tool_name": "t2"},
    ]
    projected = project_for_provider(conv, flatten_tool_calls=True)
    system_msgs = [m for m in projected if m["role"] == "system"]
    assert len(system_msgs) == 1, "tool results should merge into one system msg"
    assert "r1" in system_msgs[0]["content"]
    assert "r2" in system_msgs[0]["content"]


# ── Backward compat ──────────────────────────────────────────────────────


def test_sanitize_tool_history_alias():
    """sanitize_tool_history must be an alias for project_for_provider(flatten=True)."""
    conv = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "r", "tool_call_id": "c1", "tool_name": "t"},
    ]
    via_alias = sanitize_tool_history(conv)
    via_direct = project_for_provider(conv, flatten_tool_calls=True)
    assert via_alias == via_direct
