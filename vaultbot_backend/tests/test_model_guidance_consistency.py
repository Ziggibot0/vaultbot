"""Keep local chat and small-model guidance aligned across install surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_file(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_local_chat_default_is_consistent() -> None:
    setup_ps1 = _read_repo_file("setup.ps1")
    setup_sh = _read_repo_file("setup.sh")
    readme = _read_repo_file("README.md")
    contributing = _read_repo_file("CONTRIBUTING.md")

    assert '$chatModel = "qwen3:latest"' in setup_ps1
    assert 'CHAT_MODEL="${CHAT_MODEL:-qwen3:latest}"' in setup_sh
    assert "ollama pull\n> qwen3:latest" in readme
    assert "ollama pull qwen3:latest qwen3.5:4b nomic-embed-text" in contributing


def test_small_model_guidance_remains_independent() -> None:
    setup_ps1 = _read_repo_file("setup.ps1")
    setup_sh = _read_repo_file("setup.sh")
    env_example = _read_repo_file(".env.example")

    assert "ollama pull qwen3.5:4b" in setup_ps1
    assert "ollama pull qwen3.5:4b" in setup_sh
    assert "SMALL_MODEL=qwen3.5:4b" in env_example
