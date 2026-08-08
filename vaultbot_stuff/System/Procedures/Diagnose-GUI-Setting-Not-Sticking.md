---
type: procedure
model_cartridge: big
tags:
  - troubleshooting
  - gui
  - persistence
  - silent-failure
covers: GUI setting (dropdown/toggle) appears to 'flip back' or not stick after the user changes it
status: raw
created: 2026-08-06
summary: Diagnose GUI Setting Not Sticking (Optimistic-Update / Silent-Failure Mask)
---

# Diagnose GUI Setting Not Sticking (Optimistic-Update / Silent-Failure Mask)

General class: **any UI control whose change fires a backend mutation but does not
confirm it landed.** Symptom: the control snaps back to the old value (or the saved
state on disk never changes) right after the user picks something new.

## Symptom → likely cause decision tree

1. Saved/authoritative state on disk NEVER changed (check the persisted file / endpoint).
   → The mutation POST never landed OR the handler assumed success and never verified.
   Look for **fire-and-forget** + **optimistic re-render**.

2. Saved state changed, but the UI re-renders the old value on a timer / event
   (refresh loop, reconnect, websocket event) with a STALE read.
   → A render loop is reading a cached / racing source of truth.

3. Saved state changed but the *behaving subsystem* (the loop that actually uses the
   value) still uses the old one.
   → The mutation wrote the config but did NOT rebuild / notify the consumer
   (in-memory singleton not updated, cache not cleared).

## Concrete steps

1. **Find the single source of truth.** Where is this setting actually read from at
   use-time? (a file, an in-memory registry, an endpoint.) Trace the read path first —
   don't assume the control's value is the truth.

2. **Find every WRITER to that truth.** Grep the backend for all mutation call sites
   (set_role / set_model / setRoleCfg / roles[...] / direct field writes). There is
   often a migration-from-env or legacy endpoint that also writes the same value and
   can overwrite a newer selection on boot.

3. **Check the change handler for the silent-failure mask.** Red flags, any of:
   - `await X(...)` result ignored → assumed success.
   - `try { ... } catch { return null; }` on the fetch → a failed POST looks like OK.
   - Handler re-renders/re-reads the authoritative truth **before** confirming the
     write landed → snaps back to the old value (reads as a "revert").
   - A timeout in the fetch path → a slow POST looks like failure.

4. **Confirm with the persisted state.** If the truth on disk matches the OLD value
   after the user's change, the POST never landed or wasn't verified — NOT a revert
   loop. Fix the mask (return ok/false + detail, check in handler, keep visible
   selection on failure) rather than inventing a reverting-loop theory.

5. **Stop theorizing early.** If you keep forming new "it could be X" hypotheses,
   run one read-only probe against the live backend (health + read the truth) to pin
   which branch of the tree you're in, then stop.

## Anti-masking fix pattern (what "good" looks like)

- The fetch helper returns `{ok, roles?, detail?}` — never `null` + silent.
- The handler checks the response. On failure: surface the real error AND keep the
  user's visible selection (do not re-render from the authoritative truth, which
  would snap back and look like a revert). On success: show confirmation + refresh.
- See the model-registry dropdowns in `.obsidian/plugins/vaultbot/main.js`
  (`setRoleCfg` + the big/small/vision change handlers) for the worked example.

## Related
- memory: `gui-setting-silent-failure-mask.md`
- model registry: `provider-model-registry-2026-08-01.md`
