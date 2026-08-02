---
type: procedure
status: verified
model_cartridge: big
created: 2026-07-27
last_reviewed: 2026-07-31
review_interval_days: 30
success_count: 23
failure_count: 1
success_rate: 0.96
description: "Vault dreaming: scan for orphan nodes, connect them to the graph (idempotent), consolidate cross-session patterns into semantic notes via LLM synthesis, prune junk, and validate the graph is healthier. 6 steps, all deterministic except Step 3 (LLM synthesis). Safe to run repeatedly."
when_to_use: "when the vault feels disorganized — orphan notes accumulating, cross-session patterns unconsolidated, or periodic memory-consolidation maintenance is due"
falsifiable_if: "the vault has more orphan islands after dreaming than before, or the operator reports the vault is still disorganized"
applies_to:
  - vault-maintenance
  - memory-consolidation
  - graph-organization
  - dreaming
depends_on:
  - "[[Semantic-Consolidation-Architecture]]"
  - "How to Organize a Knowledge Base"
  - "[[Cross-Session-Patterns-from-75-Chat-Logs]]"
sources:
  - "https://github.com/itechmeat/open-second-brain"
  - "https://arxiv.org/abs/2605.20616v1"
  - "https://arxiv.org/abs/2303.11366v4"
allowed_tools:
  - vault_graph_analyzer
  - vault_list
  - vault_append
  - vault_delete
  - vault_lint
  - vault_search
  - code_read
  - llm_generate
  - run_procedure
---

# Dream Pass

## When to Run This

Run this procedure when:
- The vault has accumulated orphan notes (isolated islands in the graph)
- the operator asks to "organize," "prune," "consolidate," or "dream"
- The autonomous researcher has created many new notes that aren't connected
- Periodically (e.g., every 10-20 new chat logs or research notes)

This is the "sleep-time" processing pass — like open-second-brain's dream pass: "turns repeat signals into rules and retires the ones nothing uses any more." The dreaming process is mostly deterministic: graph analysis, file listing, and link building. The LLM only synthesizes cross-session patterns into semantic notes, and it gets shown everything it needs — extracted patterns, gaps from all quality modules, the exemplar format, and existing notes for dedup.

## What Dreaming Does

Based on research into AI agent dreaming mechanisms:

1. **open-second-brain / Hermes Agent**: "A nightly dream pass turns repeated corrections into rules and retires the ones nothing uses any more." Deterministic by design — counters and atomic file moves, no LLM guessing inside memory.
2. **Auto-Dreamer**: Decouples fast per-session memory acquisition from slow cross-session consolidation. Inspired by complementary learning systems theory.
3. **Generative Agents (Park et al. 2023)**: Agents periodically cluster recent observations and synthesize higher-order reflections. Removing reflection causes behavior to degenerate within 48 simulated hours.
4. **Semantic-Consolidation-Architecture**: The 6-phase pipeline (Scan → Extract → Cluster → Synthesize → Validate → Store) that formalizes this for VaultBot.

The dreaming process has 6 phases: **Journal → Scan → Connect → Consolidate → Prune → Validate**.

## Steps

### Step 0: Journal Integration — Read the operator's Psyche

Before any other step, check for new journal entries (date-only filenames like `2026-07-25.md`).

0. ```python
import os, re, datetime

vault_root = os.environ.get("VAULT_PATH", ".")
today = datetime.date.today().isoformat() + ".md"
journal_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')

journal_files = []
for f in sorted(os.listdir(vault_root)):
    if journal_pattern.match(f):
        filepath = os.path.join(vault_root, f)
        with open(filepath, encoding='utf-8') as fh:
            content = fh.read().strip()
        if f == today:
            if content:
                journal_files.append({"file": f, "content": content, "chars": len(content)})
            # Keep today's even if empty — the operator might still write
            continue
        if content:
            journal_files.append({"file": f, "content": content, "chars": len(content)})
        else:
            # Delete empty past journals — no thoughts to protect
            vault_delete(f)

result = json.dumps({"journal_entries": [j["file"] for j in journal_files], "total_chars": sum(j["chars"] for j in journal_files)})
```

For each non-empty journal entry: read it, extract topics, research them with `vault_research`, write linked notes that link TO the journal entry via `[[2026-07-25]]`. The journal is the operator's raw thought signal — treat it as gold. NEVER modify or delete non-empty journals.

### Step 1: Scan — Map the Vault's Current State

Run `vault_graph_analyzer` to find orphan islands and measure connectivity.

1. ```python
result = vault_graph_analyzer()
data = result["analysis"]
total_islands = data["num_islands"]
isolated = data["isolated_nodes"]
largest_island = data["largest_island_size"]
connectivity = data["connectivity_ratio"]

# Categorize isolated nodes by path prefix
chat_orphans = [n for n in isolated if "Chat-" in n]
research_orphans = [n for n in isolated if "vaultbot/research/" in n]
system_files = [n for n in isolated if n in ["GOALS", "SECURITY"]
                or "vaultbot_backend/identity/" in n]
junk_files = [n for n in isolated if "_20260726" in n or "baseline_" in n]


result = json.dumps({
    "total_islands": total_islands,
    "isolated_nodes": isolated,
    "connectivity": connectivity,
    "chat_orphans": chat_orphans,
    "research_orphans": research_orphans,
    "junk_files": junk_files,
})
```

[validate: at_least 1 islands]

### Step 2: Connect — Link Orphan Nodes to the Graph

Categorize each orphan and connect it to the appropriate hub note using idempotent linking (no duplicate links on re-run).

2. ```python
import re, os, json

# Extract data from Step 1's output
_step1_data = json.loads(prior_results[-1]) if prior_results else {}
chat_orphans = _step1_data.get("chat_orphans", [])
research_orphans = _step1_data.get("research_orphans", [])
isolated = _step1_data.get("isolated_nodes", [])

def strip_md(name):
    """Remove .md extension and directory prefix for clean wikilinks."""
    name = name.replace("\\", "/")
    name = name.split("/")[-1]
    if name.endswith(".md"):
        name = name[:-3]
    return name

def link_exists_in_note(note_path, target_stem):
    try:
        with open(note_path, encoding='utf-8') as f:
            content = f.read()
        pattern = r'\[\[' + re.escape(target_stem) + r'(?:\.md)?(?:\|[^\]]+)?\]\]'
        return bool(re.search(pattern, content))
    except:
        return False

def extract_chat_description(vault_root, chat_stem):
    """Extract first user message from a chat log as a description."""
    chat_path = os.path.join(vault_root, "vaultbot", "chat", chat_stem + ".md")
    if not os.path.exists(chat_path):
        chat_path = os.path.join(vault_root, chat_stem + ".md")
    try:
        with open(chat_path, encoding='utf-8') as f:
            content = f.read()
        match = re.search(r'\*\*User:\*\*\s*(.+?)(?:\n\n|\n##|\Z)', content, re.DOTALL)
        if match:
            desc = match.group(1).strip().replace('\n', ' ')
            if len(desc) > 100:
                desc = desc[:97] + "..."
            return desc
    except:
        pass
    return "auto-linked by Dream-Pass"

def merge_duplicate_sections(note_path, section_title):
    """Merge all instances of a section into one, dedup links."""
    try:
        with open(note_path, encoding='utf-8') as f:
            content = f.read()
    except:
        return
    pattern = re.compile(r'^(##\s+' + re.escape(section_title) + r'\s*\n)(.*?)(?=^##\s+|\Z)', re.MULTILINE | re.DOTALL)
    matches = list(pattern.finditer(content))
    if len(matches) <= 1:
        return
    all_links = []
    seen_links = set()
    for m in matches:
        body = m.group(2)
        for line in body.strip().split('\n'):
            line = line.strip()
            if line.startswith('- [['):
                wl_match = re.match(r'-\s*\[\[([^\]|]+)', line)
                if wl_match:
                    target = wl_match.group(1).strip()
                    if target not in seen_links:
                        seen_links.add(target)
                        all_links.append(line)
    merged = f"## {section_title}\n" + "\n".join(all_links) + "\n\n"
    result = content
    for i, m in enumerate(reversed(matches)):
        actual_idx = len(matches) - 1 - i
        if actual_idx == 0:
            result = result[:matches[0].start()] + merged + result[matches[0].end():]
        else:
            result = result[:matches[actual_idx].start()] + result[matches[actual_idx].end():]
    with open(note_path, 'w', encoding='utf-8') as f:
        f.write(result)

def idempotent_connect(orphan_stems, hub_path, vault_root, section_title="Related"):
    hub_full = os.path.join(vault_root, hub_path)
    # First, merge any existing duplicate sections from prior runs
    merge_duplicate_sections(hub_full, section_title)
    clean_stems = [strip_md(n) for n in orphan_stems]
    new_links = [n for n in clean_stems if not link_exists_in_note(hub_full, n)]
    skipped = [n for n in clean_stems if n not in new_links]
    if new_links:
        link_lines = []
        for n in new_links:
            desc = extract_chat_description(vault_root, n)
            link_lines.append(f"- [[{n}]] \u2014 {desc}")
        links_block = "\n" + "\n".join(link_lines)
        try:
            with open(hub_full, encoding='utf-8') as f:
                hub_content = f.read()
        except:
            hub_content = ""
        section_pattern = re.compile(r'^##\s+' + re.escape(section_title) + r'\s*$', re.MULTILINE)
        if section_pattern.search(hub_content):
            match = section_pattern.search(hub_content)
            insert_pos = match.end()
            next_section = re.search(r'^##\s+', hub_content[insert_pos:], re.MULTILINE)
            if next_section:
                insert_end = insert_pos + next_section.start()
            else:
                insert_end = len(hub_content)
            new_content = hub_content[:insert_end].rstrip() + links_block + "\n\n" + hub_content[insert_end:]
            with open(hub_full, 'w', encoding='utf-8') as f:
                f.write(new_content)
        else:
            vault_append(hub_path, f"\n## {section_title}\n" + "\n".join(link_lines))
    return new_links, skipped

vault_root = os.environ.get("VAULT_PATH", ".")

# FIX: Strip path prefix BEFORE keyword matching to prevent false matches
# (e.g., "vault" in "vaultbot/chat/..." matching design keyword "vault")
clean_orphans = [strip_md(n) for n in chat_orphans]

# Keyword categories \u2014 match on clean filename only, not full path
testing_chats = [n for n in clean_orphans if any(k in n.lower() for k in
    ["sure", "certain", "check", "confidant", "test", "verify", "safe", "break", "broke", "kill", "checked", "double-check"])]
build_chats = [n for n in clean_orphans if any(k in n.lower() for k in
    ["backend", "tool", "implement", "docker", "orphan", "graph", "build", "phase", "code", "python", "fix", "write", "create", "step", "procedure", "run", "error", "import", "module", "config"])]
design_chats = [n for n in clean_orphans if any(k in n.lower() for k in
    ["pivot", "system", "keep-up", "typed", "design", "architect", "plan", "structure", "organize", "vault", "model", "saturat", "cloud", "local", "agi", "obsolet", "fractal", "entropy"])]
research_chats = [n for n in clean_orphans if any(k in n.lower() for k in
    ["research", "gaps", "would-doing", "source", "credib", "wikipedia", "epistem", "hermeneut", "ontolog", "knowledge", "learn", "textbook", "ingest", "consolidat", "semantic", "pattern", "dream"])]
comm_chats = [n for n in clean_orphans if any(k in n.lower() for k in
    ["sup", "homie", "hey", "what", "status", "report", "progress", "save", "stale", "junk", "empty", "slop", "read", "didnt", "dont", "cant", "wont", "yes", "no", "ok", "begin", "go", "stop", "wait", "actually", "really", "tbh", "honest"])]

# FIX: Make categories EXCLUSIVE \u2014 priority: testing > build > design > research > comm
# (prevents same orphan being linked to multiple hubs)
testing_set = set(testing_chats)
build_chats = [n for n in build_chats if n not in testing_set]
build_set = set(build_chats)
design_chats = [n for n in design_chats if n not in testing_set and n not in build_set]
design_set = set(design_chats)
research_chats = [n for n in research_chats if n not in testing_set and n not in build_set and n not in design_set]
research_set = set(research_chats)
comm_chats = [n for n in comm_chats if n not in testing_set and n not in build_set and n not in design_set and n not in research_set]

new1, skip1 = idempotent_connect(testing_chats, "Testing-and-Verification-History.md", vault_root, "Additional Testing Chats")
new2, skip2 = idempotent_connect(build_chats, "VaultBot-Build-Log.md", vault_root, "Additional Build Chats")
new3, skip3 = idempotent_connect(design_chats, "the operator-Design-Decisions.md", vault_root, "Additional Design Chats")
new4, skip4 = idempotent_connect(research_chats, "Cross-Session-Patterns-from-75-Chat-Logs.md", vault_root, "Additional Research Chats")
new5, skip5 = idempotent_connect(comm_chats, "the operator-Communication-Preferences.md", vault_root, "Additional Communication Chats")

# Fallback: uncategorized orphans -> vault_search to find closest hub
all_categorized = set()
for group in [testing_chats, build_chats, design_chats, research_chats, comm_chats]:
    for n in group:
        all_categorized.add(n)
uncategorized = [n for n in clean_orphans if n not in all_categorized]
search_linked = []
unresolved = []

# vault_search pass for uncategorized orphans
for orphan in uncategorized:
    search_result = vault_search(orphan.replace("-", " "), k=3)
    if search_result and len(search_result) > 0:
        best_match = search_result[0]
        if isinstance(best_match, dict):
            hub = best_match.get("filename", best_match.get("title", ""))
        else:
            hub = str(best_match)
        hub = strip_md(hub)
        if hub and hub != orphan:
            hub_path = hub + ".md"
            if not link_exists_in_note(os.path.join(vault_root, hub_path), orphan):
                desc = extract_chat_description(vault_root, orphan)
                vault_append(hub_path, f"\n## Related Chat\n- [[{orphan}]] \u2014 {desc}")
                search_linked.append({"orphan": orphan, "hub": hub})
            else:
                unresolved.append(orphan)
        else:
            unresolved.append(orphan)

# Connect research orphans to build log
new6, skip6 = idempotent_connect(research_orphans, "VaultBot-Build-Log.md", vault_root, "Additional Research Notes")

total_new = len(new1) + len(new2) + len(new3) + len(new4) + len(new5) + len(new6) + len(search_linked)
total_skip = len(skip1) + len(skip2) + len(skip3) + len(skip4) + len(skip5) + len(skip6)

result = json.dumps({
    "new_links": total_new,
    "skipped": total_skip,
    "search_linked": search_linked,
    "uncategorized_remaining": len(uncategorized) - len(search_linked),
    "unresolved": unresolved,
})
```

Rules: Never append to LOCKED notes or sacred journal files. System files stay isolated. If an orphan doesn't fit any category, link it to the closest semantic match found via `vault_search`.

### Step 3: Consolidate — Extract Patterns and Synthesize Semantic Notes

Gather all deterministic signals from the four quality modules, show the LLM everything it needs (extracted patterns, gaps, exemplar format, existing notes for dedup), and let it synthesize semantic notes.

3. ```python
import json, os, re
from pathlib import Path
from datetime import date

vault_path = os.environ.get("VAULT_PATH", ".")
backend_dir = os.environ.get("PYTHONPATH", ".").split(os.pathsep)[0]
today = date.today().isoformat()

# --- Import the four deterministic quality modules ---
from pattern_extractor import PatternExtractor
from calibration import CalibrationTracker
from rag_eval import RAGEvaluator
from claim_verifier import ClaimVerifier

pe = PatternExtractor(vault_path=vault_path)
ct = CalibrationTracker(log_path=os.path.join(backend_dir, "calibration_log.json"))
re_eval = RAGEvaluator(log_path=os.path.join(backend_dir, "rag_eval_log.json"))
cv = ClaimVerifier(log_path=os.path.join(backend_dir, "claim_verification_log.json"))

# --- Gather all data the LLM needs ---
patterns = pe.extract_all()
consolidation_gaps = pe.get_consolidation_gaps()
calibration_gaps = ct.get_calibration_gaps()
rag_gaps = re_eval.get_retrieval_gaps()
verification_gaps = cv.get_verification_gaps()

# --- Find existing semantic notes (for dedup) ---
existing_semantic = []
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if f.endswith(".md"):
            try:
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    head = fh.read(500)
                if "type: semantic" in head:
                    existing_semantic.append(os.path.splitext(f)[0])
            except:
                pass

# --- Read the exemplar semantic note (shows the LLM the exact format) ---
exemplar_content = ""
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if f == "Cross-Session-Patterns-from-75-Chat-Logs.md":
            try:
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    exemplar_content = fh.read()
            except:
                pass

# --- Check if there's anything to consolidate ---
total_gaps = (len(consolidation_gaps) + len(calibration_gaps) +
              len(rag_gaps) + len(verification_gaps))
if total_gaps == 0:
    result = json.dumps({
        "status": "skipped",
        "reason": "No consolidation gaps found from any quality module",
        "patterns_found": len(patterns.get("recurring_topics", [])),
    })
else:
    # --- Build the comprehensive prompt ---
    # Show the LLM EVERYTHING: patterns, gaps, exemplar, existing notes
    prompt_parts = [
        "You are a memory consolidation system for an AI agent called VaultBot.",
        "Your job is to synthesize cross-session patterns into semantic knowledge notes.",
        "Each semantic note captures a recurring pattern from the agent's experiences",
        "and turns it into a reusable rule that future sessions can retrieve.",
        "",
        "## Existing Semantic Notes (do NOT duplicate these)",
    ]
    for title in existing_semantic:
        prompt_parts.append(f"- {title}")
    prompt_parts.extend([
        "",
        "## Exemplar Semantic Note (follow this format exactly)",
        "This is a real semantic note that was previously written. Match its structure:",
        exemplar_content,
        "",
        "## Schema Guidance (from Semantic-Consolidation-Architecture)",
        "Key fields for semantic note frontmatter:",
        "- type: semantic",
        "- status: tentative (until 3+ evidence sources, then verified)",
        "- created: " + today,
        "- evidence_count: number of chat logs that support this pattern",
        "- evidence_sources: wikilinks to the chat logs ([[Chat-...]])",
        "- scope: list of areas this pattern applies to",
        "- falsifiable_if: condition that would prove this pattern wrong",
        "- tags: list of relevant tags",
        "",
        "## Extracted Patterns (deterministic, from pattern_extractor.py)",
        f"Total sessions scanned: {patterns['total_sessions']}",
        f"Total exchanges: {patterns['total_exchanges']}",
        f"Recurring topics found: {len(patterns['recurring_topics'])}",
        f"Sentiment: {json.dumps(patterns['sentiment'], indent=2)}",
        f"Over-reporting: {patterns['over_reporting']['count']} exchanges over {patterns['over_reporting']['threshold_chars']} chars",
        f"Tool frequency: {json.dumps(patterns['tool_patterns'].get('tool_frequency', {}), indent=2, default=str)[:1500]}",
        f"Top workflows: {json.dumps(patterns['tool_patterns'].get('top_workflows', []), indent=2, default=str)[:1500]}",
        "",
        "## Consolidation Gaps (patterns ready for semantic notes)",
        "These are patterns that the deterministic extractor flagged as ready:",
        json.dumps(consolidation_gaps, indent=2, default=str),
        "",
        "## Calibration Gaps (the operator's corrections — where VaultBot failed)",
        "These are places where the operator corrected VaultBot. Each is a failure pattern.",
        json.dumps(calibration_gaps, indent=2, default=str) if calibration_gaps else "[]",
        "",
        "## RAG Evaluation Gaps (retrieval quality issues)",
        "These are queries where FUSED retrieval performed poorly.",
        json.dumps(rag_gaps, indent=2, default=str) if rag_gaps else "[]",
        "",
        "## Claim Verification Gaps (notes with unsupported claims)",
        "These are notes where claims were not supported by cited sources.",
        json.dumps(verification_gaps, indent=2, default=str) if verification_gaps else "[]",
        "",
        "## Instructions",
        "For each pattern that is NOT already covered by an existing semantic note:",
        "1. Write a semantic note with YAML frontmatter (see exemplar format above)",
        "2. Include a 'How This Note Was Generated' section explaining the deterministic extraction",
        "3. For each pattern, include:",
        "   - The pattern description (what keeps happening across sessions)",
        "   - Evidence: specific chat log references with [[wikilinks]]",
        "   - A 'Semantic rule' section: the actionable lesson",
        "   - Links to related notes using [[wikilinks]]",
        "4. Only consolidate patterns that have 3+ evidence sources",
        "5. Use title-case-with-hyphens for the note title (e.g., Over-Reporting-Brevity-Rule)",
        "6. CRITICAL: In the Related section, write a full sentence explaining each wikilink",
        "   relationship. Never dump bare links. Format: - [[Note-Name]] -- explains why related",
        "7. Do NOT include .md extensions in wikilinks. Use [[Note-Name]] not [[Note-Name.md]]",
        "8. Only link to notes that actually exist in the vault. If unsure, omit the link.",
        "",
        "Output each note as:",
        "### NOTE: [Note-Title]",
        "[full note content including YAML frontmatter and body]",
        "",
        "If all patterns are already covered by existing semantic notes, output: NO_NEW_NOTES",
    ])

    prompt = "\n".join(prompt_parts)

    # --- Check if LLM service is available before calling ---
    llm_output = None
    _llm_available = False
    try:
        from llm_client import get_llm_client as _get_client
        _client = _get_client()
        _llm_available = _client.is_running()
    except:
        _llm_available = False

    if not _llm_available:
        result = json.dumps({
            "status": "llm_unavailable",
            "total_gaps": total_gaps,
            "consolidation_gaps": len(consolidation_gaps),
            "calibration_gaps": len(calibration_gaps),
            "rag_gaps": len(rag_gaps),
            "verification_gaps": len(verification_gaps),
            "note": "Pattern extraction succeeded. LLM service not running. Data ready for when it is."
        })
    else:
        # --- Call the LLM with the full context ---
        system_prompt = (
            "You are a memory consolidation system. Synthesize the provided "
            "patterns into semantic knowledge notes. Follow the exemplar format "
            "exactly. Be specific with evidence. Link to related notes. "
            "Output only the notes."
        )
        llm_output = None
        try:
            llm_output = llm_generate(prompt, system=system_prompt)
        except Exception as e:
            result = json.dumps({
                "status": "llm_error",
                "error": str(e)[:200],
                "total_gaps": total_gaps,
                "note": "Pattern extraction succeeded but LLM synthesis failed. Data is ready for retry."
            })

    # --- Parse the LLM output and write notes ---
    if llm_output is None:
        pass  # LLM unavailable or failed, result already set above
    elif not llm_output.strip():
        result = json.dumps({
            "status": "llm_empty",
            "total_gaps": total_gaps,
            "note": "LLM returned empty output. Data ready for retry."
        })
    elif "NO_NEW_NOTES" in llm_output:
        result = json.dumps({
            "status": "no_new_notes",
            "reason": "LLM determined all patterns are already covered",
            "total_gaps": total_gaps,
        })
    else:
        notes_written = []
        notes_skipped = []
        lint_issues = []

        note_blocks = re.split(r'### NOTE:\s*', llm_output)
        for block in note_blocks[1:]:
            lines = block.strip().split('\n')
            title = lines[0].strip()
            note_content = '\n'.join(lines[1:]).strip()

            if title in existing_semantic:
                notes_skipped.append(title)
                continue

            # Post-process: strip .md extensions from wikilinks
            note_content = re.sub(r'\[\[([^\]|\]]+?)\.md(\|[^\]]+)?\]\]',
                                  lambda m: f'[[{m.group(1)}{m.group(2) or ""}]]',
                                  note_content)

            # Post-process: remove wikilinks to non-existent notes
            all_vault_stems = set()
            for root, dirs, files in os.walk(vault_path):
                dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
                for f in files:
                    if f.endswith(".md"):
                        all_vault_stems.add(os.path.splitext(f)[0])
            
            def _clean_wikilinks(text):
                wl_pat = re.compile(r'\[\[([^\]|\]]+)(?:\|([^\]]+))?\]\]')
                def _replacer(m):
                    target = m.group(1).strip()
                    if target in all_vault_stems:
                        return m.group(0)
                    chat_check = os.path.join(vault_path, "vaultbot", "chat", target + ".md")
                    if os.path.exists(chat_check):
                        return m.group(0)
                    return ""
                cleaned = wl_pat.sub(_replacer, text)
                cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
                return cleaned
            
            note_content = _clean_wikilinks(note_content)

            note_path = os.path.join(vault_path, f"{title}.md")
            with open(note_path, 'w', encoding='utf-8') as f:
                f.write(note_content)
            notes_written.append(title)

            lint_result = vault_lint(note_path)
            if lint_result.get("broken_wikilinks"):
                lint_issues.append({
                    "note": title,
                    "broken": lint_result["broken_wikilinks"],
                })

        try:
            pe.log_consolidation({
                "notes_written": notes_written,
                "notes_skipped": notes_skipped,
                "gaps_processed": total_gaps,
                "lint_issues": lint_issues,
            })
        except:
            pass

        result = json.dumps({
            "status": "consolidated",
            "notes_written": notes_written,
            "notes_skipped": notes_skipped,
            "lint_issues": lint_issues,
            "total_gaps_processed": total_gaps,
        }, indent=2)
```

What the LLM sees: the full exemplar semantic note, all extracted patterns (recurring topics, sentiment, tool usage, over-reporting), consolidation gaps from all four quality modules (pattern extractor, calibration tracker, RAG evaluator, claim verifier), existing semantic note titles for dedup, and the schema guidance. The LLM only synthesizes — all pattern detection is deterministic.

### Step 4: Prune — Remove Junk and Stale Content

Scan for and remove pytest cache files, duplicate/backup files, corrupted filenames, and trash remnants.

4. ```python
# Extract isolated nodes from Step 1's output
vault_path = os.environ.get("VAULT_PATH", ".")
_step1_data = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
isolated = _step1_data.get("isolated_nodes", [])

junk_patterns = [".pytest_cache", "baseline/", ".bak", ".orig", ".tmp",
               "_restart.bat", "_restart.sh", "trash/"]
junk_files = [f for f in isolated if any(p in f for p in junk_patterns)]
# Also flag empty .md files (0 bytes) in isolated nodes
for f in isolated:
    full_path = os.path.join(vault_path, f) if not os.path.isabs(f) else f
    if f.endswith(".md") and os.path.exists(full_path):
        if os.path.getsize(full_path) == 0:
            junk_files.append(f)
# Also scan all vault files for junk
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if f.endswith(".md") and any(p in f for p in junk_patterns):
            junk_files.append(os.path.join(root, f))

deleted = []
for f in junk_files:
    try:
        vault_delete(f)
        deleted.append(f)
    except:
        pass

result = json.dumps({"junk_deleted": deleted})
```

Rules: Always back up before deleting (vault_delete does this automatically). Never delete sacred journals, LOCKED notes, or identity files. When in doubt, don't delete — flag for the operator.

### Step 5: Validate — Verify the Graph is Healthier

Re-run `vault_graph_analyzer` and compare to the pre-dream state.

5. ```python
# Extract baseline metrics from Step 1's output
vault_path = os.environ.get("VAULT_PATH", ".")
_step1_data = json.loads(prior_results[1]) if len(prior_results) > 1 else {}
total_islands = _step1_data.get("total_islands", 0)
connectivity = _step1_data.get("connectivity", 0)
isolated = _step1_data.get("isolated_nodes", [])

result_after = vault_graph_analyzer()
data_after = result_after["analysis"]

islands_before = total_islands
islands_after = data_after["num_islands"]
connectivity_before = connectivity
connectivity_after = data_after["connectivity_ratio"]


result = json.dumps({
    "islands_before": islands_before,
    "islands_after": islands_after,
    "connectivity_before": connectivity_before,
    "connectivity_after": connectivity_after,
    "orphans_resolved": len(isolated) - len(data_after['isolated_nodes']),
})
```

[validate: islands_after <= islands_before]
[validate: connectivity_after >= connectivity_before]

### Step 6: Evaluate — Score the Procedure Library

Per the operator's directive, the framework self-scores its procedures each dream cycle. Call [[Procedure-Eval]] to classify every procedure as healthy/degraded/broken and surface which need review, cartridge demotion, or retirement. Deterministic counters — the small model only formats.

6. ```python
import json

eval_result = run_procedure("Procedure-Eval")
summary = {}
try:
    summary = json.loads(eval_result.get("final_output", "{}"))
except Exception:
    summary = {"raw": eval_result.get("final_output", "")[:1000]}

result = json.dumps({
    "step": "procedure_eval",
    "overall_passed": eval_result.get("overall_passed"),
    "eval_summary": summary.get("summary", summary),
    "problem_procedures": summary.get("problem_procedures", []),
})
```

## Requirements

- **Hub notes** must exist: Testing-and-Verification-History, VaultBot-Build-Log, the operator-Design-Decisions, Cross-Session-Patterns-from-75-Chat-Logs, the operator-Communication-Preferences
- **Quality modules** must be importable: pattern_extractor, calibration, rag_eval, claim_verifier (Step 3 only)
- **LLM service** must be running for Step 3 (consolidation). Steps 0-2, 4-5 are fully deterministic and work without an LLM.
- **Allowed tools**: vault_graph_analyzer, vault_list, vault_append, vault_delete, vault_lint, vault_search, code_read, llm_generate, run_procedure

## Output Format

Each step outputs JSON. The final output is all steps concatenated:

| Step | Output | Key Fields |
|---|---|---|
| 0: Journal | `{"journal_entries": [...], "total_chars": N}` | Files found, total content size |
| 1: Scan | `{"total_islands": N, "isolated_nodes": [...], "connectivity": 0.X, "chat_orphans": [...], "research_orphans": [...], "junk_files": [...]}` | Graph state before dreaming |
| 2: Connect | `{"new_links": N, "skipped": N, "search_linked": [...], "uncategorized_remaining": N, "unresolved": [...]}` | Links created, orphans that couldn't be connected |
| 3: Consolidate | `{"status": "consolidated"|"no_new_notes"|"llm_unavailable", "notes_written": [...], "notes_skipped": [...], "lint_issues": [...]}` | Semantic notes created |
| 4: Prune | `{"junk_deleted": [...]}` | Files removed |
| 5: Validate | `{"islands_before": N, "islands_after": N, "connectivity_before": 0.X, "connectivity_after": 0.X, "orphans_resolved": N}` | Graph health delta |
| 6: Procedure-Eval | `{"step": "procedure_eval", "eval_summary": {...}, "problem_procedures": [...]}` | Procedure health, degraded/broken list |

## Dreaming Frequency

- **Light dream** (Steps 1-2, 4-5 only): Every 10-15 new chat logs or when the operator asks. Just connects orphans and prunes junk. ~5 minutes.
- **Full dream** (all steps): Every 20-30 new chat logs or monthly. Also runs consolidation. ~15-20 minutes.

## What NOT to Do During Dreaming

- Don't delete chat logs — they're episodic memory, permanent record
- Don't modify LOCKED notes or sacred journals
- Don't create new pattern highways unless there are 10+ orphans in a new category
- Don't use LLM for pattern detection — use deterministic code (counters, graph analysis)
- Don't rush — verify each connection is semantically appropriate before linking

## Related

- [[Semantic-Consolidation-Architecture]] — the full 6-phase consolidation pipeline
- How to Organize a Knowledge Base — note organization procedure
- [[Cross-Session-Patterns-from-75-Chat-Logs]] — first output of consolidation
- [[Orphan-Note-Patterns-and-Lessons]] — patterns from orphan notes
- [[VaultBot-Build-Log]] — build progression hub
- [[Testing-and-Verification-History]] — testing hub
- [[the operator-Design-Decisions]] — design decisions hub
- [[Procedural-Bootstrap-and-Evolution-Plan]] — the framework this procedure lives in
- [[open-second-brain-Hermes-Agent-dream-pass-mechanism-how-does-the-dream-pass-work]] — research source
- [[Memory-consolidation-in-AI-agents-how-to-convert-episodic-memories-conversation-]] — research source
