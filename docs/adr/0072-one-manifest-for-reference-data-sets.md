<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0072 — One manifest and four storage tiers for reference data sets

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** bioedca (tiers and limits chosen in the interview of 2026-11-11, Q23 A and R9 A,
  recorded on issue #696)
- **PRD anchor:** §8 (NFR-FIXTURES, NFR-VALID), §12.1 (SPDX / REUSE), §12.6 (CI)
- **Milestone:** M19

## Context and problem statement

The regression plan of [#695](https://github.com/bioedca/tether/issues/695) compares Tether with
other tools on public data sets from 12 MB to 49 GB. Before this record, each fixture had its own
rule: two LFS files under `tests/fixtures/large/`, one REUSE override for kinSoft, and rows in
`tests/fixtures/PROVENANCE.md`. There was no rule for a set that is too large for the repository,
and no code that downloads a set and verifies it. Each new reference check would have added its
own mechanism.

Where does each reference file live, how large can it be in each place, and how does a test get a
file whose bytes are known to be correct, without a network call in the required matrix?

## Decision drivers

- **The required matrix makes no network call.** An upstream outage or a slow mirror must never
  make a pull request red.
- **A wrong file is never used.** Every file has a SHA-256 in one place, and a mismatch stops the
  test with both checksums. A test that runs on wrong bytes gives a wrong pass or a wrong failure.
- **The repository stays small.** Git LFS on this repository has a small free budget, and every
  clone pays for plain-git files.
- **One mechanism.** A later issue adds a row, not code.
- **Licence first.** Only data that can be downloaded now, with no request to a person, and with a
  licence that permits the copy or the download (Q21).

## Considered options

1. **Keep one rule for each fixture.** Rejected: the problem statement.
2. **Commit every reference set to LFS.** Rejected: the sets of #695 are far larger than the LFS
   budget, and an LFS pull on every CI run costs bandwidth for files that most jobs do not read.
3. **Download every set in the required matrix, with a cache.** Rejected: a cache miss or an
   outage then makes a pull request red, and three operating systems triple the traffic.
4. **One manifest, four tiers, downloads only in a weekly job (chosen).**

## Decision outcome

Chosen option: **option 4**.

### The manifest

`tests/reference_data/manifest.toml` has one `[[dataset]]` entry for each file: `id`, `source`
(`doi:…` or `accession:…`), `record_url`, `license` (an SPDX id with a file in `LICENSES/`),
`file`, `size`, `sha256`, `tier` and `citation`. A `download` entry also has `download_url`; a
`workstation` entry has `env`. `tests/test_reference_data.py` holds every entry to these rules.

### The tiers and their limits

| Tier | Where the file is | Limit | When a test gets it |
| --- | --- | --- | --- |
| `git` | committed in plain git | 1 MB for each file | always |
| `lfs` | committed in Git LFS (`tests/fixtures/large/`, `benchmarks/large/`) | 50 MB for all LFS files together | when the LFS object is pulled; else skip |
| `download` | a public URL, downloaded to a cache | 200 MB for each file | when it is cached, or when downloads are on; else skip |
| `workstation` | a directory that an environment variable names | none | when the variable is set; else skip |

The limits are decimal megabytes (1 MB = 1,000,000 B). The LFS limit counts every file under the
LFS paths, also the files that are not in the manifest, because the budget is for the repository.
A git-tier file has `-text` in `.gitattributes`, so that a Windows checkout keeps its bytes and its
checksum.

### Large public sets

A public set that is larger than the CI limit has two kinds of entry (R9 A): a small committed
extract (one or two files, tier `git` or `lfs`) and the full set (tier `download` when it is at
most 200 MB, else `workstation`). The extract names its sources in `extract_of`, and either the
`extract_script` that makes it or the `extract_member` that it copies without change from one
archive. The weekly job compares each `extract_member` extract with that member of the downloaded
archive.

### The fetch rule and the cache

`tests/reference_data/fetch.py` gives `path_for(id)`:

- It calculates the SHA-256 before the first use of a file in a process. A mismatch raises
  `ChecksumMismatch`, with the set id and the two checksums. The file is not used.
- A download goes to `<file>.part` and gets its final name only after the checksum is correct. A
  download that is larger than the manifest size stops at once. A failed download leaves no file.
- A download occurs only when `TETHER_REFERENCE_DOWNLOAD=1`. The cache directory is
  `$TETHER_REFERENCE_CACHE`, default `~/.cache/tether-reference-data`.

The marker `reference_download` identifies a test that needs the network. The required matrix
deselects it. `.github/workflows/reference-data.yml` runs it every week and on manual dispatch,
with `TETHER_REFERENCE_DOWNLOAD=1` and an `actions/cache` whose key is a digest of the ids and
checksums of the `download` entries (`fetch.py cache-key`). A change of a citation keeps the cache;
a new or changed file makes a new cache. A cached file is verified again on each run. A failed
scheduled run opens or updates one issue through `scheduled-failure.yml` (#697).
`tests/test_marker_contract.py` fails when a `reference_download` test is collected by no
workflow, or when the required matrix collects one.

### Third-party files

A committed third-party file needs a manifest entry, a REUSE annotation with the same licence, a
`NOTICE` entry and a `tests/fixtures/PROVENANCE.md` entry. The manifest test finds third-party
files from `REUSE.toml` (an annotation whose copyright holder is not The Tether Authors) and from
`.license` files, and fails when one has no entry.

### Consequences

- Good: a new reference check adds a row and a test, and the rules check the row.
- Good: no test in the required matrix depends on a network or on an upstream record.
- Good: the first users are the kinSoftChallenge fixtures, which now have entries, and the three
  source archives they came from, which the weekly job downloads and verifies.
- Bad: a `download` set is checked only once a week, so an upstream change can stay unseen for up
  to seven days.
- Bad: a `workstation` set runs only where a person has put the data; CI never sees it.
- Neutral: the maintainer's private laboratory data is not in this manifest (#698).

## References

- Issue [#696](https://github.com/bioedca/tether/issues/696) and the plan in
  [#695](https://github.com/bioedca/tether/issues/695).
- kinSoftChallenge data: Götz M & Schmid S, Zenodo
  [doi:10.5281/zenodo.5701310](https://doi.org/10.5281/zenodo.5701310) (CC-BY-4.0). The three
  `sim_dataset_Fig{2,3,4}.zip` checksums in the manifest were downloaded and verified again on
  2026-11-11; they are equal to the values in `tests/fixtures/PROVENANCE.md` (accessed 2026-07-13).
