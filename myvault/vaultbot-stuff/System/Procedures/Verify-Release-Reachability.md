---
type: procedure
status: stable
baseline: true
created: 2026-08-29
description: Verify a VaultBot release is actually REACHABLE by installed clients before announcing it. The update path is a three-link chain (resolveLatestTag -> manifest version compare -> tag tarball with myvault/ layout), and each link has broken silently in the past. This procedure exercises every link against the LIVE GitHub endpoints with the same fetches the plugin makes, so a broken release is caught before users are told to update.
when_to_use: after publishing a GitHub release for VaultBot, before announcing it; or when a user reports "Check for updates says nothing available" / "Update failed: Archive has no plugin folder"
falsifiable_if: any check below returns a version that mismatches the release being cut, a non-200 tarball fetch, or a manifest at the tag equal to the previous release's manifest version
applies_to:
  - releases
  - self-update
  - versioning
depends_on:
  - "[[Diagnose-Self-Update-Incomplete]]"
  - "[[Plugin-Reload]]"
  - "[[Backend-Restart]]"
allowed_tools:
  - code_run
  - web_read_source
summary: Verify-Release-Reachability
tags:
  - procedure
  - procedures
  - release
  - updater
---

# Verify-Release-Reachability

## When to Run This

Run AFTER publishing a GitHub release and BEFORE telling users to update.
Also run when debugging reports of "no update available" or updater
failures.

## Why This Exists

The v1.5.2 release (2026-08-29) almost shipped unreachable. Three links
in the update chain had each broken SILENTLY in the weeks prior:

1. `resolveLatestTag()` in the plugin fetches `/releases/latest`. That
   endpoint kept serving the previous release for minutes after
   `make_latest=true` — verify, don't assume.
2. `checkLatestVersion()` compares the manifest version STRING at the
   tag against the local one. v0.4.0 and v1.5.1 both shipped with the
   manifest still at 1.5.1, so no install was EVER offered those
   releases. The manifest bump is not optional.
3. `performSelfUpdate()` extracts the plugin from
   `<archive>/myvault/.obsidian/plugins/vaultbot` — after the
   `vault/ -> myvault/` rename, the updater still looked at `vault/`
   and threw AFTER applying the backend (half-applied update). The
   layout must be verified against the actual tag tarball, not against
   the local checkout.

## Steps

Each step is a live fetch. Use the same URLs the plugin uses.

1. Resolve latest: `GET https://api.github.com/repos/Ziggibot0/vaultbot/releases/latest`
   — `tag_name` must equal the release just published. If stale, re-PATCH
   the release with `make_latest=true` and re-check after ~20s.
2. Manifest at tag: `GET https://raw.githubusercontent.com/Ziggibot0/vaultbot/<tag>/myvault/.obsidian/plugins/vaultbot/manifest.json`
   — `version` must differ from the PREVIOUS release's tag manifest
   version (not from local main), or no install will see the update.
3. Tarball fetch: `GET https://github.com/Ziggibot0/vaultbot/archive/refs/tags/<tag>.tar.gz`
   — must return 200 with a plausible size. List it and confirm the
   archive contains BOTH `vaultbot_backend/` and
   `myvault/.obsidian/plugins/vaultbot/` (the updater requires both).
4. Half-apply audit: if installs in the wild run a plugin OLDER than the
   updater fix in this release, their first update half-applies (backend
   yes, plugin no). State the manual plugin-refresh step in the release
   notes; do not claim "just update" if it isn't true.
5. CHANGELOG: the `[Unreleased]` section must have been folded into a
   dated section for this version, and `manifest.json` on main must match
   the released version so the NEXT checkLatestVersion compare works.

## Verification

- All fetches above return the expected tag/version/layout.
- `gh api graphql -f query='{ repository(owner:"Ziggibot0", name:"vaultbot") { latestRelease { tagName } } }'`
  agrees with the REST endpoint.

## Failure Modes

- Latest marker lagging: re-PATCH with `make_latest=true`; the GraphQL
  endpoint reflects it sooner than REST.
- Manifest forgotten: bump it in a release-prep PR — the string compare
  is the ONLY update signal installs have.
- Pre-rename pinned tags: the updater falls back to `vault/`; do not
  "simplify" the fallback away.