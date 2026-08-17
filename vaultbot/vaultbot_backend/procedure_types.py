"""Dataclasses for the procedure compiler.

Extracted from ``procedure_compiler.py`` so that both
``procedure_compiler.py`` and ``procedure_step_compilers.py`` can import
``Step`` / ``Procedure`` without creating a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    """A single step in a procedure.

    Attributes:
        number: 1-indexed step number as written in the markdown.
        instruction: The instruction text (for text steps), stripped of
            bold markers and inline annotations.
        step_type: One of "text" (v1 default), "code" (v2 embedded
            Python), or "llm" (v2 [llm: ...] tag).
        code: Python code for code steps (``step_type == "code"``).
            None for other step types.
        llm_instruction: Instruction for LLM steps
            (``step_type == "llm"``).  None for other step types.
        validation: Validation criteria parsed from ``[validate: ...]``.
            None if no annotation present.
        condition: Execution condition parsed from ``[condition: ...]``.
            None if no annotation present (step always executes).
        branch_target: Step number to jump to, parsed from
            ``[branch: step N]``.  None if no branch annotation.
    """

    number: float
    instruction: str
    step_type: str = "text"
    code: str | None = None
    llm_instruction: str | None = None
    validation: str | None = None
    condition: str | None = None
    branch_target: float | None = None


@dataclass
class Procedure:
    """A compiled procedure ready for step-gate execution.

    Attributes:
        name: Note stem (title without extension).
        file_path: Path to the source markdown, or '' if compiled from text.
        version: Semantic version from frontmatter (default '1.0.0').
        activation: When the procedure activates — 'always', 'on-match',
            or 'manual' (default 'always').
        spec_version: Format spec version (default '1').
        steps: Ordered list of Step objects.
        raw_text: Full markdown text (for fallback / debugging).
        frontmatter: Parsed frontmatter dict.
        description: One-line summary from frontmatter (v2). Empty string
            if not present. Used for retrieval efficiency — VaultBot
            reads THIS instead of the full procedure body to decide
            whether to invoke.
        allowed_tools: List of tool names the procedure is permitted to
            call (v2). Empty list if not present. The step-gate runtime
            injects only these tools into the subprocess namespace.
    """

    name: str
    file_path: str
    version: str
    activation: str
    spec_version: str
    steps: list[Step]
    raw_text: str
    frontmatter: dict
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model_cartridge: str = "big"  # "big", "small", or "vision"
