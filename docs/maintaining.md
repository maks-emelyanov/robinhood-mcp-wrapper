# Maintaining and releasing

This guide covers publication preparation. A local checkout and passing checks do not create a
GitHub repository, configure repository settings, or publish a release automatically.

## Before the first GitHub publication

Choose the repository owner and name, then create or connect the actual GitHub repository.
Inspect the complete diff and history for credentials or personal/account data before pushing.
Keep `.env`, credential files, test artifacts, and build output out of version control.

After the repository exists, maintainers with the required permissions should configure:

- GitHub Actions to run the committed CI workflow for pull requests and the default branch.
- A protected default branch or ruleset requiring review and the actual CI job names after the
  first successful run. Use the repository's real default branch name.
- Private vulnerability reporting under repository security settings and a process for reading
  those reports. Update [SECURITY.md](../SECURITY.md) if a verified private contact is added.
- Dependabot alerts and security update settings as appropriate for the repository owner.
- GitHub topics, a short description, and issue settings that match the README and project scope.

Do not add badges, package-index links, repository URLs, or `CODEOWNERS` identities until they
refer to real configured resources. The checked-in contributor templates work without invented
contacts or ownership entries.

## Prepare a release

Choose a version and update both `pyproject.toml` and `src/robinhood_mcp/__init__.py`. Move reviewed
`Unreleased` entries in [CHANGELOG.md](../CHANGELOG.md) into a dated version section, stating any
breaking changes, dependency changes, and migration steps. If project metadata changes, run
`uv lock` and review the resulting lockfile.

From a clean checkout of the release candidate, run:

```bash
uv sync --locked --all-groups
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -m 'not live' --cov=robinhood_mcp --cov-report=term-missing
uv build --no-sources
uv run --locked python scripts/check_dist.py
```

The distribution check verifies archive contents and version metadata, installs the wheel into
an isolated environment, and checks import and CLI behavior outside the checkout. Run it again
if code or metadata changes after the initial build. Review source and wheel contents for
accidental credentials or generated files before uploading them.

The live test is optional discovery-only verification and must not be required for CI or a
release. Account secrets should never be made available to pull request workflows.

## Publish deliberately

Commit the reviewed release changes using the repository's normal review process. After the
release commit passes required checks, create a matching version tag such as `v0.1.0` and a
GitHub release with the changelog text and built wheel/source archive if those artifacts are
intended for distribution.

Package-index publication is a separate maintainer decision. Create and verify ownership of
the intended package-index project, choose its authentication policy, and review release
artifacts before uploading. This repository does not assume a package-index account or
automatically publish packages. Only add installation instructions for a published version
once that version exists.

After publication, verify the release links, download the published artifacts, and check the
installed version and CLI help from an isolated environment. Create a fresh `Unreleased` section
for subsequent changes. Keep security reports and credential incident response private.
