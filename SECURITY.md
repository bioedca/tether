# Security Policy

## Report a vulnerability

**Do not report a security vulnerability through a public GitHub issue, discussion or pull
request.**

Report it privately through GitHub
**[Private Vulnerability Reporting (PVR)](https://github.com/bioedca/tether/security/advisories/new)**.
On the repository, open Security → Advisories → "Report a vulnerability". If PVR is not available
to you, email **<bioedca@u.northwestern.edu>** with the subject `SECURITY: tether`.

Include these items in the report:

- a description of the issue and its impact,
- the affected version or commit,
- the reproduction steps or a proof of concept,
- any remediation that you suggest.

You can expect an acknowledgement within a few days. After the triage, the project and you agree
on a fix and a timeline for coordinated disclosure. Nobody shares a public detail before that
agreement.

## Supported versions

**Today:** `v1.0.0` is not tagged yet. Security fixes land on the latest `main` and on the newest
published release, which is a pre-release. The
[Releases page](https://github.com/bioedca/tether/releases) lists it. Where a release is
warranted, the fix ships in a new tag cut from `main`. Older `0.x` tags and older pre-releases
get no back-patch.

**From `v1.0.0` onward:** security fixes land on the `1.x` line. The latest `main` and the most
recent `1.x` release receive them, and a fix ships as a patch release. Older `1.x` releases get no
back-patch, so upgrade to the latest `1.x`. At that point the `0.x` development line becomes
end-of-life.

By policy, that upgrade keeps your `.tether` projects readable. It also keeps the `tether` command
line and the small covered Python API working. The stability policy
([`docs/stability.md`](docs/stability.md) in this repository) says exactly which names are
covered. After that release is deployed, the policy is also published at `/stability/` on the
[documentation site](https://bioedca.github.io/tether/). Anything outside that list may change in
any release, so pin your version if you script against it.

Read the table below against that changeover. The `0.x` rows apply until `v1.0.0` is tagged, and
the `1.x` rows apply from then on. No release line is supported on both sides of the changeover.

| Version                           | Supported                                    |
|-----------------------------------|----------------------------------------------|
| `main` (latest)                   | :white_check_mark: always                    |
| newest published pre-release      | :white_check_mark: until `v1.0.0`; :x: after |
| older `0.x` tags and pre-releases | :x:                                          |
| `1.x` (latest release)            | :white_check_mark: once `v1.0.0` ships       |
| older `1.x` tags                  | :x:                                          |

## Supply-chain note

The committed `conda-lock` files pin the dependencies of the base stack. The isolated
`sidecar/conda-lock.yml` pins the tMAVEN sidecar, and the isolated `deep/conda-lock.yml` pins
PyTorch. Dependabot updates `pip` and `github-actions`, but it **does not re-solve the conda lock
files**. A scheduled dependency audit (`deps-audit.yml`, with `pip-audit` and `safety`) checks the
locked PyPI packages only. It does not check the packages that come only from conda, so that part of
the gap stays open.

Secret scanning and push protection are on for the repository. `docs/PRD.md` §12.8 gives the
full supply-chain posture.
