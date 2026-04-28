# Contributing

Thanks for your interest in `rag-on-azure`. This document captures the working conventions; the architectural source of truth is [`docs/design/rag-on-azure.md`](docs/design/rag-on-azure.md).

## Conventional Commits

Every commit and every PR title must follow the [Conventional Commits](https://www.conventionalcommits.org/) format. Allowed types:

| Type        | Meaning                                          | Version impact      |
| ----------- | ------------------------------------------------ | ------------------- |
| `feat:`     | New user-visible feature                         | Minor bump          |
| `fix:`      | Bug fix                                          | Patch bump          |
| `chore:`    | Routine work, dependency bumps, infra plumbing   | None                |
| `docs:`     | Documentation only                               | None                |
| `refactor:` | Code change with no behavioural impact           | None                |
| `test:`     | Tests only                                       | None                |
| `ci:`       | CI / build pipeline                              | None                |
| `build:`    | Build system / packaging                         | None                |
| `perf:`     | Performance improvement                          | Patch bump          |

A breaking change is denoted with `!` after the type (e.g. `feat!:`). While the repo is on the pre-major `v0.x.x` lock, breaking changes bump the **minor**, never the major — version bumps are managed automatically by [Release Please](https://github.com/googleapis/release-please) and the `release-please-config.json` at the repo root.

Rule of thumb: **one logical change per commit, one logical change per PR**.

## Branch model

- `main` is the only long-lived branch and is protected.
- All changes go through pull requests against `main`.
- Linear history is enforced — rebase your branch onto `main` before merging.
- Force-pushes to `main` are blocked.

## Pull requests

PRs use the template at [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md). Each PR should:

- Use a Conventional Commits PR title.
- Link the relevant section of `docs/design/rag-on-azure.md`.
- Pass `pre-commit run --all-files` locally.
- Pass CI: lint → unit → integration → secrets scan → build → bicep what-if.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install pre-commit
pre-commit install
```

`pre-commit install` wires both the standard `pre-commit` hook (lint, format, secrets scan) and the `commit-msg` hook (Conventional Commits validation).

## Security

Never commit personal email addresses, real Azure subscription IDs, real tenant GUIDs, API keys, or any value resembling a secret. The `gitleaks` pre-commit hook is the first line of defence; GitHub secret scanning + push protection is the second.
