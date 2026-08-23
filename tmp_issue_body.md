## Why

The CI pipeline already auto-cuts a GitHub Release on every merge to main (reading the version from `manifest.json`). But the release notes are just the auto-generated commit list, which is raw and un-curated. Release-drafter complements this by maintaining a running draft release that categorizes PRs by type (bug fix, feature, docs, etc.) using labels and conventional-commit prefixes. When you're ready to publish, the draft is already organized.

This also works with the auto-merge workflow (#325): Dependabot patches labeled `automerge` merge silently, and release-drafter categorizes them under "Dependency updates" so they don't clutter the release notes.

**Security note (#330):** This workflow uses `pull_request_target`, which runs with the base repo's `GITHUB_TOKEN`. The workflow is scope-restricted to `permissions: { pull-requests: write, contents: write }` and does not execute fork code, so the exfiltration risk is minimal. SHA-pinning the action version should be added per #330.

## What

1. Add `.github/workflows/release-drafter.yml`
2. Add `.github/release-drafter.yml` config

### Workflow

```yaml
name: Release drafter
on:
  push:
    branches: [main]
  pull_request_target:
    types: [opened, reopened, labeled]

jobs:
  update-draft:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: write
    steps:
      - uses: release-drafter/release-drafter@v6
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Config (`.github/release-drafter.yml`)

```yaml
name-template: 'v$RESOLVED_VERSION'
tag-template: 'v$RESOLVED_VERSION'
categories:
  - title: '🚀 Features'
    labels:
      - 'enhancement'
  - title: '🐛 Bug Fixes'
    labels:
      - 'bug'
  - title: '🔒 Security'
    labels:
      - 'security'
  - title: '📝 Documentation'
    labels:
      - 'documentation'
  - title: '🏗️ CI/Build'
    labels:
      - 'github_actions'
  - title: '📦 Dependency updates'
    labels:
      - 'dependencies'
change-template: '- $TITLE @$AUTHOR (#$NUMBER)'
template: |
  ## Changes
  $CHANGES
```

## Effort

10 minutes -- two small files.

## Acceptance criteria

- [ ] `.github/workflows/release-drafter.yml` exists
- [ ] `.github/release-drafter.yml` config exists
- [ ] Draft release is auto-updated on every merge to main
- [ ] PRs are categorized by label in the draft release notes