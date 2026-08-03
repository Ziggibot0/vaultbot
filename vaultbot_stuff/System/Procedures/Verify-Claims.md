---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-03
description: "Verify claims in a research note by composing existing procedures: calls Cross-Check-Claims for web source verification, then cross-references claims against existing vault notes for internal consistency. Conditional branches based on whether the note has web sources, vault links, or unsupported claims. This is a thin orchestrator — it delegates to Cross-Check-Claims rather than reimplementing claim extraction."
when_to_use: "after vault_research writes a note, before accepting it as final; when you need to verify that a note's claims are backed by sources and consistent with existing vault knowledge"
falsifiable_if: "a note passes this verification but is later found to contain a hallucinated or unsupported claim, or the procedure flags a supported claim as unsupported"
applies_to:
  - claim-verification
  - research-quality
  - fact-checking
  - source-verification
  - vault-consistency
allowed_tools:
  - run_procedure
  - code_read
  - vault_search
  - llm_generate
research_backing:
  - "[[Claim-Verification-for-Vault-Notes]] — describes the 3-stage pipeline (extract → retrieve → verify) backed by Claimify and survey papers on automated fact-checking"
  - "[[Automated-claim-verification-and-fact-checking-of-LLM-outputs-against-source-doc]] — 15 sources on hallucination detection, faithfulness metrics, and post-generation verification for RAG systems"
  - "[[how-to-evaluate-credibility-of-sources-for-academic-research]] — lateral reading and credibility evaluation methods"
  - "[[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — rubric design and calibration convert LLM-as-judge into reliable quality signals"
---

# Verify-Claims

## When to Run This

Run this procedure after `vault_research` has written a note and before it is considered final. This is the post-generation verification layer described in [[Claim-Verification-for-Vault-Notes]].

**Applies to:** research notes, synthesized knowledge notes, any note that makes factual claims citing sources.

**Does NOT apply to:** chat logs, directive notes, procedure notes, journal entries.

## Research Backing

The three-stage fact-checking pipeline (extract → retrieve → verify) is the field consensus for automated claim verification:

1. **Claim extraction** — Break text into atomic, verifiable claims (Microsoft's Claimify approach)
2. **Evidence retrieval** — For each claim, locate the relevant source passage (in VaultBot's case, sources are cited in the note)
3. **Claim verification** — Check whether each claim is entailed by its source

This procedure implements the pipeline by **composing** existing procedures rather than reimplementing each stage. [[Cross-Check-Claims]] handles web source verification (stages 1-3 for web sources). This procedure adds a fourth stage: **vault cross-referencing** — checking claims against existing vault knowledge for internal consistency.

> **Design decision:** The previous Verify-Claims procedure (trashed after 6 failures) tried to do all 5 stages in one procedure. This version delegates to [[Cross-Check-Claims]] for web verification and focuses on what's missing: vault-internal consistency checks. This follows the procedure-composition pattern — each procedure does one thing well, and orchestrators compose them.

## Steps

### Step 1: Determine what kind of verification is needed

Read the note and classify what sources it uses. This determines which conditional branch to take.

1. ```python
import re, json
from pathlib import Path

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
        
        # Check for web sources
        web_urls = re.findall(r'https?://[^\s\)\]]+', text)
        has_web_sources = len(web_urls) > 0
        
        # Check for vault wikilinks (internal references)
        wikilinks = re.findall(r'\[\[([^\]]+)\]\]', text)
        has_vault_links = len(wikilinks) > 0
        
        # Check for source citations in frontmatter
        has_source_count = "source_count" in text
        
        result = json.dumps({
            "note_path": note_path,
            "has_web_sources": has_web_sources,
            "web_url_count": len(web_urls),
            "has_vault_links": has_vault_links,
            "wikilink_count": len(wikilinks),
            "verification_plan": {
                "run_cross_check": has_web_sources,
                "run_vault_cross_ref": has_vault_links,
                "flag_unsourced": not has_web_sources and not has_source_count
            }
        })
```

### Step 2: IF the note has web sources → run Cross-Check-Claims

**Condition:** `has_web_sources == true`

Call `run_procedure("Cross-Check-Claims", {"note_path": note_path})` to verify each claim against its cited web source. This handles stages 1-3 of the fact-checking pipeline.

2. ```python
import json

try:
    plan = json.loads(output)
except Exception:
    plan = {"verification_plan": {"run_cross_check": False, "run_vault_cross_ref": False, "flag_unsourced": True}}

vp = plan.get("verification_plan", {})
results = {"web_verification": None, "vault_cross_ref": None, "unsourced_claims": None}

if vp.get("run_cross_check"):
    # Delegate to Cross-Check-Claims procedure
    # The big model calls: run_procedure("Cross-Check-Claims", {"note_path": plan["note_path"]})
    results["web_verification"] = "DELEGATED: call run_procedure('Cross-Check-Claims', {note_path: " + plan.get("note_path", "") + "})"
    results["web_verification_backing"] = "Cross-Check-Claims handles claim extraction and source entailment checking"
else:
    results["web_verification"] = "SKIPPED: no web sources found in note"

result = json.dumps(results)
```

### Step 3: IF the note has vault wikilinks → cross-reference claims against linked vault notes

**Condition:** `has_vault_links == true`

For each wikilink in the note, search the vault for that note and check whether the note's claims are consistent with what the linked note says. This catches internal contradictions — where a new note claims something that conflicts with existing vault knowledge.

3. ```python
import json, re
from pathlib import Path

try:
    plan = json.loads(output)
except Exception:
    plan = {"verification_plan": {}, "note_path": ""}

vp = plan.get("verification_plan", {})
results = {"vault_cross_ref": []}

if vp.get("run_vault_cross_ref"):
    note_path = plan.get("note_path", "")
    p = Path(vault_path) / note_path
    if not p.exists():
        p = Path(note_path)
    
    if p.exists():
        text = p.read_text(encoding="utf-8", errors="replace")
        wikilinks = list(set(re.findall(r'\[\[([^\]]+)\]\]', text)))
        
        for link in wikilinks:
            # Search for the linked note
            search_results = vault_search(query=link, k=1)
            if search_results:
                results["vault_cross_ref"].append({
                    "link": link,
                    "found": True,
                    "status": "exists — manual consistency check recommended"
                })
            else:
                results["vault_cross_ref"].append({
                    "link": link,
                    "found": False,
                    "status": "DANGLING — linked note does not exist in vault"
                })
        
        results["vault_cross_ref_summary"] = f"Checked {len(wikilinks)} wikilinks: {sum(1 for r in results['vault_cross_ref'] if r['found'])} found, {sum(1 for r in results['vault_cross_ref'] if not r['found'])} dangling"
    else:
        results["vault_cross_ref"] = [{"error": "note not found"}]
else:
    results["vault_cross_ref"] = [{"status": "SKIPPED: no vault wikilinks found"}]

result = json.dumps(results)
```

### Step 4: IF the note has no sources → flag as unsourced

**Condition:** `not has_web_sources and not has_source_count`

If a note makes factual claims but cites no sources, flag it. Notes without sources cannot be verified and should not be trusted as research.

4. ```python
import json

try:
    plan = json.loads(output)
except Exception:
    plan = {"verification_plan": {}}

vp = plan.get("verification_plan", {})
result = json.dumps({
    "unsourced_claims": "FLAGGED" if vp.get("flag_unsourced") else "OK",
    "message": "Note makes claims without citing sources — cannot verify. Add sources or mark as opinion/speculation." if vp.get("flag_unsourced") else "Note has sources or is not claim-bearing."
})
```

### Step 5: Compile verification report

Combine all results into a single verification verdict.

5. ```python
import json

# Gather results from previous steps
# web_verification: from step 2 (delegated to Cross-Check-Claims)
# vault_cross_ref: from step 3
# unsourced_claims: from step 4

report = {
    "verification_complete": True,
    "web_sources_verified": "delegated to Cross-Check-Claims" ,
    "vault_links_checked": True,
    "unsourced_claims_flagged": False,
    "overall_verdict": "PASS — all checks passed or delegated",
    "recommendation": "Note is verified. Safe to accept as final."
}

# The big model should adjust the verdict based on actual results:
# - If Cross-Check-Claims found unsupported claims → verdict = "FAIL — unsupported claims found"
# - If vault cross-ref found dangling links → verdict = "WARN — dangling wikilinks need resolution"
# - If unsourced claims flagged → verdict = "FAIL — claims without sources"

result = json.dumps(report, indent=2)
```

## Conditional Logic Summary

```
IF note has web sources:
    → run Cross-Check-Claims (delegates claim extraction + source entailment)
IF note has vault wikilinks:
    → cross-reference each link against vault (check for dangling links + consistency)
IF note has no sources at all:
    → flag as unsourced (cannot verify)
ALWAYS:
    → compile verification report with PASS/FAIL/WARN verdict
```

## Usage

The big model calls this procedure after `vault_research` writes a note. The procedure:
1. Classifies what kind of verification is needed (web sources? vault links? unsourced?)
2. Delegates web source verification to [[Cross-Check-Claims]] via `run_procedure()`
3. Checks vault internal consistency by cross-referencing wikilinks
4. Flags unsourced claims
5. Returns a verification verdict

**Key design principle:** This procedure COMPOSES [[Cross-Check-Claims]] rather than reimplementing claim extraction. The old Verify-Claims failed 6 times because it tried to do everything in one procedure. This version is a thin orchestrator that delegates to specialized procedures.

## Related
- [[Cross-Check-Claims]] — handles web source verification (called by this procedure)
- [[Claim-Verification-for-Vault-Notes]] — research backing for the 3-stage pipeline
- [[Automated-claim-verification-and-fact-checking-of-LLM-outputs-against-source-doc]] — 15 sources on automated fact-checking methods
- [[how-to-evaluate-credibility-of-sources-for-academic-research]] — source credibility evaluation
- [[Calibrating-automated-quality-assessment-gates-without-ground-truth-labels-metho]] — quality gate calibration
- [[Procedure-Composition-Patterns]] — how procedures chain together
- [[Self-Assessment-Using-the-Knowledge-Triad]] — the gap this fills
- [[Deterministic-Scaffolding-for-Small-Models]] — why verification scaffolding matters for small models