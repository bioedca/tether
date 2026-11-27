<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# NNNN — Frame steps run on the whole frame before the split, and a movie's own replace the map's default

- **Status:** accepted
- **Date:** 2026-11-27
- **Deciders:** bioedca (issue #1435 approved 2026-11-25; card of 2026-11-25: the steps are general, not a turn only, and are set on both the map, as a default, and the movie, as an override)
- **PRD anchor:** §7.1 (FR-EXTRACT), Appendix E stages 1 and 10, §5 (data model)
- **Milestone:** M20
- **Issue:** [#1435](https://github.com/bioedca/tether/issues/1435); amends
  [ADR-0128](0128-the-map-file-carries-a-format-version-and-a-reader-refuses-a-newer-one.md) and
  [ADR-0146](0146-the-map-file-is-a-separate-asset-and-reuse-checks-optics-not-the-date.md)
  (map file format version 3)

## Context and problem statement

Tether reads a cellSens `.vsi` movie in the orientation its `.ets` files store (#907, #1398). The
maintainer's TIFFs of the same movies are turned 90° right in an ImageJ batch step before they are
saved, and his maps are made on those TIFFs. A map gives its cut positions in the frame it was made
on, so an unturned `.vsi` with such a map is cut in the wrong place. A 512 x 512 frame turned by 90°
still fits the map, so `check_frame_fits` (#910) cannot see it, and the extraction gives a negative
median acceptor with no error.

The per-channel recipes of #910 run *after* the cut, and extraction refuses a layout beside a map.
Nothing turns the whole frame *before* the cut. How are such steps represented, how do they compose
with an imported `.tmap`, a saved map file and a channel layout, where is a default kept and how
does a movie override it, and what does a project record?

## Decision drivers

- No step resamples a pixel, and with no steps the bytes read are those of today (the frozen
  extraction numbers do not move).
- One set of operations: #910 already has rotate, flip, whole-pixel move and crop, with an exact
  inverse of every point.
- Everything that later re-reads a movie's pixels through its stored `/movies` geometry (generation
  matching, the trace viewer, a re-extraction) must keep working with no knowledge of the steps.
- The maintainer's card: the map gives the default and a movie can change it; "no steps" is a valid
  movie value, for a TIFF that was already turned.
- ADR-0128: a change that adds arrays to a map file raises its format version by one.
- No HDF5 schema change for this.

## Considered options

1. **A `ChannelRecipe` (#910) applied to the whole frame; the stored geometry folds the steps and the
   cut into one geometry of the stored frame.**
2. A new `FrameSteps` type with its own operations.
3. Store the cut geometry of the turned frame in `/movies`, and the steps beside it, so every reader
   applies the steps first.
4. Turn the movie on read, in the movie reader.

## Decision outcome

Chosen option: **1**. Option 2 would duplicate operations whose inverses #910 already tests.
Option 3 would make every reader of `/movies` geometry wrong until it learns the steps; option 1
needs none of them to change, because the canonical rotate → flip → crop geometry of #910 can
express any sequence of the operations followed by a cut. Option 4 is out of scope by the issue:
the reader's orientation is correct and stays as it is.

### The steps

The frame steps of a movie are a `ChannelRecipe`, used on the whole frame. They run on every frame,
in order, before the split of a `.tmap`, a map file, a layout, or the two files of a movie pair.
`rotate 90` is clockwise, ImageJ's "Rotate 90 Degrees Right" (`numpy.rot90(frame, k=3)`), as the
recipe's rotation already was. The steps are applied as a view of the stored frames (rotation,
flip and slicing do not copy), so no pixel is resampled or copied for them. The cut of the map or
the layout, `check_frame_fits` and the layout's own checks all run on the frame after the steps; a
refusal names the movie's frame size and its size after the steps.

`/movies` stores, for each channel, `through_frame_steps(steps, cut)`: the recipe "steps, then the
cut" reduced to one rotate → flip → crop geometry of the stored frame. It gives the same pixels as
the steps followed by the cut. The registration map in `/calibration` keeps the geometry of the
frame after the steps, which is the frame its coordinates are local to.

### Where the steps come from

1. The movie's own steps (`--frame-steps`, or one value for a whole `tether batch`) replace the
   map's default. `none` is a valid value and replaces a default.
2. Otherwise, the map's default:
   - a saved map file stores it (`tether map create --frame-steps`, or `MapAsset.frame_steps`) as
     the array `frame_steps_json` of **map file format version 3**: the JSON of the steps' text, or
     `null` for no default. A version-3 file without the array is refused; a file of version 2 or
     older reads with no default;
   - a `.tmap`, which Tether does not write, takes its default when it is used
     (`--map-frame-steps`), and `map_asset_from_tmap(frame_steps=...)` when it is imported as a map
     file. `--map-frame-steps` without a `.tmap` is refused.
3. Otherwise there are no steps, and the frames are read as stored.

There is no built-in default. Frame steps are not a field of an extraction profile: they describe how
one movie or map was acquired, not how to extract.

### What a project records

- The profile of each movie (`/settings/extraction/movies@<movie_id>`) stores
  `frame_steps: {"steps": "<text>", "source": "map" | "movie"}` whenever steps came from either
  source (`"none"` from a movie included). A run with no steps from anywhere writes no key, so its
  profile is byte for byte the one of today. This is a key of a JSON attribute; the HDF5 layout
  does not change.
- The `/calibration` provenance of a map file run keeps the file's record, which now names its
  `frame_steps`, and of every map with a default, `map_frame_steps`, so a `.tmap`'s default is kept
  with its record in the project.
- `reextract_movie` without steps of its own uses the movie's recorded steps again: a movie's own
  always, and a map's default unless the map used now has a default of its own.

### Consequences

- Good: a `.vsi` and a map made on turned TIFFs give the same molecules and traces as the TIFFs,
  with `rotate 90` on the map or the movie; the synthetic tests show this exactly for every step and
  a combination, with a layout, a map file and a `.tmap`.
- Good: nothing that reads `/movies` geometry changes, and with no steps every geometry and every
  number is as before.
- Good: the per-channel recipes of #910 stay as they are, for a channel turned against the other.
- Bad: an older Tether refuses a version-3 map file (ADR-0128's trade-off); the user updates Tether.
- Bad: the stored geometry no longer shows the steps on its own; the profile's `frame_steps` says
  which were used, and the stored geometry is the one that reads the pixels.
- Neutral: Tether does not detect a wrong orientation by itself (a warning on a negative median
  acceptor is a separate issue), and turns that are not a multiple of 90° are out of scope.

## More information

The workstation check of #1433 can use `--frame-steps "rotate 90"` instead of its own test-only
turn. The import page of #913 can show the steps with a preview later. Tests:
`tests/test_frame_steps.py`.
