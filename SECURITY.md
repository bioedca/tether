# Security Policy

## Supported versions

Tether is pre-1.0 and under active development. Until the first stable release
(`v1.0.0`, milestone M9), only the latest `main` and the most recent tagged
release receive security fixes.

| Version            | Supported          |
|--------------------|--------------------|
| `main` (latest)    | :white_check_mark: |
| latest `v0.x` tag  | :white_check_mark: |
| older `v0.x` tags  | :x:                |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Report privately via GitHub **[Private Vulnerability Reporting (PVR)](https://github.com/bioedca/tether/security/advisories/new)**
(Security → Advisories → "Report a vulnerability" on the repository). If PVR is
unavailable to you, email **bioedca@u.northwestern.edu** with subject
`SECURITY: tether`.

Please include:

- a description of the issue and its impact,
- the affected version / commit,
- reproduction steps or a proof of concept,
- any suggested remediation.

You can expect an acknowledgement within a few days. Once triaged, a fix and a
coordinated disclosure timeline will be agreed before any public detail is
shared.

## Supply-chain note

Dependencies are pinned via committed `conda-lock` files (base stack) and an
isolated `sidecar/conda-lock.yml` (tMAVEN sidecar). Dependabot updates `pip`
and `github-actions`, but **does not re-solve the conda lock files**; a
scheduled dependency audit (`deps-audit.yml`, `pip-audit` / `safety`) backstops
that gap. Secret scanning and push protection are enabled on the repository.
See PRD §12.8 for the full supply-chain posture.
