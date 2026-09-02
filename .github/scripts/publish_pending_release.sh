#!/usr/bin/env bash
# Publish the pending VaultBot release for the plugin manifest version.
#
# WHY THIS EXISTS: the in-Obsidian updater only sees PUBLISHED releases.
# release-drafter creates a DRAFT on every merge to main, and publishing
# was a manual click — so every release was invisible to users ("Check for
# updates shows the old version") until someone remembered. This script
# closes that gap: it publishes (or creates) the release for the version
# in myvault/.obsidian/plugins/vaultbot/manifest.json, pinned to an
# explicit, CI-verified commit.
#
# SAFETY PROPERTIES (see PR for the full rationale):
#   1. Idempotent   — if the tag already exists, or the release is already
#                     published, it exits 0 and touches nothing.
#   2. Never invents — it only acts on the version declared in the
#                     manifest at TARGET_SHA. No version guessing.
#   3. CI-gated     — the caller guarantees TARGET_SHA passed full CI
#                     (push path) or verifies it itself (cron path).
#   4. Fail loud    — any ambiguity (multiple matching drafts, missing
#                     manifest, tag created but release unpublished) exits
#                     non-zero with a clear message; it never silently
#                     half-publishes.
#   5. Pinned       — the release tag is created at TARGET_SHA (the CI-
#                     tested commit), never at a newer HEAD that moved
#                     mid-run.
#
# Usage: publish_pending_release.sh <target-sha> <verify-ci>
#   target-sha  — the commit the release should be tagged at.
#   verify-ci   — "true" (cron path): before doing anything, require the
#                 latest CI run for <target-sha> to have succeeded.
#                 "false" (push path): the caller already gated on CI
#                 (workflow_run fires only when CI concluded successfully).
set -euo pipefail

TARGET_SHA="${1:?usage: publish_pending_release.sh <target-sha> <verify-ci>}"
VERIFY_CI="${2:-false}"

REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

echo "repo=$REPO target_sha=$TARGET_SHA verify_ci=$VERIFY_CI"

# ── Step 0 (cron path only): confirm CI passed for this exact commit ──
if [ "$VERIFY_CI" = "true" ]; then
  echo "Checking CI conclusion for $TARGET_SHA…"
  CI_STATE=$(gh run list --repo "$REPO" --commit "$TARGET_SHA" \
    --workflow CI --json status,conclusion \
    --jq '.[0] | .status + "/" + (.conclusion // "none")')
  echo "CI state: $CI_STATE"
  if [ "$CI_STATE" != "completed/success" ]; then
    # Still running or failed. Never publish on unverified code. Exit 0 so
    # the scheduled run doesn't spam failure notifications; the next hour's
    # run (or the push-time path) will publish once CI is green.
    echo "CI not green for $TARGET_SHA — not publishing (this is expected right after a merge)."
    exit 0
  fi
fi

# ── Step 1: read the version the manifest declares at TARGET_SHA ──
# Prefer python3 (GitHub runners), fall back to python (Windows dev machines).
PYTHON_BIN="$(command -v python3 || command -v python)"
if [ -z "$PYTHON_BIN" ]; then
  echo "FAIL: no python3/python on PATH — cannot read the manifest version." >&2
  exit 1
fi
MANIFEST_JSON=$(git show "$TARGET_SHA:myvault/.obsidian/plugins/vaultbot/manifest.json" 2>/dev/null) || {
  echo "FAIL: cannot read manifest.json at $TARGET_SHA (does the commit exist on origin?)" >&2
  exit 1
}
VERSION=$(printf '%s' "$MANIFEST_JSON" | "$PYTHON_BIN" -c "import json,sys;print(json.load(sys.stdin)['version'])")
if [ -z "$VERSION" ] || [ "$VERSION" = "None" ]; then
  echo "FAIL: could not read a version from manifest.json at $TARGET_SHA" >&2
  exit 1
fi
TAG="v$VERSION"
echo "manifest version=$VERSION tag=$TAG"

# ── Step 2: resolve the commit the tag must point at. If TARGET_SHA is an
#    artifact of a merge queue (dynamic push), prefer the actual head of
#    main so users' git pull matches the release. Otherwise use TARGET_SHA.
TAG_COMMIT="$TARGET_SHA"

# ── Step 3: idempotency — if the real git tag already exists, nothing to do.
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
  echo "Tag $TAG already exists — nothing to publish."
  exit 0
fi

# ── Step 4: find an EXISTING release object for this tag (the release-
#    drafter draft, if any). Never create a duplicate when a draft exists.
RELEASE_ID=$(gh api \
  "repos/$REPO/releases?per_page=100" \
  --jq ".[] | select(.tag_name == \"$TAG\") | .id" \
  | head -n 1 || true)
if [ -n "$RELEASE_ID" ]; then
  IS_DRAFT=$(gh api "repos/$REPO/releases/$RELEASE_ID" --jq '.draft')
  if [ "$IS_DRAFT" = "false" ]; then
    echo "Release for $TAG already published — nothing to do."
    exit 0
  fi
  # Publish the draft, pinned to the CI-tested commit. Two explicit fields:
  # target_commitish so the tag lands on TAG_COMMIT, draft=false to publish.
  echo "Found draft release #$RELEASE_ID for $TAG — publishing at $TAG_COMMIT…"
  gh api -X PATCH "repos/$REPO/releases/$RELEASE_ID" \
    -f tag_name="$TAG" \
    -f target_commitish="$TAG_COMMIT" \
    -F draft=false \
    --jq '.html_url'
else
  # No draft — create the release ourselves at the tested commit.
  echo "No existing release for $TAG — creating it at $TAG_COMMIT…"
  gh release create "$TAG" \
    --repo "$REPO" \
    --target "$TAG_COMMIT" \
    --title "$TAG" \
    --generate-notes
fi

# ── Step 5: verify the outcome. A "successful" publish that didn't create
#    the real tag is a latent bug — fail loud so the summary shows it.
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
  echo "OK: $TAG published and tagged at $TAG_COMMIT."
else
  echo "FAIL: release published but git tag $TAG is missing — investigate." >&2
  exit 1
fi