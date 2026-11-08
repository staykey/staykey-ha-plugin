# Contributing to the Staykey Home Assistant integration

The Staykey integration connects Home Assistant to [Staykey](https://getstaykey.com),
streaming Z-Wave/Matter smart-lock and device events to your Staykey account so
short-term-rental (Airbnb/VRBO) hosts can automate guest access. See the
[README](README.md) for an overview and installation instructions.

Thanks for taking the time to contribute! This guide covers local development,
how we write commits, and the branch and release flow.

## Local development

Clone the repository and install the test dependencies:

```bash
git clone https://github.com/staykey/staykey-ha-plugin.git
cd staykey-ha-plugin
pip install -r requirements-test.txt
```

You do **not** need a Home Assistant install to work on the integration — the
test suite stubs the Home Assistant APIs it depends on, so everything runs
against a plain Python environment (3.13 in CI).

Run the test suite:

```bash
pytest
```

Lint and format with [Ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .          # lint
ruff format .         # auto-format
ruff format --check . # verify formatting (what CI runs)
```

CI runs `ruff check .`, `ruff format --check .`, and `pytest` on every pull
request, so run them locally before pushing.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/). Commit
messages drive automated releases via
[semantic-release](https://semantic-release.gitbook.io/), so the type you choose
determines the next version:

| Commit type | Effect on release |
|-------------|-------------------|
| `feat:` | minor version bump |
| `fix:` | patch version bump |
| `feat!:` or a `BREAKING CHANGE:` footer | major version bump |
| `docs:`, `chore:`, `ci:`, `style:`, `refactor:`, `test:` | no release |

Use a scope where it helps, for example:

```
feat(staykey): forward Matter lock state changes
fix(staykey): retry webhook delivery on 5xx
docs(readme): clarify HACS install steps
ci: pin setup-python to 3.13
```

### Release notes

Release notes are generated from the squash commit's title: the PR title, or
the commit subject when the PR has a single commit. Make both read the way you
want the note to read. `feat:` and `fix:` titles appear in the notes that
Home Assistant users see when they update, so write them for those users: say
what changes for them, not how the code changed.

| Instead of | Write |
|------------|-------|
| `fix(staykey): use only public zwave_js services for Z-Wave locks` | `fix(staykey): keep Z-Wave lock codes working across Home Assistant updates` |
| `fix(staykey): pass HACS and hassfest validation` | `ci: pass HACS and hassfest validation` (no user-facing change) |

When a change needs the user to do something, say exactly what in the
`BREAKING CHANGE:` footer, for example where to find a new option and what it
turns on.

Changes users won't notice (tooling, tests, refactors, docs) use `chore:`,
`ci:`, `test:`, `refactor:` or `docs:` and stay out of the notes.

## Branch & release flow

- Open pull requests against `main`.
- CI (Ruff + pytest) must pass before a PR can merge.
- PRs are **squash-merged**, so the squash commit message must follow
  Conventional Commits — that is the message semantic-release reads.
- Releases are fully automated by semantic-release when commits land on `main`.
  Pushing to an `rc/*` or `beta/*` branch produces a corresponding prerelease.

For the full release process and maintenance details, see the
[maintainer runbook](docs/maintainer-runbook.md).
