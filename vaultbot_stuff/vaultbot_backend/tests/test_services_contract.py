"""Contract tests for the Services registry: every attribute the
extracted modules (chat_handler, routers) read off ``svc`` must actually
exist on the ``Services`` dataclass. These catch the class of bug where
a consumer references ``svc.vault_path`` (or any other attribute) before
the API is defined on the registry.

Bug caught: 2026-07-30, ``chat_handler.py`` did ``svc.vault_path`` in
the post-chat vault-change broadcast, but ``Services`` had no such
field — the broadcast crashed with
``'Services' object has no attribute 'vault_path'`` on every turn.

These tests construct a ``Services`` with stub singletons (no main
import, no real indexer) and assert the attributes the consumers use
are present and well-typed.
"""

from pathlib import Path
from unittest.mock import MagicMock

import services


def _stub_services(**overrides):
    """Build a Services with mock singletons for every required field so
    we can test attribute access without the real startup graph. The
    vault_indexer mock carries a resolved ``vault_path`` so the
    ``Services.vault_path`` property can delegate to it.
    """
    indexer = MagicMock()
    indexer.vault_path = Path("/fake/vault")
    defaults = dict(
        ollama_client=MagicMock(),
        vision_client=None,
        vault_indexer=indexer,
        vault_graph=MagicMock(),
        note_creator=MagicMock(),
        research_engine=MagicMock(),
        search_client=MagicMock(),
        autonomous_researcher=MagicMock(),
        knowledge_curriculum=MagicMock(),
        checkpointer=MagicMock(),
        procedure_tracker=MagicMock(),
        self_improver=MagicMock(),
        identity=MagicMock(),
        graph_op_registry=MagicMock(),
        plan_executor=MagicMock(),
        amem=MagicMock(),
        fused_retriever=MagicMock(),
        embedding_drift=MagicMock(),
        lazy_condenser=MagicMock(),
        context_budgeter=MagicMock(),
        health_monitor=MagicMock(),
        calibration_tracker=MagicMock(),
        rag_evaluator=MagicMock(),
        claim_verifier=MagicMock(),
        pattern_extractor=MagicMock(),
        session_logger=MagicMock(),
        chat_checkpointer=None,
        manager=MagicMock(),
    )
    defaults.update(overrides)
    return services.Services(**defaults)


def test_services_has_vault_path_property():
    """Services must expose ``vault_path``. chat_handler reads
    ``svc.vault_path`` for the vault-change broadcast and the
    research-route note-title lookup; a missing attribute crashes the
    post-chat path on every turn.
    """
    svc = _stub_services()
    assert hasattr(svc, "vault_path"), (
        "Services has no vault_path attribute — chat_handler's "
        "vault-change broadcast and research route would crash with "
        "'Services' object has no attribute 'vault_path'.")
    assert isinstance(svc.vault_path, str)
    assert svc.vault_path == str(Path("/fake/vault"))


def test_services_vault_path_delegates_to_indexer():
    """vault_path must delegate to the indexer's resolved path (single
    source of truth), not re-read the env var. If the indexer is
    reconfigured at runtime, every consumer sees the new path.
    """
    svc = _stub_services()
    new_path = Path("/a/different/vault")
    svc.vault_indexer.vault_path = new_path
    assert svc.vault_path == str(new_path), (
        "Services.vault_path did not reflect a change to the indexer's "
        "vault_path — it should delegate, not cache a copy.")


def test_services_required_fields_present():
    """Every field chat_handler / routers read must be on Services. This
    is the 'consumer-before-API' guard: if a future extraction reads a
    new attribute off svc, this test forces the author to add the field
    to the dataclass first. Listed here are the attributes the current
    consumers actually dereference.
    """
    svc = _stub_services()
    # The core consumers (chat_handler, ws, research) dereference these.
    # NOTE: 'compactor' removed — the sliding-window refactor replaced
    #   compaction; no live consumer reads svc.compactor (the Compactor
    #   class is a no-op shim kept for import compat only).
    for attr in ("ollama_client", "manager", "session_logger",
                 "vault_indexer", "research_engine", "fused_retriever",
                 "identity", "vault_graph", "note_creator",
                 "amem", "pattern_extractor", "procedure_tracker",
                 "autonomous_researcher", "vault_path"):
        assert hasattr(svc, attr), (
            f"Services is missing '{attr}' — a consumer reads svc.{attr} "
            f"and would crash with AttributeError.")
