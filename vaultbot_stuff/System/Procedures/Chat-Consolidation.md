---
type: procedure
status: active
model_cartridge: small
created: 2026-08-03
description: "Extract recurring patterns from recent chat logs and classify them into pattern highways. Scans chat logs for recurring topics, tool-usage patterns, design decisions, and operator directives. The small model classifies each pattern and routes it to the appropriate highway note (Build-Log, Design-Decisions, Testing-History) or flags it as a new pattern. Replaces the big model's inline pattern-spotting during dream passes."
when_to_use: "during a dream pass, when asked to consolidate chat history, when chat logs are accumulating without being linked into pattern highways, or when asked 'what patterns are emerging from recent chats'"
falsifiable_if: "it classifies a chat into the wrong highway, or misses a pattern that appears in 3+ chats, or links a chat to a highway it doesn't belong to"
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