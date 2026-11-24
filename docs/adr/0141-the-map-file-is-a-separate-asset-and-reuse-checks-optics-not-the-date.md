<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0141 — The map file is a separate asset, and its reuse checks the optics, not the date

- **Status:** accepted
- **Date:** 2026-11-24
- **Deciders:** bioedca (issue #916 approved 2026-11-14, grooming card "Approve all recommended", Q3, and again 2026-11-24; card A1 option B puts it in v1.0.0)
- **PRD anchor:** §7.1 (FR-EXTRACT, registration), Appendix E stage 10 (map persistence), §5 (data model)
- **Milestone:** M15
- **Issue:** [#916](https://github.com/bioedca/tether/issues/916); amends
  [ADR-0128](0128-the-map-file-carries-a-format-version-and-a-reader-refuses-a-newer-one.md)

## Context and problem statement

G02 stores the map as a "separate reusable map file: source IDs + geometry + transform" and asks
"map accepted with recorded QC?". Note 02 says: "A map file is distinct from its calibration
image/video. Reuse checks geometry and optics, not date alone." The `.npz` map file of #353
(ADR-0014, ADR-0128) holds the transform and the channel geometry. Its provenance holds the bead
source and the quality record when #915 made it, as free JSON. It has no id of its own, no
channel layout, no optics, and nothing that compares a map with the movie it is about to
register. A project records the file name and the sha256 of a map it used, but not which map.

What does the map file hold, how does a reader tell a map's fields from an older file's, how does
a project name the map it used, and what decides whether a map may register a movie?

## Decision drivers

- One map file serves many projects. Each project must name the map it used so that two projects
  can be seen to share one.
- The reuse check must name each difference, and it must not use the date (G02 note 02; issue
  acceptance criterion 4).
- ADR-0128: a change that adds arrays raises `MAP_FORMAT_VERSION` by one, and its reader still
  reads every older version. No pickled object enters a map.
- A `.tmap` still reads, and a map from one records that origin and has no quality record unless
  one is calculated.
- A project with a map inside it opens with no change; no schema change for this.

## Considered options

1. **Version 2 of the `.npz` map file with a typed record; the project records the id and the
   checksum in its `/calibration` provenance; the check compares layout, geometry, pixel size and
   optics.**
2. **A JSON side file beside the `.npz`** (`beads.npz` + `beads.map.json`).
3. **The record in the provenance JSON only**, with no version change.
4. **A new `/maps` table in the project skeleton.**

## Decision outcome

Chosen option: **1**, implemented in `src/tether/imaging/map_asset.py` (`MapAsset`,
`save_map_asset`, `load_map_asset`, `check_map_reuse`, `require_map_reuse`, `project_map_links`,
`save_project_map`, `map_asset_from_tmap`), `src/tether/imaging/calibrate.py`
(`MAP_FORMAT_VERSION = 2`, `save_map` writes an asset) and `src/tether/project/extract.py` (the
check at extraction, the link in `/calibration`).

Option 2 makes one asset two files that can be copied apart, and the checksum a project records
would name only half of it. Option 3 keeps the fields out of the format rule: a version-1 reader
would read a version-3 map and drop its layout without a word, the failure ADR-0128 exists to
stop. Option 4 copies the map into every project, which is the opposite of a separate file, and is
a schema change for data that already has a home: the `/calibration` group of each movie.

### The file (format version 2)

Every array of version 1 is written as before. Version 2 adds nine arrays, all plain:
`map_id` (`map:<32 hex>`, made once when the map is saved), `map_origin` (`beads`, `tmap` or
`native-fit`), `source_ids_json` (a JSON list of the #912 source ids, for a bead map
`sha256:<hex>` of the bead file), `channel_layout_json` (the #908 layout with its #910 recipes, or
`null`), `quality_json` (the #915 quality record, or `null`), `optics` (the user's text),
`pixel_size_um` (`NaN` when not known), `created_utc` and `app_version`. A version-2 file that
lacks one is refused and the message names it. A file of version 0 or 1 reads with a derived
record: the id `sha256:<hex>` of its bytes (stable, and it names the file), the origin, source id
and quality record its provenance gives, and no layout, optics, pixel size or time.

`tether map create` writes the side-by-side layout it split with, and takes `--optics` and
`--pixel-size`. A `.tmap` becomes an asset of origin `tmap` with no quality record; its source id
is the sha256 of the `.tmap`.

### The link from a project

A map-file extraction adds to the `/calibration/<id>` provenance's `map_file` record (already
`name` and `sha256`) the map's record: `map_id`, `origin`, `source_ids`, `channel_layout`,
`optics`, `pixel_size_um`, `has_quality`, `created_utc`, `format_version`, the movie's optics text
and the result of the check. `project_map_links` reads `(calibration_id, map_id, sha256, name)`
back. The map stays in its own file; the project keeps a copy of the transform in `/calibration`,
as before. `save_project_map` saves a map that a project holds as a separate asset; the project is
only read.

### The reuse check

`check_map_reuse(map_facts, movie_facts)` compares four things. A different **channel layout**, a
different **channel geometry** or a different **pixel size** is a refusal. A different **optics
text** is a warning that the user must confirm (`--confirm-optics`, `confirm_optics=True`); the
confirmation is recorded. A field one side does not know is not compared and is listed as such. The
date is not an input to the check, so it cannot be a reason to accept or refuse.

Two pixel sizes are equal within a relative 1e-6. That is the rounding of one value written two
ways (a TIFF resolution is a ratio), not a tolerance of the optics: a different objective or
binning changes the pixel size by a factor. It is therefore not a tunable of PRD §11.2.

At extraction the movie's pixel size comes from its file and its optics text from `--optics`. The
movie's layout and geometry are not stated there: a map file splits at its own geometry, and a
layout beside it is refused as before (#353); `check_frame_fits` (#887) still refuses a frame that
cannot hold the map's geometry. The library check compares layout and geometry when a caller
states them, which the mapping page (#917) will do.

### Consequences

- Good: two projects that used one map name the same id and the same checksum.
- Good: a map made for one camera cannot register a movie of another pixel size by accident, and
  the message gives both values.
- Good: a map file written before this change still reads and still registers a movie.
- Bad: an older Tether refuses a version-2 map (ADR-0128's trade-off). The user updates Tether or
  makes the map again.
- Bad: a map with no pixel size or optics text is not checked on those; the check says so in the
  record rather than guessing.
- Neutral: re-saving a map a project used keeps its id but writes new bytes (a new time), so the
  checksum differs; the id says it is the same map, the checksum says which file.
