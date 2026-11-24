<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0143 — cellSens `.vsi` movies are read by Bio-Formats in an isolated fourth stack

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (the acceptance criteria of #907, approved 2026-11-24; cards D1 option B
  and D2 option B, 2026-11-14)
- **PRD anchor:** §7.1 FR-EXTRACT, Appendix A (input formats), §4.1 (technology stack), §9.1
  (isolated dependency locks)
- **Milestone:** M20

## Context and problem statement

The movies of the maintainer's laboratory are Olympus/Evident cellSens `.vsi` files. No
maintained Python reader for `.vsi` is known, so today each movie is exported to TIFF by the
vendor software before Tether reads it, and #887 shows that this export route can rotate the
frames against the direct data. Bio-Formats reads `.vsi`, but it is a Java library: in the base
environment it would add a Java runtime to every installer and change the base lock for every
user. ADR-0113 (rule 3) already keeps any JVM out of the base process. **Where does Bio-Formats
run, and how does the base application reach it?**

## Decision drivers

- The base lock and the base installer do not change (ADR-0004, ADR-0113 rule 3).
- The isolated stacks stay isolated: the tMAVEN sidecar (ADR-0004) and the deep stack (ADR-0047)
  are the model.
- A `.vsi` movie must satisfy the same reader interface and conformance tests as every other
  container (#901, ADR-0113).
- The frames must be the acquired values: a lossy codec is refused, as the TIFF adapter refuses
  one.
- The licences of Bio-Formats and of the Java runtime must permit the route.

## Considered options

- **A. A fourth isolated conda stack and a runner process.** `bioformats/environment.yml` and
  `bioformats/conda-lock.yml` hold Python, `openjdk`, `jpype1` and bioconda's `bftools` (the
  Bio-Formats command line tools, which ship `bioformats_package.jar`). The base side starts
  `src/tether/io/_bioformats_runner.py` with that stack's Python and talks to it over a pipe.
- **B. Bio-Formats in the base lock** (`openjdk` + `jpype1` or `scyjava`). Simplest to call, but
  it puts a JVM in every install and in the base process: ADR-0113 rule 3 forbids it, and the
  maintainer's card D1 chose B (the isolated stack) over it.
- **C. `bfconvert` to an OME-TIFF on disk, then the TIFF adapter.** No long-lived process, but a
  full copy of every movie on each open and a second export route — the thing #887 is about.
- **D. A Python `.vsi` reader.** None maintained is known; writing one is out of scope.

## Decision outcome

Chosen option: **"A"**, because it keeps the base environment exactly as it is and puts Java
behind the same kind of process boundary as the sidecar.

1. **The stack.** `bioformats/environment.yml` pins `python=3.12.*`, `openjdk>=17,<22`,
   `jpype1>=1.5,<2` and `bftools>=8.0,<8.1` from `conda-forge` then `bioconda` (bftools is
   packaged only by bioconda, which builds against conda-forge). The committed four-platform lock
   (linux-64, osx-64, osx-arm64, win-64) resolves Bio-Formats 8.0.0, OpenJDK 21.0.13 and JPype
   1.7.1. `tether` itself is not installed there: the runner needs only the standard library,
   JPype and the jar.
2. **The process boundary.** `tether.io.bioformats` starts the runner as
   `<stack python> -I _bioformats_runner.py` and sends one JSON request a line (`hello`, `open`,
   `read`, `close`); a `read` reply is a JSON line followed by the raw frame bytes. Before the JVM
   starts, the runner moves its standard output to standard error and keeps a private copy for
   the protocol, so nothing Java or Bio-Formats prints can corrupt a reply. A refusal is a typed
   reply (`unsupported`, `lossy`, `invalid_pixel`, `index`, `not_found`, `unavailable`, `error`)
   that the base side maps to the exceptions of ADR-0113. **The base process imports no Java
   binding** — a test runs it and lists `sys.modules`.
3. **The adapter.** `BIOFORMATS_ADAPTER` ("cellSens VSI", `.vsi`) is a built-in of the registry,
   after TIFF. A `.vsi` file begins with a TIFF header, so by content alone it is a TIFF; the
   registry's rule — the content decides, the extension breaks a tie — gives it to this adapter
   by its extension. A renamed `.vsi` goes to the TIFF adapter (its frames live in the `.ets`
   files of the `_<name>_` folder, which the name finds), and the conformance case says so with
   `by_content=False`. Of the series in the file, the runner reads the one with the most time
   points at full resolution, and refuses a tie between two movies, a series with more than one
   channel or focal plane, and color or palette pixels.
4. **Lossy codecs.** Bio-Formats decodes cellSens tiles of JPEG (2), JPEG 2000 (3), lossless JPEG
   (5), PNG (8), BMP (9) and raw (0) data, and does not put the codec in its metadata
   (`CellSensReader`, v8.0.0). The runner reads the reader's own codec list and refuses JPEG and
   JPEG 2000 (whose stream does not say whether it is lossless) and any unknown number; a list it
   cannot read is a refusal too.
5. **Not installed.** Without `$TETHER_BIOFORMATS_PYTHON`, or with a path that is not a file, a
   jar that is missing or a JVM that does not start, opening a `.vsi` raises
   `BioformatsUnavailableError` (an `UnsupportedContainerError`) whose message gives the install
   command. The stack is not in the base installer; `docs/install.md` gives the command.
6. **CI.** The required `conda-lock-verify` context checks `bioformats/conda-lock.yml` against
   its `environment.yml` on every pull request, exactly as it checks the sidecar and deep locks.
   The live suite (`tests/test_*_bioformats.py`, marker `bioformats`) runs in the **non-required,
   path-filtered** `bioformats / live` job (`.github/workflows/bioformats.yml`), nightly and on a
   pull request that changes its inputs, as `deep / smoke` does; the base matrix deselects the
   marker. The job sets `TETHER_REQUIRE_BIOFORMATS=1`, so a lost interpreter fails rather than
   skips, and its skip budget is zero.
7. **Test data (card D2).** No redistributable `.vsi` movie is known. The live job reads
   OME-TIFF movies that tifffile writes, through Bio-Formats' own reader, and must get every
   frame back exactly. The base matrix runs the #901 conformance tests on the cellSens adapter
   through a Java-free stand-in runner. A real laboratory `.vsi` movie is held to the same rules
   on the maintainer's workstation (`tests/test_vsi_direct_read.py`, ADR-0071): that check also
   compares the direct frames with the vendor's TIFF export under the eight rotations and mirror
   images of a frame and records which one matches, the number the rotation finding of #887
   needs.

### Licence review

An engineering review of what the route combines, from the packages the lock resolves (not legal
advice):

| Component | Licence | Source of the statement |
| --- | --- | --- |
| Bio-Formats 8.0.0 (`formats-gpl`, which holds `CellSensReader`) | GPL-2.0-or-later | `LICENSE.txt` and the header of `components/formats-gpl/src/loci/formats/in/CellSensReader.java` at tag `v8.0.0` of `ome/bioformats` |
| `bioformats_package.jar` (bundled third-party code) | the bundled libraries' own licences (Apache-2.0 and others), each under `META-INF/third-party-licenses/` and `license/` in the jar | the jar of `bftools-8.0.0-hdfd78af_0` |
| OpenJDK 21.0.13 (`openjdk`, conda-forge) | GPL-2.0-or-later WITH Classpath-exception-2.0 | the package record `conda-meta/openjdk-21.0.13.1-*.json` |
| JPype 1.7.1 (`jpype1`, conda-forge) | Apache-2.0 | the package record `conda-meta/jpype1-1.7.1-*.json` |
| Tether, including the runner | GPL-3.0-or-later | `LICENSE` |

Tether is GPL-3.0-or-later, and "version 2 or (at your option) any later version" lets
Bio-Formats be used under version 3; Apache-2.0 is compatible with GPL-3.0; the Classpath
exception means that running code on the JVM puts no licence on that code. So the combination is
permitted even within the runner process, and the process boundary is still the right place for
it for the reason ADR-0113 gives: it keeps a JVM, and every licence above but Tether's own, out of
the base process and out of the base installer. Tether does not redistribute the stack: a user
installs it from conda-forge and bioconda with the committed lock.

### Consequences

- Good: `.vsi` movies are read directly, so the export route of #887 is no longer needed; the
  base lock, the base installer and the base process do not change.
- Good: the runner reads any container Bio-Formats reads (`open_bioformats_source`), so a later
  adapter for another container is a registry entry and a conformance case.
- Bad / trade-off: a fourth lock to re-lock and verify, a second channel (`bioconda`) in one lock,
  and a JVM start (about a second) on each open.
- Bad / trade-off: the codec check reads a private field of `CellSensReader`; a Bio-Formats
  version that renames it makes every `.vsi` a refusal (fail closed) until the runner follows.
  The lock holds the version, and the workstation check would show it.
- Follow-up: the `.vsi` conformance and the #887 comparison need the laboratory files mapped as
  `vsi-NN` (roles `vsi` and `tiff_export`) on the workstation and the stack installed there.

## More information

- `src/tether/io/bioformats.py`, `src/tether/io/_bioformats_runner.py`,
  `bioformats/environment.yml`, `.github/workflows/bioformats.yml`.
- `tests/test_bioformats_adapter.py` (base matrix), `tests/test_movie_adapter_conformance.py`
  (the `vsi-fake-runner` case), `tests/test_runner_bioformats.py` (live tier),
  `tests/test_vsi_direct_read.py` (workstation).
- ADR-0004 (pin-and-hold isolation), ADR-0047 (the deep stack), ADR-0071 (the workstation
  runner), ADR-0113 (the reader interface and its rule 3), #887, #901.
- Retrieved 2026-11-24: bioconda `bftools-8.0.0-hdfd78af_0.tar.bz2`
  (sha256 `a774743df5b3127c482b446d388a1ad689d5318d9df11c1fd177e1144067e2ca`); GitHub
  `ome/bioformats` tag `v8.0.0` (`LICENSE.txt`, `CellSensReader.java`).
