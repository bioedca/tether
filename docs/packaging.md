---
description: "What a Tether installer and the offline bundle contain, and how to check that a download is genuine."
---

# Packaging &amp; installers

**Who this page is for.** Anyone who wants to know what a Tether installer contains, or who
must check that a download is genuine. It assumes you can run a command in a terminal. It does
not assume you know conda.

> **Looking to install Tether?** To download, install and run Tether step by step, follow
> [Install Tether](install.md). Maintainers building the installers themselves want
> [Building the installers](maintainers/building-installers.md).

Tether ships as a **self-contained desktop installer** for Windows, macOS and Linux,
built with [`constructor`](https://conda.github.io/constructor/). One installer bundles
everything the app needs and resolves **fully offline**. The install needs no git and no
network access.

## What a Tether installer contains

- The **Tether application environment** (installed under `envs/tether`). It holds the
  PySide6 shell, the pyqtgraph trace docks and the compute/IO stack. It is pinned to the
  committed lock of Tether, so every install is byte-for-byte reproducible. It also holds
  [napari](https://napari.org) for the movie panel of the application.
- An **isolated tMAVEN sidecar** environment (installed under `envs/sidecar`). It runs every
  idealization fit:
    - the whole-project fit of **Idealize → Idealize project…** (the **I** key) in the
      application,
    - the whole-project consensus VB-HMM fit of [`tether batch`](cli.md#tether-batch),
    - the consensus VB-HMM and ebFRET fits that [run from Python](how-to/idealize.md).

    It runs in its own interpreter (PyQt5 on `numpy<2`), so it never collides with the GUI
    stack of the application.
- A minimal **conda bootstrap** in the install root. The installer uses it to lay down the two
  pinned environments above. It holds no Tether code and never modifies your shell.
- Two **launchers**, `tether` and `tether-gui` (`tether.bat` and `tether-gui.bat` on Windows),
  in a `launchers` folder in the install root. That folder holds nothing else, so it is the one
  folder you add to your `PATH`
  ([Install Tether, step 4](install.md#optional-put-the-launcher-folder-on-your-path)).
  Installers up to and including 1.0.0-rc3 put the launchers in the install root's `bin`
  folder instead. On Linux and macOS that folder also holds the bootstrap's own `python`, `pip`
  and `conda`
  ([ADR-0094](adr/0094-the-launchers-get-a-folder-of-their-own.md)).
- Tether's **GPL-3.0 license** text, shown during installation and shipped beside the
  installer. The bundled sidecar also carries tMAVEN's own GPL-3.0 license.
- A copy of the **GitHub CLI** (`gh`, MIT license) in the `verifier/gh` folder of the install
  root. It is there for the in-app updater, which uses it to verify a downloaded installer
  before it runs it ([ADR-0060](adr/0060-in-app-update-mechanism-and-integrity-model.md)). The
  folder also holds the `gh` license and the license notices of the software `gh` contains.
  Tether does not put this folder on your `PATH`, so it does not replace a `gh` that you
  installed yourself. Its version and checksums are pinned in `packaging/gh-verifier.toml`.

The optional deep-learning GPU add-on is **not** part of the installer. It is a separate,
documented install for users with a supported NVIDIA GPU.

## Installer format per platform

| Platform | Installer |
|---|---|
| Windows | `Tether-<version>-Windows-x86_64.exe` (NSIS) |
| macOS | `Tether-<version>-MacOSX-<arch>.pkg` |
| Linux | `Tether-<version>-Linux-x86_64.sh` |

## How to verify a download

**No Tether installer is OS-code-signed.** That is the settled position for 1.0, not a gap
that a later release closes. Expect Windows SmartScreen or macOS Gatekeeper to warn you when
you open one.
[ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)
records the decision;
[Releasing](release.md#os-code-signing-why-there-is-none) carries the maintainer-side detail.

Verify the file instead. Two anchors ship with every published release:

- **`SHA256SUMS-<platform>.txt`**. Check it with `sha256sum -c` on Linux, `shasum -a 256 -c`
  on macOS, or `Get-FileHash` on Windows.
- **A build-provenance attestation** over every installer, checked with:

```bash
gh attestation verify --repo bioedca/tether Tether-<version>-<platform>.<ext>
```

The two answer different questions. A code signature is a real cryptographic check on the
file. It binds the artifact to whoever holds the signing certificate, so tampering breaks it.
What it does not say is *which build* produced the file. The attestation binds the file to the
repository, the workflow and the tag. That answers the question "did Tether build this exact
file?", which is the question that you can check against this project.

Both come from the **release** workflow alone. The advisory `packaging.yml` run validates,
builds, install-smokes and uploads `packaging/dist/*` and nothing else, so a
workflow-dispatch artifact has no checksum file and no attestation. Verify against a
published release, never against an advisory build.

The OS warning itself, and how to get past it on each platform, is in
[the installer is flagged as unsigned](troubleshooting.md#the-installer-is-flagged-as-unsigned).

## How the in-app updater checks a download

In this release, the in-app update notice downloads nothing and installs nothing. It names the
newer release and links to the Releases page. You download, check and install the new release
yourself, as [Update Tether](install.md#update-tether) tells you.

The check that the updater will run on a downloaded installer is already part of Tether
(`tether.gui.update_verify`, [ADR-0060](adr/0060-in-app-update-mechanism-and-integrity-model.md)).
It runs only when you allowed the update check. It accepts a file only when all of these are true:

- The version is a stable `MAJOR.MINOR.PATCH` release, and the file name is this computer's
  installer for that version.
- GitHub holds a build-provenance attestation for the file's SHA-256 digest.
- The bundled `gh` verifies that attestation offline. The attestation must come from the release workflow of
  `bioedca/tether`, built for the tag of that same version, on a GitHub-hosted runner.

The updater refuses a file that fails any check, and no setting turns the checks off. Tether does
not yet show this check or its result to you, so there is no failure message to look for.
Installing from inside Tether is still to come
([#331](https://github.com/bioedca/tether/issues/331)), and its failure message comes with it.
