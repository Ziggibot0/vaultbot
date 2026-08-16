"""
Tournament benchmarks — vaultbot-specific problem sets for model evaluation.

Two benchmark suites, one for each cartridge role. Each benchmark is a
prompt + rubric that tests the kind of work that role actually does in
production. The tournament runner sends each prompt to each contestant model
and scores the response with an LLM-as-judge (the current big model).

Design principles:
- **Vaultbot-specific**: every benchmark tests a real task the model does in
  the agentic loop, not a generic MMLU/GSM8K question.
- **Deterministic rubric**: each benchmark has a pass/fail rubric so the judge
  model can score objectively.
- **Cheap to run**: small benchmarks are single-turn (no tool calls); big
  benchmarks may be multi-turn but kept short (2-3 turns max).
- **Growable**: add new benchmarks by appending to the lists below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════════════
# Benchmark dataclass
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Benchmark:
    """One tournament benchmark problem."""

    id: str  # unique id, e.g. "big-reasoning-01"
    name: str  # human-readable name
    description: str  # what this tests
    prompt: str  # the user message to send
    system: str = ""  # optional system prompt
    rubric: str = ""  # scoring rubric for the judge
    expected_keywords: list[str] = field(default_factory=list)  # fast pre-filter
    max_tokens: int = 512  # max output tokens
    temperature: float = 0.0  # deterministic for eval
    category: str = ""  # grouping: "reasoning", "extraction", etc.


# ═══════════════════════════════════════════════════════════════════════════
# BIG cartridge benchmarks — reasoning, synthesis, research, planning
# ═══════════════════════════════════════════════════════════════════════════

BIG_BENCHMARKS: list[Benchmark] = [
    Benchmark(
        id="big-reasoning-01",
        name="Multi-hop vault reasoning",
        description="Can the model connect facts across multiple vault notes?",
        category="reasoning",
        prompt=(
            "A user's vault contains these notes:\n"
            "1. 'Python-Dependency-Management.md' says: 'Use pip-tools to "
            "compile requirements.in into a locked requirements.txt. Never "
            "edit requirements.txt by hand.'\n"
            "2. 'VaultBot-Deployment.md' says: 'The backend runs in a venv "
            "at .venv/. Install dependencies with pip install -r "
            "requirements.txt.'\n"
            "3. 'Contributing-Guide.md' says: 'After adding a new dependency, "
            "run pip-compile requirements.in to regenerate requirements.txt.'\n\n"
            "Question: If a developer adds 'httpx' to requirements.in and "
            "wants to deploy, what two commands must they run, in order?"
        ),
        rubric=(
            "PASS if the answer mentions BOTH: (1) pip-compile or pip-tools "
            "to regenerate requirements.txt from requirements.in, AND (2) "
            "pip install -r requirements.txt to install into the venv. "
            "FAIL if it misses either step or gets the order wrong."
        ),
        expected_keywords=["pip-compile", "pip install", "requirements"],
    ),
    Benchmark(
        id="big-reasoning-02",
        name="Procedure selection",
        description="Can the model pick the right procedure for a task?",
        category="reasoning",
        prompt=(
            "A user asks: 'I just wrote a research note about climate models. "
            "Before I publish it, I want to make sure every factual claim in "
            "it is backed by a source.'\n\n"
            "Which VaultBot procedure should the user run? Answer with the "
            "procedure name and explain why in one sentence."
        ),
        rubric=(
            "PASS if the answer identifies 'Verify-Claims' or "
            "'How-to-Verify-Claims-in-a-Research-Note' as the correct "
            "procedure. FAIL if it suggests a different procedure or gives "
            "a generic answer without naming the specific procedure."
        ),
        expected_keywords=["verify", "claim"],
    ),
    Benchmark(
        id="big-reasoning-03",
        name="Research vs. answer decision",
        description="Can the model decide when to research vs. answer from vault?",
        category="reasoning",
        prompt=(
            "A user asks: 'What is the capital of France?'\n\n"
            "Should VaultBot answer this from its own knowledge, search the "
            "vault, or run web research? Explain your reasoning in one sentence."
        ),
        rubric=(
            "PASS if the answer says to answer from own knowledge (it's common "
            "knowledge, no vault search or web research needed). FAIL if it "
            "suggests searching the vault or running web research for this "
            "trivial fact."
        ),
        expected_keywords=["knowledge", "common", "trivial"],
    ),
    Benchmark(
        id="big-reasoning-04",
        name="Note structuring",
        description="Can the model structure a research note properly?",
        category="synthesis",
        prompt=(
            "You have researched 'WebAssembly performance vs JavaScript' and "
            "found these facts:\n"
            "- WASM is typically 1.2-2x faster than JS for compute-heavy tasks\n"
            "- WASM has no DOM access; must call JS for DOM\n"
            "- Startup time is slower for WASM due to compilation\n"
            "- WASM is a compilation target for C/C++/Rust\n\n"
            "Write a 3-sentence summary suitable for a research note. "
            "Be concise and factual."
        ),
        rubric=(
            "PASS if the summary: (1) is 3 sentences or fewer, (2) mentions "
            "the performance advantage, (3) mentions the DOM limitation, and "
            "(4) is purely factual with no fluff or hedging. "
            "FAIL if it exceeds 3 sentences, omits key facts, or adds "
            "unsupported claims."
        ),
        expected_keywords=["WASM", "DOM", "performance"],
    ),
    Benchmark(
        id="big-reasoning-05",
        name="Tool-use planning",
        description="Can the model plan a multi-step tool-use task?",
        category="planning",
        prompt=(
            "A user says: 'Find all notes in my vault that mention Python, "
            "read the top 3 most relevant ones, and write a summary.'\n\n"
            "List the exact VaultBot tools you would call, in order, to "
            "complete this task. Use the actual tool names."
        ),
        rubric=(
            "PASS if the plan includes: (1) vault_search for 'Python', "
            "(2) code_read or vault_read_note on the top results, "
            "(3) write_note or similar to save the summary. "
            "FAIL if it misses the search step or tries to write before reading."
        ),
        expected_keywords=["vault_search", "read", "write"],
    ),
    Benchmark(
        id="big-reasoning-06",
        name="Source evaluation",
        description="Can the model evaluate source quality?",
        category="reasoning",
        prompt=(
            "You are researching 'CRISPR gene editing safety' and find these "
            "sources:\n"
            "A. A 2024 Nature paper with 200+ citations\n"
            "B. A 2025 Medium blog post by an anonymous author\n"
            "C. A 2023 FDA regulatory document\n"
            "D. A 2025 Reddit thread with 50 comments\n\n"
            "Rank them from most to least reliable for a research note. "
            "Explain each ranking in one phrase."
        ),
        rubric=(
            "PASS if the ranking is A/C (top tier), then B, then D (bottom), "
            "with reasoning that values peer review and official sources over "
            "anonymous/social media. FAIL if it ranks B or D above A or C."
        ),
        expected_keywords=["Nature", "FDA", "peer"],
    ),
    Benchmark(
        id="big-reasoning-07",
        name="Error diagnosis",
        description="Can the model diagnose a common VaultBot error?",
        category="reasoning",
        prompt=(
            "A user reports: 'VaultBot says \"no model assigned to the big "
            "cartridge\" when I try to chat.'\n\n"
            "What is the most likely cause and how should the user fix it? "
            "Answer in 2 sentences."
        ),
        rubric=(
            "PASS if the answer says to open VaultBot Settings -> AI Models "
            "& Providers and assign a model to the Big cartridge role. "
            "FAIL if it suggests editing .env manually, restarting Ollama, "
            "or any fix that doesn't involve the settings UI."
        ),
        expected_keywords=["settings", "cartridge", "assign"],
    ),
    Benchmark(
        id="big-reasoning-08",
        name="Hallucination resistance",
        description="Does the model admit ignorance rather than fabricate?",
        category="reasoning",
        prompt=(
            "What is the exact procedure name for 'flarging the worble' in "
            "VaultBot? If you don't know, say so."
        ),
        rubric=(
            "PASS if the answer admits it doesn't know or says no such "
            "procedure exists. FAIL if it invents a procedure name or "
            "describes what 'flarging the worble' might mean."
        ),
        expected_keywords=["don't know", "no such", "not aware", "doesn't exist"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# SMALL cartridge benchmarks — classification, extraction, routing, tagging
# ═══════════════════════════════════════════════════════════════════════════

SMALL_BENCHMARKS: list[Benchmark] = [
    Benchmark(
        id="small-classify-01",
        name="Trivial-turn detection",
        description="Can the model classify a trivial greeting correctly?",
        category="classification",
        prompt=(
            "Classify this user message as 'trivial' or 'meaningful':\n\n"
            '"hey"\n\n'
            "Reply with exactly one word: trivial or meaningful."
        ),
        rubric=(
            "PASS if the answer is exactly 'trivial' (case-insensitive). "
            "FAIL for any other answer."
        ),
        expected_keywords=["trivial"],
        max_tokens=16,
    ),
    Benchmark(
        id="small-classify-02",
        name="Meaningful-turn detection",
        description="Can the model classify a complex query as meaningful?",
        category="classification",
        prompt=(
            "Classify this user message as 'trivial' or 'meaningful':\n\n"
            '"Can you search my vault for notes about machine learning '
            'and summarize the key findings?"\n\n'
            "Reply with exactly one word: trivial or meaningful."
        ),
        rubric=(
            "PASS if the answer is exactly 'meaningful' (case-insensitive). "
            "FAIL for any other answer."
        ),
        expected_keywords=["meaningful"],
        max_tokens=16,
    ),
    Benchmark(
        id="small-extract-01",
        name="Tag suggestion",
        description="Can the model suggest relevant tags for a note?",
        category="extraction",
        prompt=(
            "Suggest 1-3 tags for this note about 'Python async programming "
            "with asyncio and coroutines'. Reply with a JSON array of strings, "
            "nothing else."
        ),
        rubric=(
            "PASS if the response is a valid JSON array of 1-3 strings, and "
            "at least one tag relates to Python, async, or programming. "
            "FAIL if the response is not valid JSON, has zero tags, or the "
            "tags are completely unrelated (e.g. ['cooking', 'sports'])."
        ),
        expected_keywords=["python", "async", "programming"],
        max_tokens=128,
    ),
    Benchmark(
        id="small-extract-02",
        name="Claim extraction",
        description="Can the model extract factual claims from text?",
        category="extraction",
        prompt=(
            "Extract all factual claims from this text as a JSON array of "
            "strings:\n\n"
            '"Python 3.12 introduced the new type statement. It is 15% faster '
            'than 3.11 on average. Many developers have adopted it."\n\n'
            "Reply with ONLY a JSON array."
        ),
        rubric=(
            "PASS if the response is a valid JSON array containing at least "
            "2 claims, and the claims include the type statement and the "
            "performance improvement. FAIL if not valid JSON or fewer than "
            "2 claims extracted."
        ),
        expected_keywords=["type statement", "faster", "3.12"],
        max_tokens=256,
    ),
    Benchmark(
        id="small-classify-03",
        name="Entailment check",
        description="Can the model check if a claim is supported by evidence?",
        category="classification",
        prompt=(
            "Evidence: 'Python 3.12 is 15% faster than 3.11 on the pyperformance benchmark suite.'\n"
            "Claim: 'Python 3.12 is faster than 3.11.'\n\n"
            "Does the evidence SUPPORT, CONTRADICT, or is NEUTRAL to the claim? "
            "Reply with exactly one word."
        ),
        rubric=(
            "PASS if the answer is 'SUPPORT' (case-insensitive). "
            "FAIL for 'CONTRADICT' or 'NEUTRAL'."
        ),
        expected_keywords=["support"],
        max_tokens=16,
    ),
    Benchmark(
        id="small-classify-04",
        name="Entailment contradiction",
        description="Can the model detect when evidence contradicts a claim?",
        category="classification",
        prompt=(
            "Evidence: 'Python 3.12 is 5% slower than 3.11 on the pyperformance benchmark suite.'\n"
            "Claim: 'Python 3.12 is faster than 3.11.'\n\n"
            "Does the evidence SUPPORT, CONTRADICT, or is NEUTRAL to the claim? "
            "Reply with exactly one word."
        ),
        rubric=(
            "PASS if the answer is 'CONTRADICT' (case-insensitive). "
            "FAIL for 'SUPPORT' or 'NEUTRAL'."
        ),
        expected_keywords=["contradict"],
        max_tokens=16,
    ),
    Benchmark(
        id="small-extract-03",
        name="Query rewriting",
        description="Can the model rewrite a vague query for better retrieval?",
        category="extraction",
        prompt=(
            "Rewrite this vague search query to be more specific for a "
            "vault search:\n\n"
            '"that thing about models"\n\n'
            "Reply with the rewritten query only."
        ),
        rubric=(
            "PASS if the rewritten query is more specific than the original "
            "(adds context like 'machine learning models', 'AI models', or "
            "'language models'). FAIL if it's equally vague or just repeats "
            "the original."
        ),
        expected_keywords=["model"],
        max_tokens=128,
    ),
    Benchmark(
        id="small-summarize-01",
        name="Conversation summarization",
        description="Can the model summarize a conversation compactly?",
        category="summarization",
        prompt=(
            "Summarize this conversation in 1-2 sentences:\n\n"
            "User: 'How do I add a new model to VaultBot?'\n"
            "Bot: 'Open Settings -> AI Models & Providers, click Add Model, "
            "select the provider, enter the model ID, and save.'\n"
            "User: 'Does it work with OpenRouter?'\n"
            "Bot: 'Yes, add OpenRouter as a provider first, then add models "
            "from it.'\n\n"
            "Summary:"
        ),
        rubric=(
            "PASS if the summary captures both: (1) user wants to add a model, "
            "and (2) the answer involves Settings -> AI Models & Providers. "
            "FAIL if it misses either element or exceeds 2 sentences."
        ),
        expected_keywords=["model", "settings"],
        max_tokens=256,
    ),
    Benchmark(
        id="small-classify-05",
        name="Procedure routing hint",
        description="Can the model suggest the right procedure from a user query?",
        category="classification",
        prompt=(
            "A user says: 'I need to verify that every claim in my note about "
            "climate change is backed by evidence.'\n\n"
            "Which procedure should run? Reply with the procedure name only."
        ),
        rubric=(
            "PASS if the answer contains 'verify' or 'claim' (case-insensitive) "
            "and is a procedure name, not a generic description. "
            "FAIL if it gives a generic answer like 'fact-check' without "
            "naming a specific procedure."
        ),
        expected_keywords=["verify", "claim"],
        max_tokens=64,
    ),
    Benchmark(
        id="small-extract-04",
        name="Concept card refinement",
        description="Can the model refine a rough note into a concise concept card?",
        category="extraction",
        prompt=(
            "Refine this rough note into a 2-3 sentence concept card:\n\n"
            '"RAG is like when you give the AI some documents and it reads '
            "them and then answers your question using those documents. It's "
            "better than just asking the AI because it has context. People "
            'use it for chatbots and stuff."\n\n'
            "Refined concept card:"
        ),
        rubric=(
            "PASS if the refined text: (1) is 2-3 sentences, (2) defines RAG "
            "as retrieval-augmented generation, (3) is more formal than the "
            "original. FAIL if it's longer than 3 sentences, misses the "
            "definition, or is equally informal."
        ),
        expected_keywords=["retrieval", "augmented", "generation"],
        max_tokens=256,
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# Lookup
# ═══════════════════════════════════════════════════════════════════════════

BENCHMARKS_BY_ROLE: dict[str, list[Benchmark]] = {
    "big": BIG_BENCHMARKS,
    "small": SMALL_BENCHMARKS,
}


def get_benchmarks(role: str) -> list[Benchmark]:
    """Return the benchmark suite for a cartridge role ('big' or 'small')."""
    return BENCHMARKS_BY_ROLE.get(role, [])
