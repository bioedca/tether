# Security Policy

## Supported versions

**Today:** `v1.0.0` has not been tagged yet. The newest tag is `v0.8.0`, so
security fixes land on the latest `main` and, where a release is warranted, on a
new tag cut from it. Older `0.x` tags are not back-patched.

**From `v1.0.0` onward:** security fixes land on the `1.x` line — the latest
`main` and the most recent `1.x` release receive them, and a fix is shipped as a
patch release. Older `1.x` releases are not back-patched; upgrade to the latest
`1.x`. By policy that upgrade keeps your `.tether` projects readable and keeps
the `tether` command line and the small covered Python API working — see the
stability policy ([`docs/stability.md`](docs/stability.md) in this repository;
published at `/stability/` on the [documentation
site](https://bioedca.github.io/tether/) once that release is deployed) for
exactly which names are covered. Anything outside that list may change in any
release, so pin your version if you script against it. At that point the `0.x`
development line becomes end-of-life.

The table below is read against that changeover: the `0.x` rows apply until
`v1.0.0` is tagged, the `1.x` rows from then on. No release line is supported on
both sides of it.

| Version                 | Supported                             |
|-------------------------|---------------------------------------|
| `main` (latest)         | :white_check_mark: always              |
| `v0.8.0` (latest tag)   | :white_check_mark: until `v1.0.0`; :x: after |
| older `0.x` tags        | :x:                                    |
| `1.x` (latest release)  | :white_check_mark: once `v1.0.0` ships |
| older `1.x` tags        | :x:                                    |

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
isolated `sidecar/conda-lock.yml` (tMAVEN sidecar), and isolated
`deep/conda-lock.yml` (PyTorch). Dependabot updates `pip`
and `github-actions`, but **does not re-solve the conda lock files**; a
scheduled dependency audit (`deps-audit.yml`, `pip-audit` / `safety`) backstops
that gap. Secret scanning and push protection are enabled on the repository.
See PRD §12.8 for the full supply-chain posture.
