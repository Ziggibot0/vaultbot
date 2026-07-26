"""Services registry — bundles all VaultBot backend singletons.

main.py is a service-locator monolith: ~25 module-level singletons
(ollama_client, vault_indexer, vault_graph, fused_retriever, amem, ...)
constructed at startup, then referenced as free variables by helper
functions and route handlers. As main.py is split into separate modules
(chat_handler.py, weaving.py, task_api.py, ...), those extracted functions
can no longer read the globals as free variables. Instead they receive a
`Services` instance as a parameter and access singletons via `svc.<name>`.

This module deliberately uses `TYPE_CHECKING` + string annotations to avoid
import cycles — no leaf module is imported at services.py top level. The
`Services` dataclass is a pure data container; main.py constructs it once
after the globals block and passes it to the extracted functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # all fields are string-annotated; no runtime imports needed

@dataclass
class Services:
    """Bundle of all VaultBot backend singletons.

    Constructed once in main.py after the globals block. Extracted modules
    receive a `Services` instance as their first parameter and access
    singletons via attribute access (``svc.ollama_client``, etc.) instead of
    reading main.py's module-level globals as free variables.
    """
    # LLM + embeddings
    ollama_client: Any
    vision_client: Any | None
    # Index + graph
    vault_indexer: Any
    vault_graph: Any
    note_creator: Any
    # Research
    research_engine: Any
    search_client: Any
    autonomous_researcher: Any
    knowledge_curriculum: Any
    checkpointer: Any
    procedure_tracker: Any
    # Self-improvement + identity
    self_improver: Any
    identity: Any
    # Plan execution
    graph_op_registry: Any
    plan_executor: Any
    # A-MEM + retrieval
    amem: Any
    fused_retriever: Any
    embedding_drift: Any
    # Context management
    compactor: Any
    lazy_condenser: Any
    context_budgeter: Any
    # Health + monitoring
    health_monitor: Any
    # Calibration + evaluation + verification
    calibration_tracker: Any
    rag_evaluator: Any
    claim_verifier: Any
    pattern_extractor: Any
    # Session logging + websocket manager
    session_logger: Any
    manager: Any
