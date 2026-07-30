<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0060 — In-app update: download, verify against build provenance, hand off to the OS installer

- **Status:** accepted. Decision 2's *mechanism* was amended by the maintainer on 2026-07-30 — the
  originally-specified in-process verifier is not implementable, and the resolution is
  `gh attestation verify --bundle` shipped via `extra_files` (§"Amending decision 2's mechanism").
  Everything decision 2 requires *substantively* is unchanged.

  **One item is left open, deliberately**, and is named here so a reader checking the six decisions
  off does not have to go looking: decision 3 asks this record to state *where* the consent answer is
  stored. It does not, because the tree has no application settings store at all and the per-OS
  re-install behaviour that decides the right location has not been measured. Stating a location here
  would be a guess presented as a decision; [#330](https://github.com/bioedca/tether/issues/330)
  measures it instead (§3).
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

Decision 2 as given also fixed *where* the check runs: **in-process**, "checked inside the
application before the downloaded file is executed", with "a sigstore verifier in the base
`conda-lock`" named as its consequence. That clause could not be implemented as written — a packaging
fact, not a disagreement — and the maintainer amended it on 2026-07-30 to a bundled
`gh attestation verify --bundle` invoked as a subprocess. See §"Amending decision 2's mechanism".
**Nothing else in this section changes**: what is verified, what is necessary-but-not-sufficient, and
what happens on failure are all as decided.

A SHA-256 match against `SHA256SUMS.txt` is **necessary but not sufficient**: whoever can serve a
tampered asset can serve a matching manifest, because both come from the same place. The attestation
is what proves *which workflow, from which repository, at which commit* produced the artifact.

**Failure is refuse, never warn-and-continue.** If verification fails, or cannot be performed at all,
the update does not proceed. There is no "install anyway" affordance — not behind a confirmation, not
behind a setting.

Whether the refusal is *reported* depends on which of the two it was, and the distinction is drawn in
§"Refuse-and-report has a limit worth stating plainly" — a verification that ran and failed is told to
the user; one that could not run is silent. Do not read the maintainer's "the user is told why" as
covering both: it cannot, and pretending otherwise would put an unimplementable requirement in an
accepted record.

#### Threat model

What an attacker must control to get code executed, and what stops them:

| Attacker capability | Without verification | With attestation verification |
| --- | --- | --- |
| DNS/TLS interception between the user and GitHub | Serves any binary; the app runs it | Attestation does not verify against the expected repository/workflow → refuse |
| Compromise of the release assets, manifest included | SHA-256 matches; the app runs it | Attestation is bound to a Sigstore-issued certificate for `bioedca/tether`'s release workflow → refuse |
| **The same interceptor, replaying a genuine older release** | Serves an old Tether; the app runs it | **Not stopped by the attestation.** A previously published installer carries a real, verifying attestation, so an attacker who also controls the release *query* can answer "what is newest" with a genuine old artifact and roll a user back onto known-vulnerable code. See the rollback requirement below. |
| **The same interceptor, relabelling a genuine prerelease as stable** | Serves an RC as if stable | **Not stopped by the attestation, and not stopped by the API's `prerelease` flag either** — the attacker controls that flag. A newer RC passes both provenance and the strictly-newer check. Stopped only by deriving "stable" from the **verified** `sourceRepositoryRef` tag (§4). |
| Compromise of a maintainer's GitHub credentials | — | **Not stopped.** An attacker who can push a tag and run the release workflow produces a genuine attestation. This is the residual risk, and it is the same one the release pipeline already carries. |
| Replacing the downloaded file between verification and execution | Runs the substituted file | **Not stopped by the attestation**, which proves a property of bytes at one instant. Closing the verify-to-execute window is [#331](https://github.com/bioedca/tether/issues/331)'s problem, not the verifier's. |
| Local attacker with write access to the install prefix | Already game over | Already game over — out of scope |

Rows 2 and 3 are the reason a checksum alone was rejected. The last row is stated so nobody reads
this mechanism as stronger than it is — and the three "not stopped" rows are stated for the same
reason. An artifact signature of any kind proves *origin*, never *freshness* and never *liveness*;
reading attestation verification as protection against rollback or substitution is the specific
mistake this table exists to prevent.

**Rollback requirement, following from row 3.** An offered release must be **strictly newer than the
installed version**, compared as a version rather than trusted from the query, and the comparison
happens on the client. Refusing to move backwards is not an optimisation — it is the only control
here that addresses row 3 at all, because the artifact it would install is genuinely ours and every
signature-style check will pass. This does not re-open decision 4; stable-only and
strictly-newer-than-installed are different constraints and both apply.

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

**"Stable" must be derived from the verified tag, not from the API's `prerelease` flag.** The threat
model already assumes an attacker who controls the release query; that same attacker controls the
flag. They can relabel a genuine, genuinely-attested release candidate as stable, and a *newer* RC
then passes both the strictly-newer check and provenance verification, because the artifact really is
ours. The server-side filter is a convenience, not a control.

The trustworthy signal is the one the attestation binds: the **certificate's** `sourceRepositoryRef`
(`refs/tags/v1.0.0-rc1` on the published RC). Read it from `--format json` at
**`verificationResult.signature.certificate`**, and refuse anything carrying a SemVer prerelease
component.

**Not from the statement.** An earlier revision said "out of the verified statement", which is wrong
by one level and would have reintroduced the very problem this paragraph fixes: `gh` documents that
only `verificationResult.signature.certificate` and the verified timestamps are non-manipulable, while
`verificationResult.statement.predicate` is **controlled by the originating workflow**. A check that
reads the tag from the statement is trusting attacker-reachable metadata to decide whether to trust
the artifact. Ordering matters too — the check runs *after* verification succeeds, never on unverified
metadata.

### 5. Privacy disclosure — amended in the PR that adds the network call

`docs/privacy.md` is amended **in the PR that actually adds the request** — never before (the page
would describe behaviour that does not exist) and never after (the page would be false in a shipped
release). It must disclose: that the requests go to **three** endpoints — the GitHub releases and
attestations APIs and the Sigstore TUF trusted-root service (§"Tether owns every network step"); that
**each of the three** observes the machine's IP address and a user-agent, which is what "unauthenticated
HTTP" means and is not avoidable; that Tether adds **no account, no installation id, no usage data and
no telemetry** on top of that; that they happen only after the first-run prompt is accepted; and how to
turn it off permanently.

The decision comment names only the GitHub request because the Sigstore one is a consequence of the
mechanism chosen later. Recording the narrower version would understate what a user's network exposes.

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

## Amending decision 2's mechanism: how verification runs

Decision 2 says verification happens **in-process**, and names *"a sigstore verifier in the base
`conda-lock`"* as its consequence. Those two cannot both hold. This section was written as an
escalation; the maintainer resolved it on 2026-07-30 and it is now recorded as decided.

**Decided: `gh attestation verify --bundle`, shipped as a pinned-and-hashed `extra_files` artifact,
with the bundle fetched from the public attestations API. Not from conda-forge, and not in the lock.**

Everything substantive in decision 2 survives intact — attestation-based verification, checksum
necessary-but-not-sufficient, refuse-and-report with no "install anyway". What changed is the call
mechanism (subprocess, not in-process) and the delivery mechanism (`extra_files`, not the lock).

The packaging fact that forced it, measured against the live index and the committed lock on
2026-07-30:

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

### The lock is not touched at all

An earlier draft of this record framed the choice as *"bundle into the base lock"* versus *"first pip
entry in the base lock"*. **That was a false binary**, and the third option was already proven in this
repository: `construct.yaml`'s `extra_files` with a pinned, hashed, single-source-of-truth artifact —
the pattern [#306](https://github.com/bioedca/tether/issues/306) established for the bundled
`setuptools` wheel. It ships a third-party binary with full provenance and **never re-solves the
1192-entry lock**.

So the re-lock this record previously insisted must be "a separate, deliberate PR" is not owed at
all. That is the single biggest cost the escalation was about, and it evaporates.

It also decouples the verifier from the conda-forge feedstock, which matters more than it sounds: the
`cosign` feedstock sits at **3.0.4 against upstream 3.1.2** — roughly six months and four releases
behind, three versions ever published, one recipe maintainer. Pin-and-holding a *security* tool to a
channel that does not patch it promptly inverts the point of pinning. `extra_files` pins upstream
directly, by hash.

### Why `gh` rather than `cosign`

Both verify Tether's real published attestation and both fail closed; this was tested against the
actual `v1.0.0-rc1` bundle, fetched unauthenticated, not reasoned about. The difference is **how much
of the security logic Tether has to author**.

`cosign` is a signature engine and not a sufficient verifier on its own:

- It **ignores `--type` entirely** — a `spdxjson` predicate and a nonsense predicate URI both returned
  `Verified OK`, exit 0.
- It binds the **digest only, never the subject filename**, so the Linux `.sh` verifies happily
  against the bundle being used for the Windows `.exe`.
- The caller must hand-author the Fulcio identity policy. A wrong `--certificate-identity-regexp`
  **silently accepts any Sigstore-signed artifact from any repository** — and that regex would be the
  single highest-consequence line in the product.

`gh attestation verify` encodes GitHub's own policy instead: `--repo`, `--signer-workflow` and
`--source-ref` express the constraint directly, and it enforces the predicate type. It is also the
path GitHub's own offline-verification documentation prescribes, and it is the command
`docs/release.md` already tells users to run by hand — so the app does automatically what a user can
reproduce.

**The authentication trap, and why it does not apply.** `gh attestation verify <file> --repo <owner/repo>`
**refuses without a token** — exit 4, before any network call, even for a public repository. A first
reading of that disqualifies `gh` outright, and this record said so in an earlier draft. It is wrong:
the gate is `gh`'s own client-side policy, not an API restriction, and it applies only to the path
where `gh` fetches the bundle itself. Two facts, both verified:

- The attestations REST endpoint is **fully public** — `GET /repos/bioedca/tether/attestations/{digest}`
  with **no `Authorization` header** returns HTTP 200 and a complete
  `application/vnd.dev.sigstore.bundle.v0.3+json` bundle.
- `gh attestation verify <file> --bundle <file>` then verifies **with no credentials at all**, exit 0,
  demonstrated end-to-end against the real 624 MB `v1.0.0-rc1` macOS arm64 installer.

So Tether fetches the bundle itself and passes `--bundle`. No token, on any user's machine, ever. No
release-pipeline change is required either: the bundle comes from the API, not from a release asset.

### What Tether must still do itself

Neither tool is sufficient alone, so these are **requirements, not polish**:

1. **Bind the artifact to the subject.** Assert the download's SHA-256 equals the subject digest *and*
   that the subject **filename** is this platform's installer. The bundle covers all four installers
   at once, so a digest match alone does not establish that the file is the one for this platform.
2. **Read the API response as explicit UTF-8.** Reading it with the platform default encoding
   (`cp1252` on Windows) corrupts the em-dash in the Rekor checkpoint and produces a **false
   verification failure that is indistinguishable from tampering**. Verified by A/B test. This is a
   Windows-only footgun in the exact code this feature needs.
3. **Pin `gh >= 2.67.0`.** [CVE-2025-25204](https://github.com/cli/cli/security/advisories/GHSA-fgw4-v983-mgp8)
   made `gh attestation verify` **fail open** — exit 0 on a predicate-type mismatch — in 2.49.0
   through 2.66.x. A fail-open in the exact command this design depends on is the strongest possible
   argument for both the version floor and for requirement 1 as defence in depth.
4. **Prefer the inline `bundle`, tolerate `bundle_url`.** The API returns both today; the `bundle_url`
   variant is a snappy-compressed blob and GitHub has served `bundle: null` inline in some cases.

### Refuse-and-report has a limit worth stating plainly

Decision 2 asks that on failure "the user is told why". **That is not achievable**, and the record
should say so rather than imply a capability the implementation cannot deliver. `gh` collapses every
verification outcome to exit 1 — a tampered artifact and an unreachable network are indistinguishable
by exit code, and only exit 4 (auth) is separable. `cosign` behaves the same, and its stderr is
explicitly outside its versioning contract.

So the honest split is:

- **Verification ran and failed** → refuse, visibly.
- **Verification could not run** (no network, blocked proxy, rate-limited) → **silent no-op**. Not a
  dialog, not an error. An air-gapped machine must be unable to tell the difference between "this
  feature exists" and "nothing happened", or the offline promise breaks by a second route.

**The cost of the silent branch, stated rather than left to be found.** Silence on "could not check"
means an attacker who can block `api.github.com` keeps a user on an old, possibly vulnerable version
**indefinitely and invisibly**. That is a real consequence and it is accepted here, for two reasons.
It is not fixable by design — an attacker who can block the network can equally block the download,
so no update mechanism can be forced to work against one — and the failure is in the safe direction:
blocking an update is not the same as installing a bad one. The alternative, warning after N days
without a successful check, would fire on exactly the air-gapped machines decision 3 exists to leave
alone. If that trade is ever revisited, it belongs with the release query
([#248](https://github.com/bioedca/tether/issues/248)), which is what knows how long it has been.

**Do not pin the Sigstore trusted root.** Fetch it, per check. A pinned root goes stale — Sigstore
rotates Rekor log shards yearly, distributes the keys only via TUF, and explicitly tells clients not
to hardcode — and under refuse-and-report a stale root becomes a permanently dead updater that
refuses every genuine release. Fetching makes the failure mode "no update" instead of "wrong update".
The alternative is owning a trust-root refresh channel, which is a second updater.

### Tether owns every network step; `gh` runs fully offline

This is not an implementation preference. It is what makes the two paragraphs above *true*, and both
`P1`s on round 1 land here.

`gh attestation verify --bundle` **still reaches the network on its own** to fetch the Sigstore
trusted root; only `--bundle` **plus `--custom-trusted-root`** is genuinely offline, verified by
running it with all egress blackholed. Leaving that fetch inside `gh` breaks two things at once: the
egress boundary below becomes false, and a `gh` failure becomes ambiguous between "signature is bad"
and "could not reach Sigstore" — which is exactly the distinction the failure taxonomy depends on and
which `gh` cannot express, since every outcome is exit 1.

So the sequence is fixed:

1. Tether fetches the release list, the attestation bundle, **and** the trusted root
   (`gh attestation trusted-root`, which works unauthenticated — verified, exit 0, 34,634 bytes).
2. Tether invokes the verifier, which now makes **no network call at all**:

   ```
   gh attestation verify <installer> \
     --bundle <bundle> --custom-trusted-root <root> \
     --repo bioedca/tether \
     --signer-workflow bioedca/tether/.github/workflows/release.yml
   ```

**The identity flags are not optional and are not decoration.** An earlier revision of this section
showed the invocation with `--bundle --custom-trusted-root` alone, which was a regression introduced
by the offline fix itself: without `--repo` and `--signer-workflow`, an interceptor who substitutes
**both** the artifact and a valid Sigstore bundle from another repository or workflow passes
verification. The digest and filename checks do not save it, because the substituted artifact matches
the substituted bundle. Identity is the control that makes those checks mean anything.

They also cost nothing here: `--repo` triggers `gh`'s authentication gate only on the path where `gh`
fetches the bundle itself. With `--bundle` supplied, `--repo` and `--signer-workflow` run
**unauthenticated** — verified, exit 1 (reached verification) rather than exit 4 (auth required).

That `--custom-trusted-root` genuinely removes the network dependency is likewise measured, not
assumed. With all egress blackholed: **without** it, `gh` fails at `error creating Sigstore verifier:
no valid Sigstore verifiers could be initialized` — it never reaches verification. **With** it, `gh`
reaches verification and fails on the artifact instead. That difference is the whole basis of the
taxonomy below.

That buys both properties. **Egress is exactly three enumerable endpoints**, so it can be disclosed
truthfully and allow-listed by a site administrator. And **any non-zero exit from step 2 is a
verification failure**, because there is no network left in it to fail — which makes the taxonomy
implementable rather than aspirational.

**The conservative default for anything still ambiguous:** a failure in step 1 is *could not run* →
silent. A failure in step 2 is *ran and failed* → reported. Anything that cannot be classified at all
is treated as **ran and failed** — refuse and report. Refusing loudly when we are unsure is the safe
error; staying silent when verification actually failed is not.

**Egress boundary — the complete list.** Three hosts, no others:

| Endpoint | Purpose | Auth |
| --- | --- | --- |
| `api.github.com` — releases | what is newest | none |
| `api.github.com` — attestations | the bundle | none |
| Sigstore TUF (`tuf-repo-cdn.sigstore.dev`, via `gh attestation trusted-root`) | the trusted root | none |

A site that allow-lists only the GitHub endpoints **silently disables verification** — correctly, by
the rule above, but silently. That is a real operational trap, so the administrator documentation
must list all three, not two.

**Rate limit.** The unauthenticated attestations API allows **60 requests/hour per IP**. A lab behind
one NAT can exhaust it, which lands in the silent-no-op branch above.

### Considered and rejected

- **Checksum only.** Rejected in decision 2: the manifest and the asset share an origin, so it proves
  nothing an attacker who controls that origin cannot forge.
- **Shell out to a `gh` the user might have.** Rejected in the issue. Silent unavailability on most
  lab machines would turn refuse-and-report into never-updates, or tempt a warn-and-continue path.
  Note this is *not* what was chosen: Tether **ships** a pinned `gh`, so availability is not in
  question.
- **`sigstore-python`, in-process — the originally specified mechanism.** Rejected on three grounds,
  and the lock cost is the least of them.
  1. **Seven pip-only entries, not one.** `sigstore`, `sigstore-models`, `sigstore-rekor-types`,
     `rfc8785`, `rfc3161-client` and `tuf` have **no conda-forge package at all** — measured by
     installing 4.5.0 and checking all 31 resulting distributions against anaconda.org.
     `rfc3161-client` is a Rust extension, so that is a per-platform compiled wheel inside an
     installer that today contains only conda artifacts.
  2. **The security-critical step is not public API.** `Verifier.verify_dsse` explicitly refuses to
     bind the payload to the artifact, and the binding helper is underscore-private behind a
     CLI-private path. For a design whose entire value is that binding, depending on a private method
     across a library that broke its API at 4.0.0 is the wrong risk profile.
  3. **Its offline trusted root is read from a user-writable path without signature verification**
     ("Using unverified trusted root from cache"). An attacker with ordinary user write access could
     substitute a trust root and have a forged attestation accepted while the app reports success.
     The safe call exists; the obvious one is the unsafe one.
- **`cosign`.** Verified working and fails closed, but see §"Why `gh` rather than `cosign`": it
  ignores the predicate type, does not bind the subject filename, and makes Tether author the Fulcio
  identity policy.
- **A conda-forge verifier in the base lock.** Superseded by `extra_files`, which achieves the same
  "we ship it, pinned, no user prerequisite" property without re-solving the lock and without
  inheriting a stale feedstock.
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

Ordered by dependency: **five filed implementation issues, linked by number, plus one documentation
deliverable that lands inside #248 rather than as its own issue.**

1. **[#330](https://github.com/bioedca/tether/issues/330) — application settings store** outside the
   install prefix, with a value that survives an update and a machine-wide administrator override.
   Prerequisite for everything else, and absent from the tree entirely today.
2. **[#332](https://github.com/bioedca/tether/issues/332) — bundle a pinned, hashed `gh` via
   `extra_files`**, at `>= 2.67.0`. Not a re-lock: a PR that touches `conda-lock.yml` has used the
   wrong mechanism.
3. **[#248](https://github.com/bioedca/tether/issues/248) — release query + channel policy**, the
   stable-only lookup gated on consent, with the air-gapped path proven to produce no error, no
   dialog and no startup delay. Note that #248 **predates this record** (filed 38 minutes before the
   decision comment) and is scoped to check-and-notify. That is not a contradiction of decision 1 —
   it says so itself, *"implementable against any outcome of that design"* — but it is the check
   half only, and it is now blocked on #330 for the consent flag it assumes.
4. **[#333](https://github.com/bioedca/tether/issues/333) — download + attestation verification**,
   the security-critical unit. It carries the four app-side requirements above and is specified to
   land with adversarial tests — tampered artifact, wrong-release bundle, subject-filename mismatch,
   `cp1252`-decoded bundle — rather than a happy-path one.
5. **[#331](https://github.com/bioedca/tether/issues/331) — per-OS hand-off**, including the
   launch-surface survival check from ADR-0051, the interrupted-update behaviour, and closing the
   verify-to-execute window.
6. **The `docs/privacy.md` amendment**, which by decision 5 lands *inside* #248 rather than as its
   own PR — listed here so it is not forgotten, not so it is separated.

An earlier draft of this section claimed all six were filed when none were. The numbers are here now
because a decision record that overstates its own follow-through is exactly the kind of claim a
future reader has no way to check.

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
