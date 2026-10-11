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
