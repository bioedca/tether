---
description: "For maintainers: build the Tether desktop installers from source and smoke-test them on each operating system."
---

# Building the installers

**Who this page is for.** Tether maintainers with write access to `bioedca/tether`, who build
and smoke-test the desktop installers; users do not need this page. To download, verify and
install a published installer, follow [Install Tether](../install.md); for what an installer
contains, see [Installers & offline bundle](../packaging.md).

## Building from source

Maintainers build the installers with the recipe under `packaging/` — see the
`packaging/README.md` in the source tree for the full build contract. In continuous
integration the build runs on all three operating systems in an advisory,
manually-triggered workflow, which also **install-smokes** each installer: it installs
into a clean prefix with networking neutralised, launches `tether --version` through the
same shims a user has on `PATH`, and drives the sidecar's real headless entry point
offline — constructing the tMAVEN driver, not merely importing the package, since that
is where an environment problem actually surfaces. The release pipeline (`release.yml`)
runs the same checked-in script — `packaging/scripts/install_smoke.sh` — on each
installer it is about to publish, so a release build that cannot install or launch never
reaches a GitHub Release. [Releasing](../release.md) covers that pipeline end to end.

## Installer images and texts

The Windows and macOS installers show the Tether images in `packaging/images/`.
`packaging/construct.yaml` names them, and the brand scripts in `scripts/brand/` make them (see
[Brand](../brand.md)). Each file has the size that `constructor` uses, so `constructor` does not
change the image.

| File | Size (pixels) | Installer | Where it shows |
|---|---|---|---|
| `windows-welcome-164x314.png` | 164 × 314 | Windows | The welcome page and the finish page |
| `windows-header-150x57.png` | 150 × 57 | Windows | The header of the other pages |
| `windows-icon-256.png` | 256 × 256 | Windows | The installer file and the installer window |
| `macos-welcome-1227x600.png` | 1227 × 600 | macOS | The background of all pages, in the light and the dark appearance |

The macOS installer also shows three texts that `construct.yaml` sets: `welcome_text`,
`readme_text` and `conclusion_text`. If `readme_text` or `conclusion_text` is missing,
`constructor` shows its own text about Anaconda. Keep each text on one line, in double quotes,
because the install-smoke reads the texts from `construct.yaml`. A change to the meaning of a
text needs the maintainer's approval. The Linux installer shows no image and none of these
texts.

The install-smoke examines each Windows and macOS installer before it installs it. On macOS it
expands the package with `pkgutil --expand`. On Windows it unpacks the page images with 7-Zip.
Then `packaging/scripts/check_installer_branding.py` compares the background, the page images,
the icon and the texts with the files above. It also makes sure that the words "Anaconda" and
"conda" are not in the text that the macOS installer shows. If one of these checks fails, the
installer build fails.
