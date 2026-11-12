<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0083 — Unchanged copies of CC BY-NC-ND 4.0 reference data in a GPL-3.0 repository

- **Status:** accepted
- **Date:** 2026-11-12
- **Deciders:** bioedca (interview of 2026-11-11: note of batch 1, S2 A, recorded on issue #755)
- **PRD anchor:** §8 (NFR-FIXTURES, NFR-VALID), §12.1 (SPDX / REUSE)
- **Milestone:** M19

## Context and problem statement

The maintainer's study (Campos-Chavez et al., *Nat Commun* 15:6592, 2024,
doi:10.1038/s41467-024-50885-x) publishes its Source Data as one archive,
`41467_2024_50885_MOESM4_ESM.zip`. It holds measured smFRET trace matrices, the tMAVEN transition
count matrices with the expected outputs of the error scripts, and the numbers behind each figure.
These are published numbers on measured data, and the checks of #756, #757 and #758 compare Tether
with them.

The publisher gives the Source Data under **CC BY-NC-ND 4.0**. The repository is GPL-3.0-or-later,
and the only other third-party data in it (kinSoftChallenge) is CC-BY-4.0, which permits changes.
This licence does not: it permits copies of the material, in whole or in part, for non-commercial
purposes, and it does not permit a changed version to be shared.

Can a copy of these files be in this repository, and which rules must a copy keep?

## Decision drivers

- **The licence decides what is possible.** Under CC BY-NC-ND 4.0 §2(a)(1)(A) a licensee may
  "reproduce and Share the Licensed Material, in whole or in part, for NonCommercial purposes only",
  and under §2(a)(1)(B) may make Adapted Material but not Share it. A copy in a public repository is
  sharing, so only unchanged files can be in it.
- **The required matrix makes no network call** (ADR-0073). A check that must run on each pull
  request needs a committed copy.
- **The repository licence must not mislead.** A reader of `LICENSE` must not think that these
  files are under GPL-3.0, and a person who uses Tether commercially must be able to see which
  files are not for commercial use.

## Considered options

1. **Download only, commit nothing.** Rejected: the checks of #756 to #758 that need only the small
   text files would then run once a week and not on a pull request.
2. **Commit converted or cropped copies** (for example, one HDF5 file in Tether's format).
   Rejected: a converted copy is Adapted Material (§1(a)), which the licence does not permit to
   share.
3. **Keep the data on the reference workstation only.** Rejected: the data is public, and the
   decision of batch 1 permits the copy.
4. **Commit a small set of unchanged files, and download the full archive in the weekly job
   (chosen, S2 A).**

## Decision outcome

Chosen option: **option 4**.

### What is committed

- In plain git, below `tests/fixtures/campos_chavez_2024/`: the folder
  `ErrorCalculations_CodeAndSourceData/` of the archive and the file `checkTDP.py`, at the same
  paths below that directory as in the archive.
- In Git LFS, below `tests/fixtures/large/campos_chavez_2024/`: one trace matrix of less than
  10 MB (`smFRET_SourceData/intraFRET_WT+WT_13uM.txt`, 6,723,000 B, the smallest).

The archive is 94,911,677 B, so it is a `download` entry (below the CI limit of 200 MB), and the
checks on the full data can run in the weekly job.

Each file has one entry in `tests/reference_data/manifest.toml` with `extract_member` set to its
path in the archive, and the archive has one `download` entry with its size and SHA-256. The weekly
`reference-data` job downloads the archive, verifies its checksum, and compares each committed file
with its member, byte for byte (ADR-0073).

### The rules for these copies

1. **Unchanged only.** A committed file is byte-identical to its member of the archive. The
   repository keeps no converted, cropped, corrected or re-encoded copy. A test that needs another
   format converts the file in memory or in a temporary directory when it runs.
2. **No tool rewrites them.** The files have `-text` in `.gitattributes`, so a Windows checkout keeps
   their bytes. The pre-commit hooks that change files (whitespace, end of file, ruff) exclude the
   directory, so a hook cannot change a byte, and ruff does not lint the two published scripts.
3. **They are data, not Tether code.** No module of `src/tether` imports, includes or reads them.
   Only tests read them. The wheel holds `src/tether` only, so the installers and the packages do not
   carry them.
4. **Attribution travels with them** (§3(a)): `REUSE.toml` gives each file its holder and
   `CC-BY-NC-ND-4.0`; `LICENSES/CC-BY-NC-ND-4.0.txt` holds the licence text; `NOTICE`, the test-data
   section of `docs/validation.md` and `tests/fixtures/PROVENANCE.md` name the article, the authors,
   the licence, the URL of the archive, and say that the files are not under GPL-3.0, are not
   changed, and are for non-commercial use.
5. **A change of upstream is a failure, not an update.** When the publisher's archive changes,
   the weekly job fails with `ChecksumMismatch` and both checksums; when it is absent or moved, it
   fails with `DownloadFailed`, which names the set and the URL. A person then decides; no job
   replaces a committed copy.
6. **The manifest enforces it.** `fetch.check_manifest` refuses an `extract_script` on an entry
   whose licence is a NoDerivatives licence (`-ND-`): such an extract can only be an unchanged
   `extract_member`.

### Why this data can be in a GPL-3.0 repository

GPL-3.0 §5 calls a compilation of a covered work with "separate and independent works, which are not
by their nature extensions of the covered work" an *aggregate*, and says that the inclusion of a
covered work in an aggregate "does not cause this License to apply to the other parts of the
aggregate". The data files are separate works: Tether does not contain them, link them or derive
from them, and only tests open them as input. So each part keeps its own licence: Tether under
GPL-3.0-or-later, these files under CC BY-NC-ND 4.0. The same reasoning applies to the existing
CC-BY-4.0 kinSoftChallenge fixtures and to the OFL-1.1 font.

Hosting the repository on GitHub and archiving its releases on Zenodo are sharing for
non-commercial purposes. A person who distributes a copy of the repository for commercial purposes
must remove these files first; `NOTICE` says so.

### Consequences

- Good: the checks of #756 to #758 that need only the small files run on each pull request.
- Good: the weekly job shows a changed upstream archive, and the extract test shows a changed
  committed file.
- Bad: the repository now holds files with a non-commercial condition. A commercial redistributor
  of the repository must remove them; Tether itself (the wheel and the installers) is not affected.
- Bad: a file that does not parse (for example a published script) cannot be corrected here; a test
  uses its inputs and outputs and not the script.
- Neutral: the rules apply to any later reference data under a NoDerivatives licence.

## References

- Issue [#755](https://github.com/bioedca/tether/issues/755), the plan in
  [#695](https://github.com/bioedca/tether/issues/695), and ADR-0073 (the manifest and the tiers).
- Campos-Chavez E, Paul S, Zhou Z, Alonso D, Verma AR, Fei J, Mondragón A. Translational T-box
  riboswitches bind tRNA by modulating conformational flexibility. *Nature Communications* 15:6592
  (2024). [doi:10.1038/s41467-024-50885-x](https://doi.org/10.1038/s41467-024-50885-x)
  (PMID 39097611, PMC11297988; metadata read from PubMed on 2026-11-12). Source Data:
  `41467_2024_50885_MOESM4_ESM.zip`, CC BY-NC-ND 4.0.
- Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International, §1(a), §2(a)(1), §3(a)
  (`LICENSES/CC-BY-NC-ND-4.0.txt`).
- GNU General Public License v3.0, §5 (aggregates) (`LICENSES/GPL-3.0-or-later.txt`).
