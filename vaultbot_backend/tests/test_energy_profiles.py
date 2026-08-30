from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from energy_profiles import EnergyProfileStore, validate_energy_profile


def test_store_round_trip_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "energy_profiles.json"
    store = EnergyProfileStore(path)
    saved = store.set(
        "ollama-local:qwen3:4b",
        {
            "wh_per_1k_input_tokens": 0.2,
            "wh_per_1k_output_tokens": 0.5,
            "source_note": "Measured at the wall",
        },
    )
    assert saved["updated_at"]
    assert (
        store.load()["ollama-local:qwen3:4b"]["source_note"] == "Measured at the wall"
    )
    assert store.delete("ollama-local:qwen3:4b") is True
    assert store.load() == {}
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("value", [-1, float("inf"), "not-a-number"])
def test_profile_rejects_invalid_coefficients(value: object) -> None:
    with pytest.raises(ValueError):
        validate_energy_profile(
            {
                "wh_per_1k_input_tokens": value,
                "wh_per_1k_output_tokens": 1,
            }
        )


def test_corrupt_file_is_not_silently_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "energy_profiles.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="could not load energy profiles"):
        EnergyProfileStore(path).load()


def test_file_contains_no_default_profiles(tmp_path: Path) -> None:
    path = tmp_path / "energy_profiles.json"
    EnergyProfileStore(path).set(
        "provider:model",
        {"wh_per_1k_input_tokens": None, "wh_per_1k_output_tokens": None},
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["profiles"]) == ["provider:model"]
