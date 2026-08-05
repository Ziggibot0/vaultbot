---
type: procedure
status: active
model_cartridge: small
created: 2026-08-03
description: Extract recurring patterns from recent chat logs and classify them into pattern highways. Scans chat logs for recurring topics, tool-usage patterns, design decisions, and operator directives. The small model classifies each pattern and routes it to the appropriate highway note (Build-Log, Design-Decisions, Testing-History) or flags it as a new pattern. Replaces the big model's inline pattern-spotting during dream passes.
when_to_use: during a dream pass, when asked to consolidate chat history, when chat logs are accumulating without being linked into pattern highways, or when asked 'what patterns are emerging from recent chats'
falsifiable_if: it classifies a chat into the wrong highway, or misses a pattern that appears in 3+ chats, or links a chat to a highway it doesn't belong to
applies_to:
  - chat-consolidation
  - pattern-extraction
  - memory-consolidation
  - dream-pass
allowed_tools:
  - run_procedure
  - vault_list
  - vault_search
  - code_read
summary: Chat-Consolidation
tags:
  - procedure
  - procedures
---

# Chat-Consolidation

## When to Run This

Run during a dream pass or when chat logs are accumulating without being
linked into the pattern highway system. The three existing highways are:

- [[VaultBot-Build-Log]] — chats where Sean directed building/fixing
- [[Sean-Design-Decisions]] — chats where Sean set philosophical direction
- [[Testing-and-Verification-History]] — chats where Sean tested/challenged

This procedure finds unlinked chat logs, classifies them, and suggests
which highway each belongs to. The small model handles classification
because it's a bounded sorting task with a fixed set of categories.

## Steps

### Step 1: Find chat logs that aren't linked to any highway

1. ```python
import os
import re

vault = str(vault_path)
chat_dir = os.path.join(vault, "vaultbot_stuff", "Memory", "Chat")
highway_dir = os.path.join(vault, "vaultbot_stuff", "Memory", "Build-Log")

# Get all chat log filenames
chat_files = []
if os.path.isdir(chat_dir):
    for f in os.listdir(chat_dir):
        if f.endswith(".md"):
            chat_files.append(f)

# Get all wikilinks from highway notes
highway_links = set()
if os.path.isdir(highway_dir):
    for f in os.listdir(highway_dir):
        if f.endswith(".md"):
            path = os.path.join(highway_dir, f)
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            for m in re.finditer(r'\[\[([^\]]+)\]\]', content):
                highway_links.add(m.group(1).replace(" ", "-"))

# Find unlinked chats
unlinked = []
for cf in chat_files:
    stem = cf.replace(".md", "").replace("Chat-", "")
    if stem not in highway_links:
        unlinked.append(cf)

result = {
    "total_chats": len(chat_files),
    "linked_chats": len(chat_files) - len(unlinked),
    "unlinked_chats": len(unlinked),
    "unlinked_files": unlinked[:50]  # cap for context
}
print(json.dumps(result, indent=2))
```

### Step 2: Read each unlinked chat's first 500 chars and classify

2. [llm: You are a pattern classifier. For each unlinked chat log, read its opening lines and classify it into exactly one of these categories:
   - "build-log" — Sean directed VaultBot to build, fix, or implement something
   - "design-decisions" — Sean set philosophical direction, made architecture choices, or stated principles
   - "testing-history" — Sean tested, challenged, or verified VaultBot's behavior
   - "research" — Sean asked for research on a topic (not a highway category)
   - "casual" — greeting, small talk, or one-off question with no lasting pattern
   - "new-pattern" — recurring topic that doesn't fit existing highways (describe what highway it suggests)

   Output a JSON array where each element has:
   - "file": the chat filename
   - "category": one of the above
   - "reason": one sentence why
   - "suggested_link": the highway note it should link to (if applicable)

   Be conservative — if unsure, mark "casual".]

### Step 3: Format the consolidation report

3. [llm: Format the classification results as a concise report:
   - Summary: X chats classified, Y linked to highways, Z casual, W new patterns
   - Table of non-casual chats with their category and suggested highway
   - List of any new-pattern suggestions with a one-sentence description of what highway note to create
   - Action items: which highway notes need updating with new wikilinks

   Keep it under 500 words. This is a routing report, not a synthesis.]


## Conditional Branches (Post-Classification Routing)

> **Research backing:** The [[Execution-Loop-Dominance-Pattern]] shows that
> routing decisions after classification produce better outcomes than
> monolithic processing. [[Information-feedback-loops-for-iterative-self-improvement]]
> demonstrates that feeding classification results into specialized
> processors creates compounding quality gains. This is the
> [[Procedure-Composition-Patterns]] approach: classify once, dispatch
> conditionally.

After Step 3 produces the classification report, the caller should
dispatch each non-casual chat to a specialized procedure based on its
category. These are **conditional if-branches**, not sequential steps.

### IF category == "build-log"

→ Run `run_procedure("Code-Pattern-Extract", note_path=<chat_file>)` to
extract any code patterns, tool usage, or implementation decisions from
the chat. Then suggest linking the chat to [[VaultBot-Build-Log]].

**Rationale:** Build-log chats contain reusable code patterns that
[[Code-Pattern-Extract]] can surface and link into the pattern highway
system. Without this branch, code patterns stay buried in chat logs.

### IF category == "design-decisions"

→ Run `run_procedure("Extract-Entities", note_path=<chat_file>)` to
extract key concepts and principles Sean articulated. Then suggest
linking the chat to [[Sean-Design-Decisions]].

**Rationale:** Design-decision chats contain architectural principles
that should become standalone concept notes. [[Extract-Entities]]
identifies the concepts; the caller writes or updates the highway note.

### IF category == "testing-history"

→ Run `run_procedure("Cross-Check-Claims", note_path=<chat_file>)` to
verify any claims made during testing against vault knowledge. Then
suggest linking the chat to [[Testing-and-Verification-History]].

**Rationale:** Testing chats often contain assertions about VaultBot's
capabilities. [[Cross-Check-Claims]] verifies these against existing
vault notes, catching any claims that are outdated or contradicted by
later findings. Backed by [[Claim-Verification-for-Vault-Notes]].

### IF category == "research"

→ Run `run_procedure("Structure-Research-Note", note_path=<chat_file>)`
to ensure any research findings from the chat are properly structured as
permanent notes with sources, wikilinks, and frontmatter.

**Rationale:** Research chats may contain findings that were discussed
but never written as permanent notes. [[Structure-Research-Note]]
ensures they meet vault quality standards. Backed by Zettelkasten
method research (see its `sources` frontmatter).

### IF category == "new-pattern"

→ Create a new highway note for the pattern (using
`run_procedure("How-to-Create-a-Procedure")` if the pattern suggests a
recurring workflow, or `vault_safe_write` for a concept highway). Then
link the chat to the new highway.

**Rationale:** New patterns indicate the highway system needs expansion.
If the pattern is a workflow, it should become a procedure. If it's a
knowledge pattern, it should become a concept highway note. This branch
ensures the system grows to accommodate new categories rather than
forcing everything into existing buckets.

### IF category == "casual"

→ No action needed. The chat doesn't contain reusable patterns. Skip it.

**Rationale:** Not every chat contains knowledge worth preserving. This
branch prevents noise from polluting the highway system.
