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

## Branch & release flow

- Open pull requests against `main`.
- CI (Ruff + pytest) must pass before a PR can merge.
- PRs are **squash-merged**, so the squash commit message must follow
  Conventional Commits — that is the message semantic-release reads.
- Releases are fully automated by semantic-release when commits land on `main`.
  Pushing to an `rc/*` or `beta/*` branch produces a corresponding prerelease.

For the full release process and maintenance details, see the
[maintainer runbook](docs/maintainer-runbook.md).
