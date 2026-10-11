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

- **Keep the minimum Home Assistant version current.** HACS enforces the `homeassistant` key in `hacs.json`; users on an older Home Assistant are not offered the update. Do not add a `homeassistant` key to `custom_components/staykey/manifest.json` — it is not a valid manifest key and fails hassfest. When you raise the floor, also update the version badge and the **Requirements** list in `README.md`.
- **Keep the Validate workflow green.** `.github/workflows/validate.yml` runs the HACS action and hassfest on every pull request, on pushes to `main`, and weekly. The weekly run catches new upstream checks before they affect the HACS default store listing.
- **Review security alerts and dependency updates.** Triage Dependabot / security advisories on the repository and update the Node devDependencies used by the release tooling in `package.json`.
- **Triage issues and pull requests** on a regular cadence; label, respond, and close stale items.
- **Prune merged branches.** Auto-delete handles PR branches; periodically clean up any leftover `rc/*` and `beta/*` prerelease branches once their builds are no longer needed.
- **Keep documentation accurate.** Ensure `README.md` and `info.md` reflect current setup steps, supported devices, and configuration, since HACS renders these to users.

## Submitting to the HACS default store

Today the integration installs as a HACS **custom repository**: users add the repository URL by hand. Listing it in the HACS default store puts it in the community catalog, so users can search for and install it directly. From Home Assistant 2026.11, HACS is built into Home Assistant as the **Marketplace**, which uses the same catalog. In the Marketplace, adding a custom repository requires connecting a GitHub account, so a catalog listing matters more from that release on. When the catalog starts listing a repository that users added as a custom repository, HACS and the Marketplace switch those installs over automatically.

Listing is a one-time pull request to the `hacs/default` repository.

### Requirements

Validation runs on every PR, push to `main`, and weekly in `.github/workflows/validate.yml`, using the HACS action and hassfest. Both must pass with no ignored checks. The HACS checks cover:

- A public GitHub repository with a description, topics, issues enabled, and an OSI-approved license. Archived repositories are rejected.
- A valid `hacs.json`. Only the keys HACS documents are allowed. The `country` key, if set, must use ISO 3166-1 alpha-2 codes.
- A valid `custom_components/staykey/manifest.json` with `domain`, `name`, `documentation`, `issue_tracker`, `codeowners`, and `version`.
- Brand images in `custom_components/staykey/brand/`. At least `icon.png` must exist. `dark_icon.png` is the dark-theme variant. Since Home Assistant 2026.3, these local images are used directly, so no pull request to the `home-assistant/brands` repository is needed.
- A `README.md`.

### Submitting

1. Confirm the latest Validate run on `main` is green.
2. Publish a full GitHub release **after** that run. HACS requires a release, not just a tag, created after validation passes. The normal release flow above does this when a `feat:` or `fix:` commit merges.
3. From a **personal** GitHub account, fork `hacs/default` and create a branch from `master`. HACS does not accept submissions from organization forks, because maintainers can't edit those PRs. The submitter must be a repository owner or major contributor.
4. Add `staykey/staykey-ha-plugin` to the `integration` file. The list is sorted alphabetically, so insert the entry in its sorted position, not at the end.
5. Open the pull request and fill in its template completely: link the release, the successful HACS action run, and the successful hassfest run. Do not request reviews; HACS closes pull requests that do.

### After submitting

HACS maintainers review submissions in order, and the queue can take several months. Keep the Validate workflow green while the PR is open, because a failing check gets the PR moved back to draft. Once it merges, the repository appears in the next catalog scan. Then update the installation steps in `README.md` and the badge, replacing the custom-repository instructions with a search for **Staykey** in HACS or the Marketplace.
