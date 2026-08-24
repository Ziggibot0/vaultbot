from __future__ import annotations

import pytest
from procedure_step_executor import _run_llm_step
from procedure_types import Step

pytestmark = pytest.mark.unit


class FakeClient:
    def __init__(self) -> None:
        self.messages = None

    def chat(self, messages, temperature=0.0, stream=False, think=False):
        self.messages = messages
        return {"response": "{}"}


def test_llm_step_interpolates_procedure_args():
    client = FakeClient()
    step = Step(
        number=1.0,
        instruction="Classify",
        step_type="llm",
        llm_instruction="User request: {{ intent }}",
    )

    ok, output, error = _run_llm_step(
        step,
        {},
        client,
        procedure_args={"intent": "pick an issue and submit a PR"},
    )

    assert ok is True
    assert output == "{}"
    assert error is None
    assert client.messages is not None
    assert (
        "User request: pick an issue and submit a PR" in client.messages[1]["content"]
    )
    assert "{{ intent }}" not in client.messages[1]["content"]
