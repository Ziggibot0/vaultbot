# Chat: so there are NO stale docs rn?

## 2026-07-26 02:03 UTC

**User:** so there are NO stale docs rn?

**Assistant:** Here's the full picture:

**Stale/junk in the vault:**

| Category | Files | Count |
|---|---|---|
| **Junk research** (researcher chased nonsense dangling links) | Target, Some-Note, fake-link, Other-Procedure, Six-Degrees-Of-Separation, Self-Audit-Friction-Points, wikilinks | 7 |
| **Test artifacts** (dev testing leftovers) | Note-A, Note-B, Note-C, Related-Note, Other-post, pathtofilehtml, learningMaterialwebchat-marginalia, learningMaterialwebwikilinks | 8 |
| **Unfilled template** | `Communication-Preferences.md` (template with `[brackets]`) — superseded by `Sean-Communication-Preferences.md` | 1 |
| **Maybe stale?** | Battery-Drain, Battery-Drain-in-PT2399-Pedals, Benchmark-Data, Caching-Approach, Low-Power-Assumption, Low-Power-Pedal-Assumption, Steady-State-Assumption, Matching-free-text-failure-descriptions | 8 |

That battery/pedal cluster — is that from an earlier project of yours or is it test junk too? I don't want to nuke something real.

The rest (15 actual research notes on deterministic scaffolding, small models, typed wikilinks, etc.) are legit and current.

**TL;DR:** 15-16 junk notes to clean up, pending your call on the battery stuff. Want me to safe-delete the junk and keep the real research?

<details>
<summary>Thinking process</summary>

Sean is asking if there are any stale docs in the vault right now. Let me think about what I know:

1. I just updated the three identity files (IDENTITY.md, SELF_MODEL.md, GOALS.md) - those are fresh now.
2. But there are also baseline copies: `baseline/identity/GOALS.md`, `baseline/identity/IDENTITY.md`, `baseline/identity/SELF_MODEL.md` - those might still be stale.
3. There are also the vault-level copies of some identity files that appear in the vault list.

Let me also think about what else could be stale:
- The `Communication-Preferences.md` in the vault root still looks like a template (unfilled brackets)
- The `baseline/` directory has copies of directives that might be outdated
- Some research notes are junk/test artifacts (Target, Some-Note, fake-link, Other-Procedure, Six-Degrees-Of-Separation, Self-Audit-Friction-Points) - these are autonomous researcher artifacts that researched dangling wikilinks that weren't real topics
- `vaultbot/research/` has test artifacts like Note-A, Note-B, Note-C, Related-Note, Other-post, pathtofilehtml, learningMaterialwebchat-marginalia, learningMaterialwebwikilinks

Let me check the baseline identity files and the Communication-Preferences template, and also look at what junk research notes exist.So the `baseline/` directory has stale copies of all three identity files. These are the "template" versions that get copied when a new vault is set up. They're supposed to be the clean starting templates, so maybe they SHOULD stay stale? Or maybe they should be updated too?

Actually, the `baseline/` directory is likely the template/factory defaults. If someone sets up a new vault, these are the starting files. So they SHOULD stay as "new here" templates — they're not stale, they're templates. The active identity files are in `vaultbot_backend/identity/`.

But wait — the vault context shows `Communication-Preferences.md` exists as a vault note. Let me check the vault root for it.

Also, I should check for junk research notes. The autonomous researcher created notes like "Target", "Some-Note", "fake-link", "Other-Procedure", "Six-Degrees-Of-Separation", "Self-Audit-Friction-Points" — these are all garbage from researching dangling wikilinks that weren't real topics. They're test artifacts.

Let me also check the `baseline/Communication-Preferences.md` and the vault root `Communication-Preferences.md`.

Actually, looking at the vault list, I see `Communication-Preferences.md` is not in the root but `baseline/Communication-Preferences.md` is. And there's `Sean-Communication-Preferences.md` in the root which is the real one.

Let me look at what's actually stale or junk:

1. **baseline/identity/** files — these are templates, probably fine as-is
2. **Junk research notes** — autonomous researcher artifacts researching nonsense dangling links:
   - `Target.md` — researched the word "target" from a dangling link
   - `Some-Note.md` — researched "some note" from a dangling link  
   - `fake-link.md` — researched "fake-link" from a dangling link
   - `Other-Procedure.md` — researched "other-procedure" from a dangling link
   - `Six-Degrees-Of-Separation.md` — researched from a dangling link in No-Wikipedia-Directive
   - `Self-Audit-Friction-Points.md` — researched from a dangling link
   - `wikilinks.md` — researched "wikilinks" from a dangling link
   - Various test notes: Note-A, Note-B, Note-C, Related-Note, Other-post, pathtofilehtml, learningMaterialwebchat-marginalia, learningMaterialwebwikilinks

3. **Communication-Preferences.md** in the vault root — from the vault context, this looks like the unfilled template. But `Sean-Communication-Preferences.md` is the filled-in version. So the template version is stale/unused.

Let me report what I find.OK so there are 38 research notes. Let me categorize them:

**Real research (useful):**
- AB-testing-of-prompts-and-procedures...
- Can-30B-parameter-LLM-models-reliably-follow...
- How-to-implement-failure-logging-and-context-tracking...
- Microsoft-Hermes-Qwen-small-language-model-function-calling...
- Self-improving-AI-agent-systems-that-bootstrap...
- deterministic-agent-frameworks-for-small-language-models...
- few-shot-prompting-examples-and-techniques...
- how-to-build-deterministic-scaffolding...
- how-to-evaluate-credibility-of-sources...
- how-to-evaluate-source-credibility-research-methodology...
- how-to-structure-a-research-note-for-a-knowledge-base...
- researchrag-graph-retrieval-vs-graph-reasoning.md
- researchwikilinks-and-named-edges.md
- semantic-knowledge-graph-structure-that-encodes-reasoning...
- typed-wikilinks-and-semantic-relationships...

**Junk (autonomous researcher researched nonsense dangling links):**
- Target.md — researched the word "target"
- Some-Note.md — researched "some note"
- fake-link.md — researched "fake-link"
- Other-Procedure.md — researched "other-procedure"
- Six-Degrees-Of-Separation.md — researched from a dangling link in No-Wikipedia-Directive
- Self-Audit-Friction-Points.md — researched from a dangling link
- wikilinks.md — researched "wikilinks"

**Test artifacts (from testing/development):**
- Note-A.md, Note-B.md, Note-C.md, Related-Note.md — test notes
- Other-post.md — test note (was already deleted once, might be back?)
- pathtofilehtml.md — test artifact
- learningMaterialwebchat-marginalia-nu-0b49d55ahtml.md — test artifact
- learningMaterialwebwikilinks-net-e7d87e2chtml.md — test artifact

**Possibly from earlier project (battery/pedal stuff):**
- Battery-Drain-in-PT2399-Pedals.md
- Battery-Drain.md
- Benchmark-Data.md
- Caching-Approach.md
- Low-Power-Assumption.md
- Low-Power-Pedal-Assumption.md
- Steady-State-Assumption.md
- Matching-free-text-failure-descriptions...

These battery/pedal ones might be from an earlier project Sean was working on, or they might be test artifacts too. I'm not sure. I should ask Sean about those.

Also, there's `Communication-Preferences.md` in the vault root which is the unfilled template, while `Sean-Communication-Preferences.md` is the real one. The template one is stale/unused.

Let me also check the vault root for the Communication-Preferences template.

Actually, looking at the vault context, I can see `Communication-Preferences.md` exists and has template content with unfilled brackets. And `Sean-Communication-Preferences.md` is the filled-in version. So the template is stale/unused.

Let me give Sean a clear summary of what's stale/junk.

</details>