# Packaging &amp; installers

Tether ships as a **self-contained desktop installer** for Windows, macOS and Linux,
built with [`constructor`](https://conda.github.io/constructor/). One installer bundles
everything the app needs and resolves **fully offline** — no git or network access is
required at install time.

## What a Tether installer contains

- The **base environment** — the PySide6 shell, the embedded napari movie panel and the
  pyqtgraph trace docks, plus the compute/IO stack — pinned to Tether's committed lock so
  every install is byte-for-byte reproducible.
- An **isolated tMAVEN sidecar** environment (installed under `envs/sidecar`), used for
  one-click vbFRET / consensus VB-HMM / ebFRET idealization. It runs in its own
  interpreter (PyQt5 on `numpy<2`) so it never collides with the base GUI stack.
- Tether's **GPL-3.0 license** text, shown during installation and shipped beside the
  installer. The bundled sidecar also carries tMAVEN's own GPL-3.0 license.

The optional deep-learning GPU add-on is **not** part of the base installer; it is a
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

Released installers are code-signed — Authenticode on Windows, and `productsign` with
Apple notarization on macOS — as part of the tagged release pipeline. Development builds
produced by the advisory workflow are unsigned.
