---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: "Evaluate a vault note on three axes — Faithfulness, Connectivity, Utility — by composing existing procedures. Thin orchestrator that calls Cross-Check-Claims (faithfulness), Note-Quality-Score (connectivity), and Evaluate-Retrieval (utility) with conditional branches based on note type. Returns a triadic score with per-axis reasoning. Backed by RAG evaluation research."
when_to_use: "when assessing a note's overall quality across all three knowledge dimensions, when doing a self-audit, or when deciding whether a note needs improvement"
falsifiable_if: "a note that scores high on all three axes is later found to contain hallucinated claims, be disconnected from the vault graph, or fail to answer questions it should answer"
applies_to:
  - vault-quality
  - self-assessment
  - note-evaluation
  - audit
depends_on:
  - "[[Cross-Check-Claims]]"
  - "[[Note-Quality-Score]]"
  - "[[Evaluate-Retrieval]]"
  - "[[Verify-Claims]]"
sources:
  - "[[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]"
  - "[[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]]"
  - "[[AI-system-audit-categories-how-to-audit-an-AI-agent-system-for-reliability-knowl]]"
  - "[[Claim-Verification-for-Vault-Notes]]"
allowed_tools:
  - vault_search
  - vault_list
  - code_read
  - vault_lint
  - llm_generate
  - run_procedure
---

# Self-Assessment-Using-the-Knowledge-Triad

## Research Backing

The Knowledge Triad is grounded in the RAG evaluation framework from [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]], which identifies three core dimensions for evaluating retrieval-augmented generation systems:

1. **Faithfulness** — Are generated claims grounded in retrieved sources? (RAGAS faithfulness metric)
2. **Answer Relevance** — Does the retrieved context actually help answer the question?
3. **Context Relevance** — Is the retrieved information useful and on-topic?

These map to VaultBot's vault notes as:

- **Faithfulness** → Are the note's claims supported by its cited web sources?
- **Connectivity** → Is the note linked into the vault graph (incoming + outgoing wikilinks)?
- **Utility** → When this note is retrieved for a question, does it actually help answer it?

The [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] research shows that rubric design, bias testing, and human calibration are essential for converting LLM-as-judge from a misleading shortcut into a reliable quality signal. This procedure uses structured rubrics for each axis.

The [[AI-system-audit-categories-how-to-audit-an-AI-agent-system-for-reliability-knowl]] research provides the audit framework: reliability, knowledge quality, tool safety, retrieval accuracy, and self-improvement dimensions. The Triad focuses on the knowledge-quality and retrieval-accuracy dimensions.

[[Claim-Verification-for-Vault-Notes]] describes the verification layer this procedure implements: the post-generation check that synthesized claims are faithful to their sources.

## When to Run This

- After writing a new research note (self-assessment before finalizing)
- During a vault audit (batch-assess notes to prioritize improvements)
- When the operator asks "how good is this note?"
- As part of the [[Route-Task]] procedure's vault-maintenance branch

Do NOT run on: chat logs, sacred journal files, or locked notes.

## The Triad

### Axis 1: Faithfulness (Are claims supported by sources?)

**Question:** Does every factual claim in the note trace back to a cited source that actually supports it?

**Procedure:** Run [[Cross-Check-Claims]] on the note. This extracts claims with source URLs, fetches each source, and checks entailment.

**Scoring rubric (backed by [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] rubric design principles):**
- **5 (Excellent):** All claims have sources and all claims are supported by their sources. Zero unsupported claims.
- **4 (Good):** All claims have sources. 1-2 claims are weakly supported (source is tangentially related but doesn't directly state the claim).
- **3 (Adequate):** Most claims have sources. 1-2 claims have no source or are contradicted by their source.
- **2 (Poor):** Many claims lack sources or are contradicted. Significant hallucination risk.
- **1 (Failing):** Most claims are unsourced or contradicted. Note is unreliable.

**Conditional logic:**
- IF note has web source URLs → run [[Cross-Check-Claims]] for full verification
- IF note has no web sources but references vault notes → check if referenced notes support the claims (manual cross-reference via vault_search)
- IF note has no sources at all → score = 1 (Failing) and flag for research backing

### Axis 2: Connectivity (Is the note linked into the vault graph?)

**Question:** Does the note have meaningful incoming and outgoing wikilinks? Is it embedded in the knowledge graph?

**Procedure:** Run [[Note-Quality-Score]] which checks links, frontmatter, and content. Also run `vault_lint` to identify broken wikilinks.

**Scoring rubric:**
- **5 (Excellent):** 5+ outgoing wikilinks to existing notes, 3+ incoming wikilinks from other notes, no broken links, has frontmatter with type/status/tags.
- **4 (Good):** 3+ outgoing wikilinks, 1+ incoming wikilinks, no broken links, has frontmatter.
- **3 (Adequate):** 1-2 outgoing wikilinks, 0 incoming wikilinks, no broken links, has frontmatter.
- **2 (Poor):** 0-1 wikilinks, or has broken wikilinks, or missing frontmatter.
- **1 (Isolated):** No wikilinks at all, no frontmatter. Note is an island.

**Conditional logic:**
- IF note has broken wikilinks → flag each broken link for gap-filling (feed to [[Gap-Fill]] procedure)
- IF note has 0 incoming wikilinks → flag as orphan (may need to be linked from related notes)
- IF note has 0 outgoing wikilinks → flag as dead-end (may need to link to related concepts)

### Axis 3: Utility (Does the note help answer questions?)

**Question:** When this note is retrieved for a relevant query, does it actually provide useful information?

**Procedure:** Run [[Evaluate-Retrieval]] with the note's title as a test query. Check if the note surfaces in results and whether its content is relevant.

**Scoring rubric (backed by RAG answer-relevance and context-relevance metrics from [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]]):**
- **5 (Excellent):** Note surfaces as top-3 result for its primary topic. Content directly answers the question. Dense with relevant facts.
- **4 (Good):** Note surfaces in top-5 results. Content is mostly relevant with some useful facts.
- **3 (Adequate):** Note surfaces in top-10 results. Content is partially relevant.
- **2 (Poor):** Note does not surface in top-10, or content is mostly irrelevant to the query.
- **1 (Failing):** Note is not retrievable for its own topic, or content is empty/thin.

**Conditional logic:**
- IF [[Evaluate-Retrieval]] is not yet available → use vault_search with the note's title as query and check if it appears in top-5
- IF note is thin (under 200 words) → flag for expansion and score max 2
- IF note is a procedure or system note → skip retrieval test (procedures are invoked, not retrieved) and score based on whether it has clear when_to_use and description fields

## Steps

### Step 1: Read the note and determine its type

1. ```python
import json, os, re

note_path = args.get("note_path", "")
if not note_path:
    result = json.dumps({"error": "note_path argument required"})
else:
    p = Path(vault_path) / note_path
    if not p.exists():
        p = Path(note_path)
    if not p.exists():
        result = json.dumps({"error": f"note not found: {note_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        # Parse frontmatter
        fm = {}
        fm_match = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    fm[key.strip()] = val.strip()
        note_type = fm.get("type", "unknown")
        # Count wikilinks
        outgoing = re.findall(r'\[\[([^\]]+)\]\]', text)
        # Count words
        body = re.sub(r'^---\n.*?\n---', '', text, flags=re.DOTALL)
        word_count = len(body.split())
        # Check for source URLs
        urls = re.findall(r'https?://[^\s\)]+', text)
        result = json.dumps({
            "note_path": note_path,
            "note_type": note_type,
            "word_count": word_count,
            "outgoing_links": len(outgoing),
            "has_web_sources": len(urls) > 0,
            "url_count": len(urls),
            "frontmatter": fm
        }, indent=2)
```

### Step 2: Evaluate Faithfulness axis

**IF note_type is "research" AND has_web_sources is True:**
Run [[Cross-Check-Claims]] with `note_path` to verify claims against web sources. Score using the Faithfulness rubric above based on the number of unsupported claims returned.

**IF note_type is "research" AND has_web_sources is False:**
The note has no web sources to verify against. Search the vault for notes on the same topic and check if the claims are consistent with existing vault knowledge. Score using the rubric — max score 3 without web sources.

**IF note_type is NOT "research":**
Skip faithfulness check. Non-research notes (procedures, directives, chat logs) don't make factual claims that need source verification. Score = N/A.

1. ```python
import json

note_info = json.loads(args.get("step1_result", "{}"))
note_type = note_info.get("note_type", "unknown")
has_sources = note_info.get("has_web_sources", False)

if note_type == "research" and has_sources:
    action = "run_procedure"
    procedure = "Cross-Check-Claims"
    procedure_args = {"note_path": note_info["note_path"]}
    result = json.dumps({
        "axis": "faithfulness",
        "action": "run_cross_check_claims",
        "procedure": procedure,
        "procedure_args": procedure_args,
        "note": "Running Cross-Check-Claims to verify claims against web sources"
    })
elif note_type == "research" and not has_sources:
    action = "vault_search"
    result = json.dumps({
        "axis": "faithfulness",
        "action": "vault_cross_reference",
        "note": "No web sources. Will cross-reference claims against existing vault notes via vault_search."
    })
else:
    result = json.dumps({
        "axis": "faithfulness",
        "action": "skip",
        "score": "N/A",
        "note": f"Note type '{note_type}' does not require faithfulness verification."
    })
```

### Step 3: Evaluate Connectivity axis

Run `vault_lint` on the note to check for broken wikilinks and structural issues. Count incoming wikilinks by searching the vault for references to this note's title.

1. ```python
import json, re, os

note_info = json.loads(args.get("step1_result", "{}"))
note_path = note_info.get("note_path", "")
# Extract note title from path
note_title = os.path.splitext(os.path.basename(note_path))[0]

# Count outgoing links
outgoing_count = note_info.get("outgoing_links", 0)

# Count incoming links by searching vault
incoming = 0
vault_files = []
for root, dirs, files in os.walk(vault_path):
    for f in files:
        if f.endswith('.md'):
            vault_files.append(os.path.join(root, f))

for vf in vault_files:
    if os.path.basename(vf) == os.path.basename(note_path):
        continue  # skip self
    try:
        content = open(vf, 'r', encoding='utf-8', errors='replace').read()
        if f'[[{note_title}]]' in content:
            incoming += 1
    except:
        pass

# Score connectivity
has_fm = bool(note_info.get("frontmatter"))
if outgoing_count >= 5 and incoming >= 3 and has_fm:
    score = 5
elif outgoing_count >= 3 and incoming >= 1 and has_fm:
    score = 4
elif outgoing_count >= 1 and has_fm:
    score = 3
elif outgoing_count <= 1 or not has_fm:
    score = 2 if outgoing_count >= 1 else 1
else:
    score = 1

result = json.dumps({
    "axis": "connectivity",
    "outgoing_links": outgoing_count,
    "incoming_links": incoming,
    "has_frontmatter": has_fm,
    "score": score,
    "note_title": note_title,
    "flags": []
}, indent=2)
```

After this step, also run `vault_lint` to check for broken wikilinks. If broken links are found, subtract 1 from the connectivity score and flag each broken link for gap-filling.

### Step 4: Evaluate Utility axis

**IF note_type is "procedure":**
Check if the procedure has `when_to_use`, `description`, and `falsifiable_if` fields in frontmatter. Score based on completeness of these fields. Skip retrieval test.

**IF note_type is "research" or "knowledge":**
Run `vault_search` with the note's title as the query. Check if the note appears in the top-5 results. Score using the Utility rubric.

**IF note is thin (word_count < 200):**
Score max 2 regardless of retrieval performance. Flag for expansion.

1. ```python
import json

note_info = json.loads(args.get("step1_result", "{}"))
note_type = note_info.get("note_type", "unknown")
word_count = note_info.get("word_count", 0)
fm = note_info.get("frontmatter", {})

if note_type == "procedure":
    has_when = bool(fm.get("when_to_use", ""))
    has_desc = bool(fm.get("description", ""))
    has_falsifiable = bool(fm.get("falsifiable_if", ""))
    completeness = sum([has_when, has_desc, has_falsifiable])
    score = 2 + completeness  # 2-5 range
    result = json.dumps({
        "axis": "utility",
        "action": "procedure_check",
        "has_when_to_use": has_when,
        "has_description": has_desc,
        "has_falsifiable_if": has_falsifiable,
        "score": score,
        "note": "Procedure notes are invoked, not retrieved. Scored on metadata completeness."
    })
elif word_count < 200:
    result = json.dumps({
        "axis": "utility",
        "action": "flag_thin",
        "score": min(2, 1),
        "word_count": word_count,
        "note": "Note is thin (<200 words). Flagged for expansion. Max score 2."
    })
else:
    result = json.dumps({
        "axis": "utility",
        "action": "run_vault_search",
        "note": f"Run vault_search with note title as query to check retrievability."
    })
```

If the action is `run_vault_search`, run `vault_search` with the note's title as the query and check if the note appears in the top-5 results. Score:
- Top-3 result → 5
- Top-5 result → 4
- Top-10 result → 3
- Not in top-10 → 2
- Not retrievable → 1

### Step 5: Synthesize triadic score

Combine the three axis scores into a final assessment. Use the small model to generate a brief summary of the note's quality across all three axes, including specific improvement recommendations.

1. ```python
import json

# Collect scores from previous steps
faithfulness = args.get("faithfulness_score", "N/A")
connectivity = args.get("connectivity_score", 0)
utility = args.get("utility_score", 0)

# Calculate overall (only if all axes were scored)
scores = []
if faithfulness != "N/A":
    scores.append(faithfulness)
scores.append(connectivity)
scores.append(utility)

overall = sum(scores) / len(scores) if scores else 0

result = json.dumps({
    "triad": {
        "faithfulness": faithfulness,
        "connectivity": connectivity,
        "utility": utility
    },
    "overall": round(overall, 2),
    "recommendation": "See LLM synthesis below for improvement suggestions."
}, indent=2)
```

2. [llm] Given the triadic scores (faithfulness: {faithfulness}, connectivity: {connectivity}, utility: {utility}), write a 2-3 sentence assessment of this note's quality and 1-2 specific improvement recommendations. Focus on the lowest-scoring axis. Be concise and actionable.

## Conditional Dispatch Summary

| Condition | Faithfulness | Connectivity | Utility |
|-----------|-------------|-------------|---------|
| Research note with web sources | Run [[Cross-Check-Claims]] | Run vault_lint + count links | Run vault_search retrieval test |
| Research note without web sources | Cross-reference via vault_search | Run vault_lint + count links | Run vault_search retrieval test |
| Procedure note | Skip (N/A) | Run vault_lint + count links | Check metadata completeness |
| Thin note (<200 words) | Run [[Cross-Check-Claims]] if sources exist | Run vault_lint + count links | Flag for expansion, max score 2 |
| Chat log / directive | Skip (N/A) | Run vault_lint + count links | Skip (N/A) |

## Related
- [[RAG-evaluation-metrics-how-to-measure-retrieval-quality-in-retrieval-augmented-g]] — research backing for the triad framework
- [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — research backing for rubric design and LLM-as-judge calibration
- [[AI-system-audit-categories-how-to-audit-an-AI-agent-system-for-reliability-knowl]] — research backing for the audit framework
- [[Claim-Verification-for-Vault-Notes]] — the verification layer this implements
- [[Cross-Check-Claims]] — composed procedure for faithfulness axis
- [[Note-Quality-Score]] — composed procedure for connectivity axis
- [[Evaluate-Retrieval]] — composed procedure for utility axis
- [[Verify-Claims]] — broader verification procedure that includes vault cross-referencing
- [[Route-Task]] — dispatches to this procedure for vault-maintenance tasks
- [[Procedure-Composition-Patterns]] — how this procedure composes others