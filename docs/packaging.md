# Packaging &amp; installers

Tether ships as a **self-contained desktop installer** for Windows, macOS and Linux,
built with [`constructor`](https://conda.github.io/constructor/). One installer bundles
everything the app needs and resolves **fully offline** — no git or network access is
required at install time.

## What a Tether installer contains

- The **Tether application environment** (installed under `envs/tether`) — the PySide6 shell,
  the embedded napari movie panel and the pyqtgraph trace docks, plus the compute/IO stack —
  pinned to Tether's committed lock so every install is byte-for-byte reproducible.
- An **isolated tMAVEN sidecar** environment (installed under `envs/sidecar`), used for
  one-click vbFRET / consensus VB-HMM / ebFRET idealization. It runs in its own
  interpreter (PyQt5 on `numpy<2`) so it never collides with the application's GUI stack.
- A minimal **conda bootstrap** in the install root, which the installer uses to lay down the
  two pinned environments above — it holds no Tether code and never modifies your shell.
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

## Building from source

Maintainers build the installers with the recipe under `packaging/` — see the
`packaging/README.md` in the source tree for the full build contract. In continuous
integration the build runs on all three operating systems in an advisory,
manually-triggered workflow, which also **install-smokes** each installer: it installs
into a clean prefix with networking neutralised, launches `tether --version` through the
same shims a user has on `PATH`, and drives the sidecar's real headless entry point
offline — constructing the tMAVEN driver, not merely importing the package, since that
is where an environment problem actually surfaces.

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
