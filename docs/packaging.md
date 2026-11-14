# Packaging &amp; installers

**Who this page is for.** Anyone who wants to know what a Tether installer contains, or who
must check that a download is genuine. It assumes you can run a command in a terminal. It does
not assume you know conda.

> **Looking to install Tether?** To download, install and run Tether step by step, follow
> [Install Tether](install.md). Maintainers building the installers themselves want
> [Building the installers](maintainers/building-installers.md).

Tether ships as a **self-contained desktop installer** for Windows, macOS and Linux,
built with [`constructor`](https://conda.github.io/constructor/). One installer bundles
everything the app needs and resolves **fully offline** — no git or network access is
required at install time.

## What a Tether installer contains

- The **Tether application environment** (installed under `envs/tether`) — the PySide6 shell,
  the pyqtgraph trace docks and the compute/IO stack — pinned to Tether's committed lock so
  every install is byte-for-byte reproducible. It also holds [napari](https://napari.org) for
  the movie panel, which reaches the application with the
  [trace curation section (M16)](https://github.com/bioedca/tether/milestone/18).
- An **isolated tMAVEN sidecar** environment (installed under `envs/sidecar`), which runs
  every idealization fit: the application's one-trace fit on the **I** key, the
  whole-project consensus VB-HMM fit of [`tether batch`](cli.md#tether-batch), and the
  consensus VB-HMM and ebFRET fits that [run from Python](how-to/idealize.md). It runs in its
  own interpreter (PyQt5 on `numpy<2`) so it never collides with the application's GUI stack.
- A minimal **conda bootstrap** in the install root, which the installer uses to lay down the
  two pinned environments above — it holds no Tether code and never modifies your shell.
- Two **launchers**, `tether` and `tether-gui` (`tether.bat` and `tether-gui.bat` on Windows),
  in a `launchers` folder in the install root. That folder holds nothing else, so it is the one
  folder you add to your `PATH`
  ([Install Tether, step 4](install.md#4-put-tethers-launcher-folder-on-your-path)).
  Installers up to and including 1.0.0-rc3 put the launchers in the install root's `bin`
  folder instead. On Linux and macOS that folder also holds the bootstrap's own `python`, `pip`
  and `conda`
  ([ADR-0096](adr/0096-the-launchers-get-a-folder-of-their-own.md)).
- Tether's **GPL-3.0 license** text, shown during installation and shipped beside the
  installer. The bundled sidecar also carries tMAVEN's own GPL-3.0 license.

The optional deep-learning GPU add-on is **not** part of the installer; it is a
separate, documented install for users with a supported NVIDIA GPU.

## Installer format per platform

| Platform | Installer |
|---|---|
| Windows | `Tether-<version>-Windows-x86_64.exe` (NSIS) |
| macOS | `Tether-<version>-MacOSX-<arch>.pkg` |
| Linux | `Tether-<version>-Linux-x86_64.sh` |

## How to verify a download

**No Tether installer is OS-code-signed**, and that is the settled position for 1.0 rather
than a gap waiting to close. Expect Windows SmartScreen or macOS Gatekeeper to warn you when
you open one.
[ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)
records the decision;
[Releasing](release.md#os-code-signing-why-there-is-none) carries the maintainer-side detail.

Verify the file instead. Two anchors ship with every published release:

- **`SHA256SUMS-<platform>.txt`** — check with `sha256sum -c` on Linux, `shasum -a 256 -c` on
  macOS, or `Get-FileHash` on Windows.
- **A build-provenance attestation** over every installer, checked with:

```bash
gh attestation verify --repo bioedca/tether Tether-<version>-<platform>.<ext>
```

The two answer different questions. A code signature is a real cryptographic check on the
file: it binds the artifact to whoever holds the signing certificate, so tampering breaks it.
What it does not say is *which build* produced the file. The attestation binds it to the
repository, workflow and tag — which is what "did Tether build this exact file?" asks, and is
the question you can actually check against this project.

Both come from the **release** workflow alone. The advisory `packaging.yml` run validates,
builds, install-smokes and uploads `packaging/dist/*` and nothing else, so a
workflow-dispatch artifact has no checksum file and no attestation. Verify against a
published release, never against an advisory build.

The OS warning itself, and how to get past it per platform, is in
[the installer is flagged as unsigned](troubleshooting.md#the-installer-is-flagged-as-unsigned).
