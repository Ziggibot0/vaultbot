---
tags:
  - architecture
  - design-principle
type: architecture-note
status: raw
baseline: true
created: 2026-08-03
summary: Sovereignty means visible user work and hidden AI infrastructure, prioritizing structure over specific file types for folder organization. |workspace sovereignty|vault architecture|content visibility|
---

# Workspace Sovereignty

## Claim

The vault is the user's second brain, not the AI's playground. The user's
content must be immediately visible and never buried under AI-generated
material. The AI's infrastructure must be tucked away where the user rarely
looks. This principle governs all vault folder organization decisions.

## Reasoning

A second brain only works if the user can trust their eyes. When the user
opens the vault, they should see *their* work — their notes, their research,
their journal — without sifting through AI-generated research notes, system
architecture docs, or chat logs. If the user has to scroll past 200 AI
research notes to find their own roadmap, the system has failed its core
purpose.

This principle has three axes:

### 1. Function over content for folder names

Parent folders describe *what you do* with the contents, not *what the
contents are about*. "Bridges" is a content category — it tells you the
files are about bridges between topics, but not what you *do* with them.
"Simulations" is a function category — it tells you these are runnable
code simulations. Function-based naming scales better because new content
slots into existing functional categories instead of requiring new
content-based ones.

**Before:** `Knowledge/Bridges/Simulating-Homeostasis-in-Python.md`
**After:** `Knowledge/Simulations/Simulating-Homeostasis-in-Python.md`

### 2. User sovereignty — the User/ folder is sacred

The `User/` folder contains only human-created content. No AI-generated
notes, no system files, no chat logs. When the user opens this folder,
they see their own work and nothing else. This is the ergonomic core of
the principle: the user's reach to their own work is zero friction.

### 3. Distance proportional to access frequency

The less often the user needs to look at a file, the farther it should be
from the user's reach. The vault root is the most visible location — only
repo-level files (README, LICENSE, CONTRIBUTING) belong there. AI system
files live in `System/`. Code lives in `vaultbot_backend/`. Log files
live inside the backend directory, not at the vault root. The user never
needs to see any of these during normal use.

## Connections

- [[VaultBot Issues]] — the issues that motivated this principle
- [[Autonomy-Directive]] — the AI operates without permission, but within
  the user's sovereign workspace
- [[vaultbot_stuff/Vault-Knowledge-Only-Directive]] — the vault is the only knowledge
  source; this principle ensures the vault remains *usable* as it grows

## Implementation

The current vault structure embodies this principle:

```
Vault Root/
  User/           <- human content only, most visible
  Knowledge/      <- AI-researched knowledge, organized by function
    Simulations/  <- runnable code (was "Bridges")
    Biology/
    Research/
    Textbooks/
  Memory/         <- AI memory (chat logs, build logs)
  System/         <- AI system files (architecture, procedures, identity)
  learningMaterial/ <- source PDFs
  vaultbot_backend/ <- code, logs, config — tucked away
```

The root level is clean: only repo-level files. The user's content is in
`User/`, immediately visible. The AI's infrastructure is in `System/` and
`vaultbot_backend/`, out of the user's way.
