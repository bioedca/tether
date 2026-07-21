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
into a clean prefix with networking neutralised, launches `tether --version`, and
confirms the bundled sidecar interpreter imports offline.

## Release signing

The tagged release pipeline has code-signing wired in — Authenticode on Windows, and
`productsign` with Apple notarization on macOS — but both legs are **gated on repository
variables** (`SIGNPATH_ORGANIZATION_ID`, `APPLE_SIGNING_ENABLED`) that are not yet set. Until
they are, **every installer ships unsigned**, release and advisory build alike.

The unsigned-build `::warning::` annotation, the `SHA256SUMS-<platform>.txt` checksums and the
build-provenance attestation are all emitted by the **release** workflow only. The advisory
`packaging.yml` run validates, builds, install-smokes and uploads `packaging/dist/*` and
nothing else — so a workflow-dispatch artifact carries no checksum file, no attestation and no
warning annotation. Verify those integrity anchors against a published release, not against an
advisory build.

The maintainer-side
enrollment steps are in [Releasing (signed installers)](release.md), and the OS warning a user
sees is covered in
[the installer is flagged as unsigned](troubleshooting.md#the-installer-is-flagged-as-unsigned).
