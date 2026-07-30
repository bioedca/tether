<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0060 — In-app update: download, verify against build provenance, hand off to the OS installer

- **Status:** accepted, with **one implementability conflict escalated to the maintainer** (§"The
  one decision that cannot be implemented as written"). Every other clause is decided and is not
  re-opened here.
- **Date:** 2026-07-30
- **Deciders:** bioedca (mechanism, integrity model, consent, channel — recorded 2026-07-27)
- **PRD anchor:** §4.1 (application shell), §4.3 (offline installer/sidecar), §8 NFR-PKG; introduces
  a new capability, so the PRD amendment lands with this record
- **Milestone:** M10

## Context and problem statement

Tether ships as a ~800 MB bundled `constructor` installer. Once installed, that copy is frozen:
nothing tells a scientist a newer release exists, and the only way onto it is to download and re-run
the whole installer past an OS trust warning. The maintainer wants installed copies to pick up new
releases.

Three project-specific facts make this the highest-consequence code path in the product, and all
three were re-verified against the tree at `414e809` rather than taken from the issue:

1. **No OS signature stands behind the artifact.**
   [ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) removed code-signing
   for 1.0 after SignPath Foundation declined enrollment. Whatever the updater checks *is* the entire
   trust boundary; there is no Authenticode or Gatekeeper backstop underneath it.
2. **The application has never made an outbound request.** `grep -rn
   "urllib.request\|requests\.get\|httpx\|urlopen\|socket\." src/tether/` returns exactly one line —
   `src/tether/project/lock.py:354`, `socket.gethostname()` for the `.lock` sidecar, which sends
   nothing. (The issue cites `:277`; the call is at `:354` on current `main`.) An update check would
   be the first byte Tether ever sends off the machine, and the **"No telemetry or analytics"**
   bullet on `docs/privacy.md` currently rests on that: *"It makes no network connection as part of
   normal analysis."* (Cited by bullet rather than by line: that page moved under this record once
   already, when #321 landed while this branch was held.)
3. **Offline operation is a product promise.** The installer is specified fully offline — no
   install-time git or network — for lab machines that are air-gapped or on restricted networks.

How does an installed copy learn about, obtain, and trust a new release without weakening any of
those three?

## Decision drivers

- The integrity check is load-bearing in a way it would not be for a notify-only design: a
  verification failure means arbitrary code execution.
- The base environment is **pin-and-hold** ([ADR-0004](0004-pin-and-hold-dual-lock-isolation.md)).
  A verifier that cannot be expressed in the lock is not a verifier this project can ship.
- An air-gapped machine must see no error, no dialog, and no startup delay.
- The installed launch surface from [ADR-0051](0051-installed-app-launch-surface.md) — the
  `<prefix>/bin/tether` and `<prefix>/bin/tether-gui` shims, the Windows Start Menu `.lnk`, the Linux
  `.desktop` — must survive an update.
- There is no automated way back from an update, so moving a user is a one-way door. (Note that
  `docs/stability.md`'s "no downgrade path" is about a `.tether` file's `schema_version` under
  *Project file compatibility*, not about application versions — it does not license this claim, and
  an earlier draft cited it as though it did.)

## Decision outcome

The maintainer settled five of the six questions on 2026-07-27. They are recorded here **as
decided**.

### 1. Scope — detect, download, verify, hand off. Never in-place, never silent

The application detects a newer **stable** release, downloads the installer for the running platform,
verifies it, then launches the OS installer and exits. It does not apply an in-place environment
update and it does not update silently.

This is deliberately the option with the highest consequence, and the ADR states the consequence
plainly: with no OS signature behind the artifact (driver 1), a verification bypass is arbitrary code
execution as the user.

### 2. Integrity — the GitHub build-provenance attestation, checked before anything is executed

A SHA-256 match against `SHA256SUMS.txt` is **necessary but not sufficient**: whoever can serve a
tampered asset can serve a matching manifest, because both come from the same place. The attestation
is what proves *which workflow, from which repository, at which commit* produced the artifact.

**Failure is refuse-and-report, never warn-and-continue.** If verification fails, or cannot be
performed at all, the update does not proceed and the user is told why. There is no "install anyway"
affordance — not behind a confirmation, not behind a setting.

#### Threat model

What an attacker must control to get code executed, and what stops them:

| Attacker capability | Without verification | With attestation verification |
| --- | --- | --- |
| DNS/TLS interception between the user and GitHub | Serves any binary; the app runs it | Attestation does not verify against the expected repository/workflow → refuse |
| Compromise of the release assets, manifest included | SHA-256 matches; the app runs it | Attestation is bound to a Sigstore-issued certificate for `bioedca/tether`'s release workflow → refuse |
| Compromise of a maintainer's GitHub credentials | — | **Not stopped.** An attacker who can push a tag and run the release workflow produces a genuine attestation. This is the residual risk, and it is the same one the release pipeline already carries. |
| Local attacker with write access to the install prefix | Already game over | Already game over — out of scope |

The middle two rows are the reason a checksum alone was rejected. The last row is stated so nobody
reads this mechanism as stronger than it is.

### 3. Consent — off until an explicit first-run prompt is answered

First launch asks once and remembers the answer. **Until the user answers, no outbound request is
made** — which preserves the offline promise for a machine that never gets asked, because it never
gets a chance to say yes silently.

The stored answer **must survive an update**. A setting that resets on upgrade would re-prompt
forever and, worse, would silently re-enable checking for someone who had declined.

**This is unsolved in the current tree and is the implementer's problem to solve first.**
`grep -rn "QSettings\|platformdirs\|appdirs\|user_config_dir" src/tether/` returns **nothing** —
Tether has no application-level settings store at all today. Every setting it persists lives inside a
`.tether` *project*, which is the wrong scope for a machine-level consent flag and does not survive
a reinstall. The follow-up issue for consent therefore delivers the settings store, not just the
flag.

Its location is an open question that issue must answer, not assume. The obvious hazard is storing
it under the install prefix: whether a `constructor` re-install preserves, replaces or removes prefix
contents has **not** been established here, and "the consent flag survived the upgrade" is exactly
the kind of thing that appears to work until the one upgrade path that clears it. The issue should
determine the per-OS behaviour empirically and site the store where the answer does not matter.

### 4. Channel — stable only

Prereleases are never offered. `v1.0.0-rc1` and its successors must be invisible to this mechanism,
because a prerelease is not a supported target and this mechanism provides no way back: moving a
scientist onto an RC could only be undone by a manual reinstall, which is the opposite of what an
updater is for. A yanked or deleted release is treated as "no update available", never as
a reason to offer the next-newest.

### 5. Privacy disclosure — amended in the PR that adds the network call

`docs/privacy.md` is amended **in the PR that actually adds the request** — never before (the page
would describe behaviour that does not exist) and never after (the page would be false in a shipped
release). It must disclose: that the request goes to the GitHub releases API; that it carries an IP
address and a user-agent and **no identifiers and no telemetry**; that it happens only after the
first-run prompt is accepted; and how to turn it off permanently.

### 6. Per-OS apply mechanics — specified as constraints, resolved by the implementation

The decisions above fix *what* happens. *How*, per platform, is where the remaining engineering sits:

- **Windows.** A running process cannot replace its own files. The hand-off must exit before the
  installer touches the prefix, so the update is "launch installer, then quit", not "quit after".
- **macOS.** `.pkg` installs differ again, and per ADR-0051 macOS gets the shims only — there is no
  `.app` bundle to relaunch.
- **Linux.** The `.sh` installer and the `~/.local/share/applications/tether.desktop` entry.
- **All three.** The launch surface must survive: `<prefix>/bin/tether`, `<prefix>/bin/tether-gui`,
  the Start Menu `.lnk`, the `.desktop`. An update that silently orphans the shortcut a scientist
  launches from has failed even if the bytes landed.
- **Offline.** No network on startup unless consent was given; no error, no dialog, no startup delay
  on an air-gapped machine. A site administrator must have a documented way to disable the mechanism
  permanently and machine-wide.

## The one decision that cannot be implemented as written

Decision 2 says verification happens **in-process**, and names *"a sigstore verifier in the base
`conda-lock`"* as its consequence. Those two cannot both hold. This is not a preference; it is a
packaging fact, measured against the live index and the committed lock on 2026-07-30:

| Fact | Evidence |
| --- | --- |
| **No conda package for `sigstore` exists.** Not on conda-forge, not on any channel. | `api.anaconda.org/search?name=sigstore&type=conda` → **zero results**; `api.anaconda.org/package/conda-forge/sigstore` → **404** |
| `sigstore` (the Python in-process verifier) is **PyPI-only** | PyPI `sigstore` 4.5.0, `requires_python >=3.10` |
| The base lock is **100% conda-managed** — there is not one pip entry to follow | all **1192** entries in `conda-lock.yml` have `manager: conda`; pip-managed count is **0** |
| It would also drag in a currently-absent tree | `cryptography` is **absent** from the base lock today |
| conda-native verifiers **do** exist, on all four target platforms | `cosign` **3.0.4** and `gh` **2.96.0**, both covering `linux-64`, `osx-64`, `osx-arm64`, `win-64` |

So an in-process verifier means making `sigstore` the **first pip-managed entry in the 1192-entry,
100%-conda, pin-and-hold lock**, and pulling `cryptography` and its transitive tree in behind it.
That is a materially larger change than "a lock bump", and it weakens the very bill-of-materials
guarantee that the `.tether` provenance stamps refer to.

Two words there are load-bearing and were wrong in a first draft. **"Entry", not "package":** 1192
counts lock entries across four platforms (`linux-64` 331, `osx-64`/`osx-arm64`/`win-64` 287 each,
353 distinct names), not the size of any environment a user installs. **"Lock", not "environment":**
the installed environment already contains pip-installed packages — `packaging/scripts/post_install.sh`
and its `.bat` twin `pip install --no-index --no-deps` the `tether` and `tmaven` wheels into it, which
[ADR-0049](0049-m9-packaging-constructor-architecture.md) and
[ADR-0051](0051-installed-app-launch-surface.md) both record. What is 100% conda is the **lock**, and
the lock is the bill of materials the provenance stamps refer to, so the argument is unaffected.

**Recommended resolution, and what this ADR assumes unless the maintainer says otherwise:** keep
every substantive part of decision 2 — attestation-based verification, checksum treated as necessary
but not sufficient, refuse-and-report — and **bundle a conda-native verifier binary from conda-forge
into the base lock**, invoked as a subprocess.

The intent of "in-process" is, as far as this record can tell, *"the verifier ships with the
application, pinned in the lock, with no user prerequisite"* — and a bundled `cosign` satisfies that
exactly. The trust boundary is identical either way; what changes is the call mechanism. Shelling out
to a tool **the user may or may not have** was rightly rejected in the issue; shelling out to a tool
**we ship and pin** is a different proposition and is not weaker.

If the maintainer's intent was literally in-process Python, then the honest answer is that this
feature requires a first-of-its-kind pip dependency in the base lock, and that trade should be
decided explicitly rather than discovered during implementation.

Either way the lock bump is a **separate, deliberate PR** with its own regeneration and
`conda-lock-verify` run. It must not be smuggled in as a side effect of the feature.

### Considered and rejected

- **Checksum only.** Rejected in decision 2: the manifest and the asset share an origin, so it proves
  nothing an attacker who controls that origin cannot forge.
- **Shell out to a `gh` the user might have.** Rejected in the issue. Silent unavailability on most
  lab machines would turn refuse-and-report into never-updates, or tempt a warn-and-continue path.
- **In-place environment update from the released `conda-lock.yml`.** Genuinely attractive — the
  locks are already published as release assets precisely because they are the authoritative bill of
  materials, and it would move megabytes instead of ~800 MB. The maintainer chose installer hand-off,
  so this is recorded as rejected, not re-argued. It remains the obvious candidate if hand-off proves
  unworkable on Windows.
- **Notify-only.** Lowest consequence and lowest value; rejected in decision 1.

## Consequences

- **Good.** Installed copies stop silently rotting on an old release. The integrity story is
  *stronger* than the one code-signing would have given for the download path, because an attestation
  binds the artifact to a workflow, repository and commit, where Authenticode binds it only to a
  publisher identity.
- **Bad / trade-off.** This adds the application's first network egress, the first thing it downloads
  and executes, and a first-run prompt that every user now sees. `docs/privacy.md`'s strongest claim
  gets a caveat it did not have. The offline promise survives only because consent gates the request,
  which makes the consent implementation security-relevant rather than cosmetic.
- **Blocked on.** [ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) — the
  premise of this design is that no OS signature backs the artifact. And on `v1.0.0` existing: there
  is nothing to update from until then.
- **Follow-up.** No updater code ships with this record. The implementation is broken out into the
  issues listed below, each of which is separately reviewable and separately revertible.

## Follow-up implementation issues

Ordered by dependency. Four are filed and are linked by number; **two are deliberately not filed
yet**, and which is which is stated rather than left to be discovered.

1. **[#330](https://github.com/bioedca/tether/issues/330) — application settings store** outside the
   install prefix, with a value that survives an update and a machine-wide administrator override.
   Prerequisite for everything else, and absent from the tree entirely today.
2. **The base-lock verifier bump** — a standalone re-lock adding the chosen conda-forge verifier,
   with `conda-lock-verify` green and a re-tested GUI stack. **Not filed:** its content depends
   entirely on how the maintainer answers the conflict above, and filing it now would bake in an
   answer that has not been given.
3. **[#248](https://github.com/bioedca/tether/issues/248) — release query + channel policy**, the
   stable-only lookup gated on consent, with the air-gapped path proven to produce no error, no
   dialog and no startup delay. Note that #248 **predates this record** (filed 38 minutes before the
   decision comment) and is scoped to check-and-notify. That is not a contradiction of decision 1 —
   it says so itself, *"implementable against any outcome of that design"* — but it is the check
   half only, and it is now blocked on #330 for the consent flag it assumes.
4. **Download + attestation verification**, with refuse-and-report and a test that a *tampered* asset
   is rejected — the security-critical unit, and it should land with an adversarial test rather than
   a happy-path one. **Not filed:** same reason as 2. What it verifies *with* is unresolved.
5. **[#331](https://github.com/bioedca/tether/issues/331) — per-OS hand-off**, including the
   launch-surface survival check from ADR-0051 and the interrupted-update behaviour.
6. **The `docs/privacy.md` amendment**, which by decision 5 lands *inside* #248 rather than as its
   own PR — listed here so it is not forgotten, not so it is separated.

An earlier draft of this section claimed all six were filed when none were. They are enumerated with
their real numbers now precisely because a decision record that overstates its own follow-through is
the kind of thing a future reader has no way to check.

## More information

- [ADR-0059](0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) — why no OS signature
  backs the artifact, and the attestation this design verifies.
- [ADR-0051](0051-installed-app-launch-surface.md) — the launch surface an update must not orphan.
- [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) — the pin-and-hold invariant the verifier
  choice runs into.
- `docs/privacy.md`, the **"No telemetry or analytics"** bullet — the claim this feature amends.
- `docs/release.md` — the manifests and attestation as published today, and how to verify them by
  hand.
- [GitHub artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
  — what `attest-build-provenance` produces and what verifying it proves.
