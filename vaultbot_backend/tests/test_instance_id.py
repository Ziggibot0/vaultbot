"""Unit tests for the per-vault instance ID in identity.py.

The instance ID is the *identity* of a VaultBot instance — stable across
restarts, model swaps, and GitHub accounts. It is NOT the GitHub account:
one account can drive many instances, and one instance can be driven by
whichever account is authed at push time. These tests verify the ID is
generated once, persisted, and stable across re-instantiation.
"""

import os

import pytest

pytestmark = pytest.mark.unit

from identity import Identity


def test_instance_id_generated_and_stable(tmp_path):
    """A fresh identity dir gets a UUID, and re-opening returns the same ID."""
    identity_dir = str(tmp_path / "identity")

    first = Identity(identity_dir=identity_dir)
    id1 = first.get_instance_id()

    # A UUID is 36 chars (8-4-4-4-12).
    assert len(id1) == 36
    assert id1.count("-") == 4

    # The file exists on disk.
    assert os.path.exists(os.path.join(identity_dir, "INSTANCE_ID"))

    # Re-instantiate (simulates a restart) — same ID, not regenerated.
    second = Identity(identity_dir=identity_dir)
    id2 = second.get_instance_id()
    assert id2 == id1


def test_instance_id_distinct_across_vaults(tmp_path):
    """Two different identity dirs (two vaults) get different IDs."""
    a = Identity(identity_dir=str(tmp_path / "vault_a" / "identity"))
    b = Identity(identity_dir=str(tmp_path / "vault_b" / "identity"))
    assert a.get_instance_id() != b.get_instance_id()


def test_instance_id_not_in_identity_md(tmp_path):
    """The instance ID is a separate file, not embedded in IDENTITY.md."""
    identity_dir = str(tmp_path / "identity")
    inst = Identity(identity_dir=identity_dir)
    identity_md = inst.get_identity()
    assert inst.get_instance_id() not in identity_md
