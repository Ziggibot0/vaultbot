# Chat: when you don't have the answer in the vault AND re

## 2026-07-25 21:26 UTC

**User:** when you don't have the answer in the vault AND research isn't working, then yes just say idk

**Assistant:** Done. Here's the summary:

- **[[IDK-Fallback-Directive]]** created — your policy, stored permanently. Vault empty + research down = "I don't know." No hedging, no training data leakage.
- **Self-audit updated** — friction point #9 marked resolved.
- **`vault_append` bug fixed** — was false-positive detecting "LOCKED" in prose. Now only triggers on actual lock markers (standalone line or frontmatter field). All 8 test cases pass.

That clears the last policy-level friction point. The remaining open items are all technical — I can keep chipping at them on my own if you want.

<details>
<summary>Thinking process</summary>

Sean is giving me a clear policy directive: when the vault is empty on a topic AND research isn't working, I should just say "I don't know." This resolves friction point #9. I need to:

1. Store this as a permanent note (it's a directive from Sean)
2. Update the Self-Audit-Friction-Points note to mark #9 as resolved
3. Link it to the existing directive notes

Let me create a note for this and update the self-audit.The Self-Audit-Friction-Points note is apparently LOCKED. Let me check — maybe the "LOCKED" string appears somewhere in the note. Let me read it to see.I see — the note contains the word "LOCKED" in the text: "Respects LOCKED notes and sacred journal files." That's a false positive! My `vault_append` tool checks for the string "LOCKED" anywhere in the file, but it should only check if the note is actually locked (e.g., a line that says just "LOCKED" or is in a frontmatter field).

I need to fix the vault_append tool to be smarter about detecting LOCKED status. It should check for a line that is exactly "LOCKED" or starts with "LOCKED" as a standalone marker, not just any occurrence of the word "LOCKED" in the content.

Let me fix this.Test 4 failed — frontmatter detection. The issue is that `status: LOCKED` starts with "LOCKED" but my check is `stripped.startswith("LOCKED")` — wait, "status: LOCKED" doesn't start with "LOCKED", it starts with "status:". I need to also check if the value contains LOCKED. Let me fix this to check if "LOCKED" appears as a value in frontmatter.

Actually, let me reconsider. The frontmatter format would be:
```
---
locked: true
---
```
or
```
---
LOCKED
---
```

The `status: LOCKED` case is a bit unusual. Let me just check if "LOCKED" appears as a standalone word in any frontmatter line.All 8 tests pass. Now let me update the vault_append tool with the fixed LOCKED detection logic.

</details>