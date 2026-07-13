# Tether installer recipe (`constructor`)

This directory builds Tether's self-contained, **offline** desktop installers for
Windows, macOS and Linux with [`constructor`](https://conda.github.io/constructor/).
Architecture and rationale: **[ADR-0049](../docs/adr/0049-m9-packaging-constructor-architecture.md)**.

## What the installer bundles

- The **Tether application env** (PySide6/napari GUI + compute) from the committed
  `conda-lock.yml`, rendered to a per-platform **explicit** lock (pin-and-hold; no
  re-solve), as a constructor `extra_envs` at `<prefix>/envs/tether`. `tether` itself is
  offline-installed from a bundled wheel.
- The **isolated tMAVEN sidecar** (`sidecar/conda-lock.yml` — PyQt5 / `numpy<2`) as a
  constructor `extra_envs` at `<prefix>/envs/sidecar`, with the pinned tMAVEN
  (commit `10f4230…`, see `NOTICE`) offline-installed from a bundled wheel.
- A thin **`python` + `conda` bootstrap** as the constructor `base` — required so the
  installer's own conda can lay down the two pinned `extra_envs` offline (constructor
  refuses `extra_envs` without `conda` in `base`). It is solved fresh at build time, holds
  no Tether code, and (`initialize_conda: false`) never touches the user's shell. See
  **[ADR-0049 → "base-env restructure"](../docs/adr/0049-m9-packaging-constructor-architecture.md)**.
- Tether's **GPL-3.0** license text (shown during install and shipped beside the
  installer).

The optional `deep/` GPU stack (ADR-0047) is **not** bundled — it stays a separate,
documented install.

## Files

| File | Role |
|---|---|
| `construct.yaml` | The recipe. Uses conda-build selectors + Jinja, so it is **not** plain YAML — render before use, and it is excluded from the `check-yaml` pre-commit hook. |
| `scripts/post_install.sh` / `.bat` | Offline `pip --no-index --no-deps` of the two wheels into their envs; wire `TETHER_SIDECAR_PYTHON`. |
| `locks/`, `staging/` | **Build-time only** (git-ignored): rendered explicit locks; the staged wheels + `LICENSE.txt`. |
| `dist/` | Built installers (git-ignored). |

## Building

CI does this on all three OSes in the advisory, non-required
[`packaging.yml`](../.github/workflows/packaging.yml) workflow
(`workflow_dispatch`). To build locally you reproduce the same contract:

```bash
# 1. A build env with constructor + conda-lock + build tools. `pip` is needed by
#    `python -m build` and `python -m pip wheel` below.
micromamba create -n pkgbuild -c conda-forge "constructor>=3.16" conda-lock=4.0.1 \
    conda-standalone python-build pip "setuptools<81" wheel
micromamba activate pkgbuild

# 2. Build the two wheels into packaging/staging/.
python -m build --wheel --outdir packaging/staging .
python -m pip wheel --no-deps --no-build-isolation -w packaging/staging \
    "git+https://github.com/GonzalezBiophysicsLab/tmaven.git@10f4230b6d13c6d2ad67b05d801696b4a40eff4a"

# 3. Render the committed locks to per-platform explicit locks (pin-and-hold).
mkdir -p packaging/locks && cd packaging/locks
conda-lock render -k explicit -p "$PLATFORM" ../../conda-lock.yml && mv "conda-$PLATFORM.lock" "base-$PLATFORM.lock"
conda-lock render -k explicit -p "$PLATFORM" ../../sidecar/conda-lock.yml && mv "conda-$PLATFORM.lock" "sidecar-$PLATFORM.lock"
cd ../..

# 4. Stage the license and export the wheel names the recipe reads.
cp LICENSE packaging/staging/LICENSE.txt
export TETHER_VERSION=... TETHER_WHEEL=staging/tether-*.whl TETHER_WHEEL_NAME=... \
       TMAVEN_WHEEL=staging/tmaven-*.whl TMAVEN_WHEEL_NAME=...

# 5. Validate the recipe, then build.
constructor --render packaging/     # offline: selectors + Jinja + YAML
constructor --conda-exe "$(command -v conda-standalone)" --output-dir packaging/dist packaging/
```

`$PLATFORM` is one of `linux-64`, `osx-64`, `osx-arm64`, `win-64` — build on (or for)
the target OS.

## Signing (deferred to PR-2)

Installers are **unsigned** here. Authenticode (Windows) and `productsign` +
notarization (macOS) are wired with repository secrets in the release pipeline
(`release.yml`, PRD §12.7): the `windows_signing_tool` / `signing_identity_name` /
`notarization_identity_name` keys are documented but inactive in `construct.yaml`.
