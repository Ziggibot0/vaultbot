"""HTTP contract tests for the energy estimate API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from app_state import get_services
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import energy

pytestmark = pytest.mark.unit


class FakeProfileStore:
    profiles: ClassVar[dict[str, dict]] = {}

    def load(self):
        return dict(self.profiles)

    def set(self, model_id, payload):
        from energy_profiles import validate_energy_profile

        profile = validate_energy_profile(payload)
        self.profiles[model_id] = profile
        return profile

    def delete(self, model_id):
        return self.profiles.pop(model_id, None) is not None


class FakeRegistry:
    def __init__(self):
        self.model = SimpleNamespace(
            id="openrouter/example/model",
            model="example/model",
            provider="openrouter",
            label="Example",
        )
        self.provider = SimpleNamespace(label="OpenRouter")

    def list_models(self):
        return [self.model]

    def get_model(self, model_id):
        return self.model if model_id == self.model.id else None

    def get_provider(self, provider_id):
        return self.provider if provider_id == "openrouter" else None

    def get_role(self, role):
        return self.model.id if role == "big" else None


@pytest.fixture
def client(tmp_path, monkeypatch):
    FakeProfileStore.profiles = {}
    monkeypatch.setattr(energy, "EnergyProfileStore", FakeProfileStore)
    services = SimpleNamespace(
        registry=FakeRegistry(),
        session_logger=SimpleNamespace(log_dir=tmp_path),
    )
    app = FastAPI()
    app.include_router(energy.router)
    app.dependency_overrides[get_services] = lambda: services
    return TestClient(app)


def test_profiles_include_registered_models_without_defaults(client):
    response = client.get("/energy/profiles")

    assert response.status_code == 200
    assert response.json()["profiles"] == [
        {
            "model_id": "openrouter/example/model",
            "model": "example/model",
            "label": "Example",
            "provider_id": "openrouter",
            "provider_label": "OpenRouter",
            "roles": ["big"],
            "profile": None,
        }
    ]


def test_profile_round_trip_supports_model_ids_with_slashes(client):
    model_id = "openrouter/example/model"
    response = client.put(
        f"/energy/profiles/{model_id}",
        json={
            "wh_per_1k_input_tokens": 0.25,
            "wh_per_1k_output_tokens": 0.5,
            "source_note": "Measured locally",
        },
    )

    assert response.status_code == 200
    assert response.json()["model_id"] == model_id
    listed = client.get("/energy/profiles").json()["profiles"][0]
    assert listed["profile"]["wh_per_1k_input_tokens"] == 0.25
    assert client.delete(f"/energy/profiles/{model_id}").json()["status"] == "ok"


def test_profile_rejects_negative_coefficients(client):
    response = client.put(
        "/energy/profiles/openrouter/example/model",
        json={"wh_per_1k_input_tokens": -1, "wh_per_1k_output_tokens": 0.5},
    )

    assert response.status_code == 400
    assert "non-negative" in response.json()["detail"]


def test_report_validates_range_and_exposes_baseline(client):
    assert client.get("/energy/report?days=0").status_code == 422

    response = client.get("/energy/report?days=7")

    assert response.status_code == 200
    report = response.json()
    assert report["range_days"] == 7
    assert report["baseline_big_model_id"] == "openrouter/example/model"
    assert report["totals"]["tracked_invocations"] == 0
