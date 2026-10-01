## What changed

Describe the problem, the resulting behavior, and any compatibility implications.
Link related issues when applicable.

## Validation

List the checks you ran and their results. Explain any checks you could not run.

- [ ] Ruff lint and format checks pass.
- [ ] Offline tests pass; relevant behavior changes have regression coverage.
- [ ] Documentation and examples reflect changes to public behavior.
- [ ] No credentials, callback URLs, or personal account/trading data are included.

For packaging changes, also run `uv build --no-sources` and
`uv run --locked python scripts/check_dist.py`.
