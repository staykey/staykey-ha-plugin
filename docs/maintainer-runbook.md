# Maintainer runbook

Operational reference for maintaining the Staykey Home Assistant custom component: how releases happen, how branch protection interacts with the release bot, how to cut a release, ongoing maintenance, and how to submit to the HACS default store.

## Release flow

Releases are fully automated by [semantic-release](https://semantic-release.gitbook.io/). The configuration lives in the `release` key of `package.json`, and the pipeline runs from `.github/workflows/release.yml`.

The end-to-end flow on a push to a release branch:

1. **CI runs first.** `release.yml` has a `ci` job and a `release` job with `needs: ci`, which reuses `.github/workflows/ci.yml`. If CI fails, no release is produced.
2. **semantic-release analyzes commits.** Using the Conventional Commits preset, it inspects every commit since the last release tag to decide whether a release is warranted and what the next version is.
3. **Version bump.** If a release is warranted, semantic-release runs `scripts/update-manifest-version.cjs <version>`, which writes the new version into `custom_components/staykey/manifest.json` so the integration's reported version matches the release.
4. **Changelog.** `CHANGELOG.md` is updated with the generated release notes.
5. **Commit, tag, and push.** semantic-release commits `CHANGELOG.md` and `custom_components/staykey/manifest.json` with the message `chore(release): <version> [skip ci]`, creates the matching git tag, and pushes both to the repository.
6. **GitHub release.** A GitHub release is published for the new tag with the generated notes.

### What triggers a release

Releases are driven entirely by commit type (Conventional Commits):

| Commit type | Effect |
|-------------|--------|
| `feat:` | minor version bump |
| `fix:` | patch version bump |
| `feat!:` / any commit with a `BREAKING CHANGE:` footer | major version bump |
| `docs:`, `chore:`, `ci:`, `style:`, `refactor:`, `test:` | no release |

A merge to `main` that contains only no-release commit types will run CI but produce no new version. Only `feat:`, `fix:`, and breaking changes cut a release.

## Branch strategy

- **Feature work happens on a branch**, then opens a pull request against `main`. PRs require passing CI and are merged with **squash-merge**; the merged branch is auto-deleted. The squash commit subject determines the release impact, so write it as a proper Conventional Commit.
- **`main` is the stable release line.** A merge to `main` may publish a new stable release (per the rules above).
- **`rc/<name>` branches publish release-candidate prereleases** (versions tagged with an `-rc.N` suffix). The `rc-<name>` form works as well.
- **`beta/<name>` branches publish beta prereleases** (versions tagged with a `-beta.N` suffix). The `beta-<name>` form works as well.

Prerelease branches let you ship a testable build to HACS testers without affecting the stable channel.

## Branch protection and the release bot

`main` is protected: changes require a pull request and passing CI, merges are squashed, and merged branches are auto-deleted.

The release job creates a commit and a tag and pushes them **to the protected `main` branch** (the `chore(release): … [skip ci]` commit and the release tag). This is the only path that writes directly to `main`; everything else goes through a reviewed, CI-gated PR. The `[skip ci]` marker stops the release commit from re-triggering the workflow.

To get that push past branch protection, `release.yml` authenticates with an **admin-owned personal access token** stored as the repository secret **`RELEASE_TOKEN`** (used both by the `Checkout` step's `token:` and the `Run semantic-release` step's `GITHUB_TOKEN`). Because the token's owner is a repository admin and the **Repository admin** role is on the ruleset **bypass list**, the release push is allowed through without a PR.

Why a PAT rather than the default `GITHUB_TOKEN` / `github-actions[bot]`: GitHub only lets you grant the **GitHub Actions** integration a ruleset bypass at the **organization** level, not on a repository-level ruleset, so the default token cannot be bypassed here. The admin PAT is the supported path for a repo-level ruleset.

### Required setup: the `RELEASE_TOKEN` secret

Releases will fail to push until this secret exists. To (re)create it:

1. Create a **fine-grained personal access token** owned by a repository admin: GitHub → **Settings → Developer settings → Fine-grained tokens**. Scope it to **this repository only**, with **Repository permissions → Contents: Read and write**. Set a calendar reminder to rotate it before it expires.
2. Add it as a repository secret named **`RELEASE_TOKEN`**: repo **Settings → Secrets and variables → Actions → New repository secret** (or `gh secret set RELEASE_TOKEN`).
3. Confirm the token owner (a repo admin) is on the branch-protection ruleset **bypass list** (it is, via the Repository admin role).

Alternatively, an org owner can grant the **GitHub Actions** integration a bypass on an **organization-level** ruleset; then the default `GITHUB_TOKEN` works and no PAT is needed. `release.yml` falls back to `GITHUB_TOKEN` automatically when `RELEASE_TOKEN` is unset.

## Cutting a release

In normal operation you do not cut releases by hand:

- **Automatic (preferred).** Merge a PR containing at least one `feat:`, `fix:`, or breaking-change commit into `main`. The Release workflow runs CI, then semantic-release publishes the version, tag, and GitHub release.
- **Manual trigger.** The Release workflow has a `workflow_dispatch` trigger. From the repository's **Actions → Release** tab, run the workflow on the desired branch. semantic-release is idempotent: if there are no releasable commits since the last tag, it does nothing.
- **Prerelease.** Push to an `rc/<name>` branch for a release candidate or a `beta/<name>` branch for a beta. The same pipeline publishes a prerelease version and GitHub prerelease.

After a release, confirm: the new GitHub release exists, the tag is present, `CHANGELOG.md` has the new entry, and `custom_components/staykey/manifest.json` shows the new `version`.

## Maintenance checklist

Recurring upkeep for the repository:

- **Keep the minimum Home Assistant version current.** It is declared in two places that must stay consistent: `homeassistant` in `custom_components/staykey/manifest.json` and `homeassistant` in `hacs.json`. Bump them together when you raise the floor.
- **Review security alerts and dependency updates.** Triage Dependabot / security advisories on the repository and update the Node devDependencies used by the release tooling in `package.json`.
- **Triage issues and pull requests** on a regular cadence; label, respond, and close stale items.
- **Prune merged branches.** Auto-delete handles PR branches; periodically clean up any leftover `rc/*` and `beta/*` prerelease branches once their builds are no longer needed.
- **Keep documentation accurate.** Ensure `README.md` and `info.md` reflect current setup steps, supported devices, and configuration, since HACS renders these to users.

## Submitting to the HACS default store

Today the integration installs as a HACS **custom repository** (users add the repo URL manually). Listing it in the HACS default store is a one-time process that requires two external pull requests. These can only be done by someone able to open PRs against the upstream Home Assistant / HACS repositories.

### Prerequisites (already satisfied)

- Public GitHub repository with a description and topics set.
- At least one published GitHub release (the automated release flow above provides this).
- A valid `hacs.json` and a valid `custom_components/staykey/manifest.json` with `domain`, `name`, `documentation`, `issue_tracker`, `codeowners`, and `version`.
- A `README.md` and an `info.md`.

### Step 1 — Add the brand assets

Open a PR against **https://github.com/home-assistant/brands** adding the Staykey icon (and logo) under the custom integrations path:

- `custom_integrations/staykey/icon.png`
- `custom_integrations/staykey/logo.png` (optional but recommended)

Follow that repository's image size and format requirements (square icon, transparent background, PNG). The `staykey` directory name must match the integration `domain` in `manifest.json`.

### Step 2 — Add the repository to the HACS default list

Open a PR against **https://github.com/hacs/default** adding `staykey/staykey-ha-plugin` to the **`integration`** list (the file is sorted; insert it in the correct position). HACS validation runs against the repository to confirm the prerequisites above.

### Note

HACS maintainers review and merge default-store submissions on their own schedule. The brands PR is typically expected to be merged (or in flight) before the `hacs/default` PR is accepted. Once both land, users can install Staykey directly from the HACS default store without adding a custom repository.
