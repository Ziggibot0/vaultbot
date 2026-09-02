"""Tests for POST /config runtime safe_mode/contributions updates."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast

import live_config
import pytest
from routers.config import _coerce_bool, set_config
from services import Services

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_live_config_overrides() -> Iterator[None]:
    live_config.set_safe_mode(None)
    live_config.set_allow_contributions(None)
    yield
    live_config.set_safe_mode(None)
    live_config.set_allow_contributions(None)


def _fake_services() -> Services:
    return cast(
        Services,
        SimpleNamespace(search_client=SimpleNamespace(is_configured=True)),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        (" false ", False),
        ("YES", True),
        ("off", False),
        ("1", True),
        ("0", False),
        (2, None),
        ("developer", None),
        ("garbage", None),
        (None, None),
    ],
)
def test_coerce_bool(value, expected):
    assert _coerce_bool(value) is expected


def test_set_config_applies_valid_bool_strings() -> None:
    asyncio.run(
        set_config(
            payload={"safe_mode": "false", "allow_contributions": "true"},
            svc=_fake_services(),
        )
    )

    assert live_config.is_safe_mode() is False
    assert live_config.allow_contributions() is True


def test_set_config_ignores_invalid_bool_strings() -> None:
    live_config.set_safe_mode(True)
    live_config.set_allow_contributions(False)

    asyncio.run(
        set_config(
            payload={"safe_mode": "developer", "allow_contributions": "maybe"},
            svc=_fake_services(),
        )
    )

    assert live_config.is_safe_mode() is True
    assert live_config.allow_contributions() is False
