"""User-supplied energy coefficients for registered LLM models."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paths import FRAMEWORK_ROOT

DEFAULT_ENERGY_PROFILES_PATH = FRAMEWORK_ROOT / "energy_profiles.json"


def validate_energy_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized profile or raise ``ValueError``."""
    normalized: dict[str, Any] = {}
    for field in ("wh_per_1k_input_tokens", "wh_per_1k_output_tokens"):
        value = profile.get(field)
        if value is None:
            normalized[field] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a number or null") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{field} must be finite and non-negative")
        normalized[field] = number
    note = str(profile.get("source_note") or "").strip()
    if len(note) > 500:
        raise ValueError("source_note must be at most 500 characters")
    normalized["source_note"] = note
    normalized["updated_at"] = datetime.now(UTC).isoformat()
    return normalized


class EnergyProfileStore:
    """Atomic JSON persistence keyed by stable provider-registry model ID."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_ENERGY_PROFILES_PATH

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not load energy profiles: {exc}") from exc
        profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
        if not isinstance(profiles, dict):
            raise ValueError("energy profiles file must contain a profiles object")
        return profiles

    def set(self, model_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        if not model_id.strip():
            raise ValueError("model_id is required")
        normalized = validate_energy_profile(profile)
        profiles = self.load()
        profiles[model_id] = normalized
        self._save(profiles)
        return normalized

    def delete(self, model_id: str) -> bool:
        profiles = self.load()
        if model_id not in profiles:
            return False
        del profiles[model_id]
        self._save(profiles)
        return True

    def _save(self, profiles: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps({"version": 1, "profiles": profiles}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)
