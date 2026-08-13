"""Tests for GET /config/effective — the config source-of-truth endpoint.

Verifies that the endpoint correctly:
  - reports .env file values as source="env_file"
  - reports runtime overrides as source="runtime"
  - flags conflicts when .env and process env disagree
  - never leaks secret values (reports has_value, not the value)
  - handles missing .env gracefully (source="default" for unset keys)

Run: pytest tests/test_config_effective.py -v
"""

from __future__ import annotations

from unittest.mock import patch


class TestConfigEffective:
    """Test the /config/effective endpoint logic directly."""

    def _call_endpoint(self, env_file_content: str | None, process_env: dict[str, str]):
        """Call config_effective() with a mocked .env + process env.

        Returns the list of config items (dicts) for assertion.
        """
        from routers.llm import config_effective

        # Mock os.getenv to return values from our process_env dict.
        def fake_getenv(key, default=""):
            return process_env.get(key, default)

        # Mock _read_env_file to return parsed env_file_content.
        def fake_read_env_file():
            if env_file_content is None:
                return {}
            result = {}
            for line in env_file_content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
            return result

        with (
            patch("routers.llm.os.getenv", side_effect=fake_getenv),
            patch("routers.llm._read_env_file", side_effect=fake_read_env_file),
        ):
            import asyncio

            result = asyncio.run(config_effective())
        return result["config"]

    def test_env_file_source(self):
        """A key set in .env and matching process env → source=env_file."""
        items = self._call_endpoint(
            env_file_content="VAULTBOT_OWNER=testuser\n",
            process_env={"VAULTBOT_OWNER": "testuser"},
        )
        owner = next(i for i in items if i["key"] == "VAULTBOT_OWNER")
        assert owner["value"] == "testuser"
        assert owner["source"] == "env_file"
        assert owner["conflict"] is False

    def test_runtime_override_source(self):
        """Key in process env but NOT in .env → source=runtime."""
        items = self._call_endpoint(
            env_file_content=None,
            process_env={"LLM_MODEL": "gpt-4o-mini"},
        )
        item = next(i for i in items if i["key"] == "LLM_MODEL")
        assert item["value"] == "gpt-4o-mini"
        assert item["source"] == "runtime"
        assert item["conflict"] is False

    def test_conflict_detected(self):
        """Key differs between .env and process env → conflict=True."""
        items = self._call_endpoint(
            env_file_content="LLM_MODEL=gpt-4o-mini\n",
            process_env={"LLM_MODEL": "claude-3-opus"},
        )
        item = next(i for i in items if i["key"] == "LLM_MODEL")
        assert item["value"] == "claude-3-opus"  # process env wins
        assert item["source"] == "runtime"
        assert item["conflict"] is True

    def test_secret_never_leaked(self):
        """API keys report has_value, not the actual value."""
        items = self._call_endpoint(
            env_file_content="LLM_API_KEY=sk-secret-12345\n",
            process_env={"LLM_API_KEY": "sk-secret-12345"},
        )
        item = next(i for i in items if i["key"] == "LLM_API_KEY")
        assert item["is_secret"] is True
        assert item["value"] == ""  # never the actual key
        assert item["has_value"] is True

    def test_default_for_unset(self):
        """A key set nowhere → source=default, value empty."""
        items = self._call_endpoint(
            env_file_content=None,
            process_env={},
        )
        item = next(i for i in items if i["key"] == "TAVILY_API_KEY")
        assert item["value"] == ""
        assert item["source"] == "default"
        assert item["has_value"] is False

    def test_all_expected_keys_present(self):
        """The response includes all keys from _CONFIG_KEYS."""
        items = self._call_endpoint(env_file_content=None, process_env={})
        keys = {i["key"] for i in items}
        expected = {
            "VAULTBOT_OWNER",
            "LLM_BACKEND",
            "OLLAMA_LLM_MODEL",
            "OLLAMA_EMBED_MODEL",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "LLM_MODEL",
            "VAULTBOT_RESEARCH_BACKEND",
            "TAVILY_API_KEY",
            "OLLAMA_HOST",
        }
        assert keys == expected
