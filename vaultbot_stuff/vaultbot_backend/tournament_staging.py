"""
Tournament staging — a separate "try before you buy" model pot.

The main ProviderRegistry pot is for production models assigned to cartridge
roles (big/small/vision). The tournament staging pot is for models the user
wants to evaluate BEFORE deciding whether to add them to the main pot.

Staging entries are lightweight: just a provider id + model name. The
tournament runner builds a one-shot client from the provider's connection
info — no need to register the model in the main pot first.

Persisted to ``tournament_staging.json`` at the vault root.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_STAGING_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tournament_staging.json"
)


@dataclass
class StagingEntry:
    """One model in the tournament staging pot."""

    id: str  # unique, e.g. "ollama-local:qwen3.6:27b"
    model: str  # provider's model name
    provider: str  # provider id (must exist in main registry)
    label: str = ""  # optional display label

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TournamentStaging:
    """Thread-safe, file-backed staging pot for tournament models.

    Separate from ProviderRegistry — these are models the user is evaluating,
    not models assigned to production cartridge roles.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _STAGING_PATH
        self._lock = threading.RLock()
        self._entries: dict[str, StagingEntry] = {}
        self.load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            try:
                if not self._path.exists():
                    return
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for e in data.get("entries", []):
                    entry = StagingEntry(**e)
                    self._entries[entry.id] = entry
            except Exception as e:
                print(f"[WARN] TournamentStaging.load failed ({e}); starting empty.")

    def save(self) -> None:
        with self._lock:
            data = {"entries": [e.to_dict() for e in self._entries.values()]}
            tmp = self._path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, self._path)
            except Exception as e:
                print(f"[WARN] TournamentStaging.save failed: {e}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_entries(self) -> list[StagingEntry]:
        with self._lock:
            return list(self._entries.values())

    def get_entry(self, entry_id: str) -> StagingEntry | None:
        with self._lock:
            return self._entries.get(entry_id)

    def add_entry(self, model: str, provider: str, label: str = "") -> StagingEntry:
        """Add a model to the staging pot. Generates id as provider:model."""
        entry_id = f"{provider}:{model}"
        with self._lock:
            entry = StagingEntry(
                id=entry_id, model=model, provider=provider, label=label
            )
            self._entries[entry_id] = entry
            self.save()
            return entry

    def remove_entry(self, entry_id: str) -> bool:
        with self._lock:
            if entry_id not in self._entries:
                return False
            del self._entries[entry_id]
            self.save()
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.save()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)
