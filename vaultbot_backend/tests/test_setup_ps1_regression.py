"""Regression test: setup.ps1 install-state + Docker daemon guards.

Guards against the two bugs fixed in issue #92:

1. **PSCustomObject property-assignment failure.** On Windows PowerShell
   5.1, ``ConvertFrom-Json`` returns a ``PSCustomObject``, not a hashtable.
   Assigning a *new* property via ``$state.$step = $true`` throws
   ``"The property 'X' cannot be found on this object."`` — so every
   ``Set-StepDone`` / ``Set-StateValue`` call after the first write silently
   failed, and the installer's resume-on-rerun feature was broken.

   The fix copies the PSCustomObject properties into a real ``@{}``
   hashtable before adding new keys. This test asserts the buggy
   ``$state.$step = ...`` / ``$state.$key = ...`` pattern is absent and the
   hashtable-copy pattern (``foreach ($prop in $obj.PSObject.Properties)``)
   is present in both helpers.

2. **Docker daemon-not-running not detected.** The old check only ran
   ``docker --version``, which succeeds if the CLI is installed even when
   Docker Desktop (the daemon) isn't running — so every ``docker ps``/``run``
   call failed with a pipe-not-found error. The fix probes the daemon with
   ``docker info`` before issuing container commands.

This is a source-level guard, not a runtime test: PowerShell 5.1 is not
available in the Linux CI runner, so we assert on the script text itself.
It catches the exact regression class from issue #92 without needing a
Windows host.

Run: pytest tests/test_setup_ps1_regression.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# setup.ps1 lives at vaultbot/setup.ps1; this file is at
# vaultbot/vaultbot_backend/tests/test_setup_ps1_regression.py.
_SETUP_PS1 = Path(__file__).resolve().parents[2] / "setup.ps1"


@pytest.fixture(scope="module")
def setup_ps1_text() -> str:
    if not _SETUP_PS1.exists():
        pytest.skip(f"setup.ps1 not found at {_SETUP_PS1}")
    return _SETUP_PS1.read_text(encoding="utf-8-sig")


def _extract_function(text: str, name: str) -> str:
    """Return the body of the named PowerShell function, or "" if absent."""
    start = text.find(f"function {name}")
    if start == -1:
        return ""
    # Find the matching closing brace: the function body is the first
    # top-level "}" after the opening "{".
    open_idx = text.find("{", start)
    if open_idx == -1:
        return ""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]
    return ""


def test_setup_ps1_exists(setup_ps1_text: str) -> None:
    assert setup_ps1_text, "setup.ps1 is empty or unreadable"


def test_no_pscustomobject_property_assignment(setup_ps1_text: str) -> None:
    """The buggy `$state.$step = ...` / `$state.$key = ...` WRITE is gone.

    On PowerShell 5.1, *assigning* a new property to a PSCustomObject
    (``$state.$step = $true``) throws "The property 'X' cannot be found on
    this object." because `$state` is a PSCustomObject after ConvertFrom-Json.

    Note: *reading* a property (``$state.$step -eq $true`` in Test-StepDone)
    is fine on a PSCustomObject — only the assignment form is the bug. So we
    assert specifically against the assignment, not the bare token.
    """
    assert "$state.$step =" not in setup_ps1_text, (
        "setup.ps1 still assigns `$state.$step = $true` — this throws on "
        "PowerShell 5.1 (PSCustomObject). Use `$state[$step] = $true` on a "
        "real hashtable instead (issue #92)."
    )
    assert "$state.$key =" not in setup_ps1_text, (
        "setup.ps1 still assigns `$state.$key = $value` — this throws on "
        "PowerShell 5.1 (PSCustomObject). Use `$state[$key] = $value` on a "
        "real hashtable instead (issue #92)."
    )


def test_set_step_done_copies_pscustomobject_to_hashtable(
    setup_ps1_text: str,
) -> None:
    body = _extract_function(setup_ps1_text, "Set-StepDone")
    assert body, "Set-StepDone function not found in setup.ps1"
    assert "PSObject.Properties" in body, (
        "Set-StepDone no longer copies PSCustomObject properties into a "
        "hashtable — the issue #92 fix regressed."
    )
    assert "$state[$step] = $true" in body, (
        "Set-StepDone no longer writes via hashtable index `$state[$step]`."
    )


def test_set_state_value_copies_pscustomobject_to_hashtable(
    setup_ps1_text: str,
) -> None:
    body = _extract_function(setup_ps1_text, "Set-StateValue")
    assert body, "Set-StateValue function not found in setup.ps1"
    assert "PSObject.Properties" in body, (
        "Set-StateValue no longer copies PSCustomObject properties into a "
        "hashtable — the issue #92 fix regressed."
    )
    assert "$state[$key] = $value" in body, (
        "Set-StateValue no longer writes via hashtable index `$state[$key]`."
    )


def test_docker_daemon_probe_present(setup_ps1_text: str) -> None:
    """The installer probes the daemon with `docker info`, not just the CLI."""
    assert "docker info" in setup_ps1_text, (
        "setup.ps1 no longer probes the Docker daemon with `docker info` — "
        "the issue #92 fix regressed. `docker --version` alone does not prove "
        "the daemon is running."
    )
