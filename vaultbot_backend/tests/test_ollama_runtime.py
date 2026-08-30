"""Direct tests for Ollama runtime probes and model preloading."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ollama_runtime import OllamaRuntime

pytestmark = pytest.mark.unit


def _response(data=None, error=None):
    response = MagicMock()
    response.json.return_value = data or {}
    response.raise_for_status.side_effect = error
    return response


def _owner(model="qwen:7b", session=None, session_logger=None):
    return SimpleNamespace(
        base_url="http://ollama.test",
        llm_model=model,
        session_logger=session_logger,
        _keep_alive="30m",
        _session=session or MagicMock(),
        _log_tool=MagicMock(),
    )


@pytest.mark.parametrize(
    ("requested", "models", "expected"),
    [
        ("qwen:7b", [{"name": "qwen:7b"}], True),
        ("qwen", [{"model": "qwen:latest"}], True),
        ("llama:8b", [{"name": "qwen:7b"}], False),
    ],
)
def test_is_model_loaded_local_and_prefix(requested, models, expected):
    owner = _owner()
    owner._session.get.return_value = _response({"models": models})

    assert OllamaRuntime(owner).is_model_loaded(requested) is expected


def test_is_model_loaded_cloud_bypasses_session():
    owner = _owner(model="glm:cloud")

    assert OllamaRuntime(owner).is_model_loaded() is True
    owner._session.get.assert_not_called()


def test_runtime_dereferences_replaced_owner_session():
    original_session = MagicMock()
    owner = _owner(session=original_session)
    runtime = OllamaRuntime(owner)
    replacement_session = MagicMock()
    replacement_session.get.return_value = _response({"models": [{"name": "qwen:7b"}]})
    owner._session = replacement_session

    assert runtime.is_model_loaded() is True
    original_session.get.assert_not_called()
    replacement_session.get.assert_called_once()


def test_preload_applies_context_cap_and_logs_with_shared_session(monkeypatch):
    monkeypatch.setenv("VAULTBOT_NUM_CTX_CAP", "32768")
    session = MagicMock()
    session.get.side_effect = [
        _response({"models": []}),
        _response({"models": [{"name": "qwen:7b"}]}),
    ]
    session.post.side_effect = [
        _response({"model_info": {"qwen.context_length": 262144}}),
        _response({"done": True}),
    ]
    session_logger = MagicMock()
    owner = _owner(session=session, session_logger=session_logger)

    assert OllamaRuntime(owner).preload_model(keep_alive="2h") is True

    generate_call = session.post.call_args_list[1]
    assert generate_call.args == ("http://ollama.test/api/generate",)
    assert generate_call.kwargs["json"] == {
        "model": "qwen:7b",
        "prompt": "",
        "stream": False,
        "options": {"num_predict": 1, "temperature": 0, "num_ctx": 32768},
        "keep_alive": "2h",
    }
    session_logger.log.assert_called_once()
    assert session_logger.log.call_args.args[0] == "model_preloaded"


def test_context_window_positive_cache_avoids_second_probe():
    owner = _owner(model="positive-cache-test-model")
    owner._session.post.return_value = _response(
        {"model_info": {"qwen.context_length": 65536}}
    )
    runtime = OllamaRuntime(owner)

    assert runtime.context_window() == 65536
    assert runtime.context_window() == 65536
    owner._session.post.assert_called_once()


def test_context_window_cache_is_shared_across_runtime_instances():
    model = "shared-cache-test-model"
    first_owner = _owner(model=model)
    first_owner._session.post.return_value = _response(
        {"model_info": {"qwen.context_length": 49152}}
    )
    second_owner = _owner(model=model)

    assert OllamaRuntime(first_owner).context_window() == 49152
    assert OllamaRuntime(second_owner).context_window() == 49152
    first_owner._session.post.assert_called_once()
    second_owner._session.post.assert_not_called()


def test_context_window_negative_cache_retries_after_ttl(monkeypatch):
    monkeypatch.setenv("VAULTBOT_CTX_PROBE_FAIL_TTL", "10")
    clock = iter([100.0, 105.0, 111.0])
    monkeypatch.setattr("ollama_runtime.time.monotonic", lambda: next(clock))
    owner = _owner(model="negative-cache-ttl-test-model")
    owner._session.post.side_effect = [
        _response(error=ConnectionError("down")),
        _response({"model_info": {"qwen.context_length": 32768}}),
    ]
    runtime = OllamaRuntime(owner)

    with pytest.raises(ConnectionError, match="down"):
        runtime.context_window()
    with pytest.raises(RuntimeError, match="negative-cached"):
        runtime.context_window()
    assert runtime.context_window() == 32768
    assert owner._session.post.call_count == 2


def test_capabilities_parse_vision_and_embed_and_surface_errors():
    owner = _owner()
    owner._session.post.side_effect = [
        _response({"model_info": {"clip.vision.block_count": 24}}),
        _response(error=ConnectionError("refused")),
    ]
    runtime = OllamaRuntime(owner)

    assert runtime.get_model_capabilities("image-embed") == {
        "vision": True,
        "instruct": False,
    }
    with pytest.raises(ConnectionError, match="refused"):
        runtime.get_model_capabilities("qwen:7b")
    owner._log_tool.assert_called_once_with(
        "get_model_capabilities", {"model": "qwen:7b"}, error="refused"
    )
