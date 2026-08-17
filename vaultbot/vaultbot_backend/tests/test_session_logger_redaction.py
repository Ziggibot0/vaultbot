"""Test that SessionLogger redacts secret-shaped values before writing to disk.

Covers two signals:
  1. A dict KEY whose name ends with a secret suffix (api_key, token, ...).
  2. A string VALUE that matches a known provider key shape (sk-..., tvly-...).

Over-redacts rather than under-redacts. A false positive only replaces a log
field with [REDACTED], never breaks the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


from session_logger import SessionLogger, _redact


def test_redact_dict_key_with_secret_suffix():
    assert _redact({"api_key": "sk-real-key-here-12345"}) == {"api_key": "[REDACTED]"}
    assert _redact({"LLM_API_KEY": "x"}) == {"LLM_API_KEY": "[REDACTED]"}
    assert _redact({"secret": "x"}) == {"secret": "[REDACTED]"}
    assert _redact({"token": "x"}) == {"token": "[REDACTED]"}
    assert _redact({"password": "x"}) == {"password": "[REDACTED]"}


def test_redact_preserves_non_secret_keys():
    assert _redact({"model": "qwen3.6:27b"}) == {"model": "qwen3.6:27b"}
    assert _redact({"round": 3}) == {"round": 3}
    assert _redact({"detail": "some text"}) == {"detail": "some text"}


def test_redact_provider_key_values():
    assert _redact({"model": "sk-abcdef1234567890"}) == {"model": "[REDACTED]"}
    assert _redact({"x": "tvly-abcdef1234567890xyz"}) == {"x": "[REDACTED]"}
    assert _redact({"x": "sk-or-v1-abcdef1234567890xyz"}) == {"x": "[REDACTED]"}


def test_redact_preserves_normal_strings():
    assert _redact({"x": "hello world"}) == {"x": "hello world"}
    assert _redact({"x": "short"}) == {"x": "short"}


def test_redact_nested():
    obj = {"outer": {"api_key": "sk-secret-1234567890", "ok": "keep"}}
    assert _redact(obj) == {"outer": {"api_key": "[REDACTED]", "ok": "keep"}}


def test_redact_list():
    obj = [{"api_key": "sk-x-1234567890"}, {"model": "qwen"}]
    out = _redact(obj)
    assert out[0]["api_key"] == "[REDACTED]"
    assert out[1]["model"] == "qwen"


def test_logger_writes_redacted_to_disk(tmp_path: Path):
    s = SessionLogger(log_dir=str(tmp_path))
    s.log("test", {"api_key": "sk-secret-1234567890abcdef", "model": "qwen3.6:27b"})
    s.close()
    line = s._file_path.read_text(encoding="utf-8").splitlines()[
        1
    ]  # [0] is session_start
    record = json.loads(line)
    assert record["data"]["api_key"] == "[REDACTED]"
    assert record["data"]["model"] == "qwen3.6:27b"
