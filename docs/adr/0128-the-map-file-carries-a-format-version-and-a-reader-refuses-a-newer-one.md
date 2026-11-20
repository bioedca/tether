<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0128 — The map file carries a format version, and a reader refuses a newer one

- **Status:** accepted
- **Date:** 2026-11-17
- **Deciders:** bioedca
- **PRD anchor:** §7.1 FR-EXTRACT (registration), Appendix E Stage 10 (map persistence)
- **Milestone:** M15
- **Issue:** [#353](https://github.com/bioedca/tether/issues/353); the full map asset is
  [#916](https://github.com/bioedca/tether/issues/916)

## Context and problem statement

ADR-0014 made the `.npz` map file (`save_map` /
`load_map`): explicit arrays plus JSON provenance, read with
`allow_pickle=False`. Until #353 nothing read that file back for a later movie. #353 lets a saved
map register later movies (`tether extract --map`, `tether map create`, the Calibration menu), so
a map file now outlives the session that wrote it. #916 will add fields to it: source ids, the
channel layout, the geometry recipe, the quality record, an optics text.

How does a map file say which format it is, and what does a reader do with a file written before
the field existed, or by a newer Tether?

## Decision drivers

- A file written today must still read after #916 adds fields.
- A file written before #353 (no version field) must still read: it holds the same arrays.
- A reader must never drop fields it does not know without saying so. A map whose quality record
  or layout is ignored would be applied as if it had none.
- `allow_pickle=False` stays (ADR-0014). No pickled object, version field included, enters a map.
- A bad file is refused with a message that names the reason.

## Considered options

1. **An integer `format_version` array; absent reads as 0; a newer version is refused.**
2. **No version field; a reader infers the format from the arrays it finds.**
3. **A `MAJOR.MINOR` version string; a reader accepts any newer minor version.**
4. **An integer version; a newer version reads with a warning.**

## Decision outcome

Chosen option: **1.**

- `save_map` writes `format_version`, a 0-d `int64` array
  equal to `MAP_FORMAT_VERSION`, which is `1`.
- A file with no `format_version` reads as version `0`. Version 0 and version 1 hold the same
  arrays.
- `load_map` refuses, with
  `MapFileError`, a version newer than its own
  `MAP_FORMAT_VERSION`, and a `format_version` that is not one non-negative integer.
- Every version holds the arrays version 0 holds. A reader refuses a file that lacks one and names
  the missing arrays.
- A change that adds arrays raises `MAP_FORMAT_VERSION` by one, and its reader still reads every
  older version. Removing or renaming an array is not part of this rule; it needs a new ADR.

Option 2 cannot tell an old file from a new file with an array missing. Each added field would add
a guess. Option 3 only works when a newer minor version is safe to read with fields dropped, and
the drivers say it is not: #916's fields change whether a map may be reused. Option 4 runs the
extraction anyway, so the warning comes after a map was applied without the fields that say
whether it should have been.

### Consequences

- Good: a map file written now reads under #916's reader, and #916 adds its fields as version 2
  with no change to a version-1 file.
- Good: an older Tether given a newer map refuses it and says why, rather than registering a movie
  with part of the map.
- Bad / trade-off: a user with an older Tether cannot use a map a newer Tether wrote, even when the
  new fields would not change the result. They update Tether, or make the map again.
- Follow-up: #916 adds its fields under this rule and records them, as an amendment to this ADR or
  a record that supersedes it. Tests: `tests/test_map_file.py` (`save_map` writes the version, an
  unversioned file reads as 0, a newer version is refused, a missing array is refused,
  `allow_pickle=False`).

## More information

- ADR-0014: the map file and its `allow_pickle=False` read.
- `docs/cli.md`, "Registration: one map source": which map an extraction uses.
