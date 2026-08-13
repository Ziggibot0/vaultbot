"""Services registry — bundles all VaultBot backend singletons.

main.py is a service-locator monolith: ~25 module-level singletons
(ollama_client, vault_indexer, vault_graph, fused_retriever, amem, ...)
constructed at startup, then referenced as free variables by helper
functions and route handlers. As main.py is split into separate modules
(chat_handler.py, weaving.py, task_api.py, ...), those extracted functions
can no longer read the globals as free variables. Instead they receive a
`Services` instance as a parameter and access singletons via `svc.<name>`.

Phase 3 (typed Services): the fields are now typed with their real classes
(imported under ``TYPE_CHECKING`` so no runtime import cycle is created).
This gives IDE autocomplete + type-checking across every extracted module
without changing the no-cycle property.  ``get_services()`` / ``set_services()``
live in ``app_state.py`` and are the FastAPI dependency-injection surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ollama_client import OllamaClient
    from llm_client import LLMClient
    from vault_indexer import VaultIndexer
    from vault_graph import VaultGraph
    from note_creator import NoteCreator
    from research_engine import ResearchEngine
    from free_search import FreeSearch
    from autonomous_researcher import AutonomousResearcher
    from knowledge_curriculum import KnowledgeCurriculum
    from checkpointer import Checkpointer
    from procedure_tracker import ProcedureTracker
    from self_improver import SelfImprover
    from identity import Identity
    from graph_ops import GraphOpRegistry
    from plan_executor import PlanExecutor
    from amem_evolution import AMemeEvolution
    from fused_retrieval import FusedRetriever
    from embedding_drift import EmbeddingDrift
    from lazy_condenser import LazyCondenser
    from context_budgeter import ContextBudgeter
    from supervision import HealthMonitor
    from calibration import CalibrationTracker
    from rag_eval import RAGEvaluator
    from claim_verifier import ClaimVerifier
    from pattern_extractor import PatternExtractor
    from session_logger import SessionLogger


@dataclass
class Services:
    """Bundle of all VaultBot backend singletons.

    Constructed once in main.py after the globals block. Extracted modules
    receive a `Services` instance as their first parameter and access
    singletons via attribute access (``svc.ollama_client``, etc.) instead of
    reading main.py's module-level globals as free variables.  Routers
    receive it via ``Depends(get_services)`` (see app_state.py).

    The ``ollama_client``, ``vision_client``, and ``small_client`` fields are
    mutable: the ``/llm/config`` / ``/llm/vision_config`` / ``/llm/small_config``
    routes can swap the corresponding client at runtime, and every
    ``Depends(get_services)`` consumer sees the new client immediately
    (the dataclass instance is shared, not re-created per request).
    """

    # LLM + embeddings
    ollama_client: OllamaClient
    vision_client: LLMClient | None
    # Index + graph
    vault_indexer: VaultIndexer
    vault_graph: VaultGraph
    note_creator: NoteCreator
    # Research
    research_engine: ResearchEngine
    search_client: FreeSearch
    autonomous_researcher: AutonomousResearcher
    knowledge_curriculum: KnowledgeCurriculum
    checkpointer: Checkpointer
    procedure_tracker: ProcedureTracker
    # Self-improvement + identity
    self_improver: SelfImprover
    identity: Identity
    # Plan execution
    graph_op_registry: GraphOpRegistry
    plan_executor: PlanExecutor
    # A-MEM + retrieval
    amem: AMemeEvolution
    fused_retriever: FusedRetriever
    embedding_drift: EmbeddingDrift
    # Context management (sliding window — no LLM compaction)
    lazy_condenser: LazyCondenser
    context_budgeter: ContextBudgeter
    # Health + monitoring
    health_monitor: HealthMonitor
    # Calibration + evaluation + verification
    calibration_tracker: CalibrationTracker
    rag_evaluator: RAGEvaluator
    claim_verifier: ClaimVerifier
    pattern_extractor: PatternExtractor
    # Session logging + websocket manager
    session_logger: SessionLogger
    # Chat-loop checkpoint/resume (multi-day sturdiness). Optional so older
    # wiring and tests that build Services without it still work.
    chat_checkpointer: object | None = None
    # Small (tiny local dance partner) client — None when SMALL_MODEL unset.
    # Optional with a default so it doesn't break the dataclass field
    # ordering (non-default fields must precede default ones).
    small_client: LLMClient | None = None
    # Provider + Model Registry — the single "pot" of LLM connections/models.
    # Roles (big/small/vision) draw from this one pot. Optional with a default
    # so tests that build Services without it still construct.
    registry: object | None = None
    # ConnectionManager is defined in main.py (kept there for now); typed
    # loosely to avoid importing main here (would create a cycle).
    manager: object = None
    # Conversation-aware retrieval: searchable index of recent conversation
    # turns so the bot can "remember what it just said." Optional with a
    # default so tests that build Services without it still construct.
    conversation_index: object | None = None

    @property
    def vault_path(self) -> str:
        """Resolved vault root path (single source of truth).

        Several extracted modules (chat_handler's vault-change broadcast,
        research route's note-title lookup) read ``svc.vault_path``. The
        canonical path lives on the indexer, which is constructed from
        ``os.getenv("VAULT_PATH")`` and resolved at startup. Delegating here
        avoids a second copy of the env read and guarantees every consumer
        sees the same root even if VAULT_PATH is reconfigured at runtime.
        """
        return str(self.vault_indexer.vault_path)
