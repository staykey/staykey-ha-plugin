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

The release job creates a commit and a tag and pushes them **to the protected `main` branch**. It does this as **github-actions[bot]**, authenticating with the default `GITHUB_TOKEN` that GitHub injects into the workflow (see the `Run semantic-release` step in `release.yml`). That `github-actions[bot]` identity is on the **bypass list of the branch-protection ruleset**, so the automated `chore(release): … [skip ci]` commit and the release tag are allowed through without a PR. The `[skip ci]` marker prevents the release commit from re-triggering the workflow.

This is the only path that writes directly to `main`. Everything else goes through a reviewed, CI-gated PR.

### Fallback: if a release push is ever rejected by protection

If branch protection ever blocks the release push (for example, the default token loses its bypass, or you tighten the ruleset and forget to re-add the bot):

1. Create a **fine-grained personal access token** (or a GitHub App installation token) owned by a repository admin, scoped to this repository with **`contents: write`** permission.
2. Store it as a repository secret with the name that `release.yml` reads (update the `GITHUB_TOKEN` env in the `Run semantic-release` step to point at the new secret, e.g. `secrets.RELEASE_TOKEN`).
3. Ensure that token's identity is on the branch-protection ruleset **bypass list**, exactly as `github-actions[bot]` is today.

Prefer the default `GITHUB_TOKEN` + bot bypass while it works; the PAT/App path is only a contingency because tokens expire and need rotation.

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
