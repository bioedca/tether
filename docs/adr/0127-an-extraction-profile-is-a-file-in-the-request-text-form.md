<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0127 — An extraction profile is a file in the request text form, and `--tdat` sets two fields

- **Status:** accepted
- **Date:** 2026-11-17
- **Deciders:** bioedca
- **PRD anchor:** §7.11 (FR-EXTRACT, FR-BATCH), §11.2, §8 NFR-REPRO
- **Milestone:** M15
- **Issue:** [#349](https://github.com/bioedca/tether/issues/349)

## Context and problem statement

`ExtractOptions` (`tether.project.extract`) has seventeen fields, and its docstring makes every one
of them part of the pinned contract, recorded verbatim in `/settings/extraction`. Before #349,
`tether extract` set ten of them, seven were reachable only from Python, and `tether batch` built
no `ExtractOptions` at all, so a batch ran at the defaults whatever the experiment needed. A
`.tdat` replaced the detection mode and threshold with no stated rule.

#349 asks for an *extraction profile* that carries all seventeen fields, for `tether batch` to
accept one, for the `--tdat` rule to be stated, and for a decision on where a profile lives
against the application settings store of #330 (ADR-0095). Its plan change of 2026-11-14 adds that
the profile becomes the extraction part of a recipe (#899), so its text form must be the request
form of the service commands (ADR-0100).

## Decision drivers

- NFR-REPRO: what ran must be recoverable, from the project and from the recipe that ran it.
- ADR-0100: the command line, the application and a recipe run the same request; the request is
  plain data with one text form, and reading it refuses an unknown field.
- An eighteenth `ExtractOptions` field must not be able to miss the profile silently.
- The existing refusal of `--tdat` beside `--detection-mode`/`--detection-threshold` must not be
  weakened by a second way of choosing a mode.
- The fields the later workflow issues add (#908, #901, #910, #920, #916) each come with a default
  that keeps today's behaviour, so the form must take an added field without a version step.

## Considered options

1. **A JSON file the user names, in the request text form, whose seventeen fields are also fields of
   the `extract` and `batch` requests.**
2. **A named profile stored in the application settings store** (ADR-0095), chosen by name.
3. **A request field that holds the path of a profile file**, read when the command runs.
4. **Seven more `tether extract` flags and seventeen `tether batch` flags**, and no file.

## Decision outcome

Chosen option: **1**.

- **A profile is a file** (`tether.services.profile`). `ExtractionProfile` has the seventeen fields
  of `ExtractOptions`, with the same names and defaults. Its text form is the request text form of
  ADR-0100 with `command` set to `"extraction-profile"`: every field written, sorted keys, a missing
  field taken at its default, an unknown field or wrong type refused. `read_profile` also builds the
  `ExtractOptions`, so a value extraction would refuse is refused when the file is read.
- **The requests carry the values, not the file.** `ExtractionFields`, the seventeen fields, is a
  base of `ExtractRequest`, `BatchRequest` and `ExtractionProfile`. `--profile` is read by the
  command line into the request, so a stored request, and a recipe, holds every value it ran with,
  and a later edit of the file cannot change what it means. `BatchRequest` builds a real
  `ExtractOptions` and passes it to `run_batch`.
- **One source of options.** `tether extract --profile` with any of the ten option flags is refused,
  even when the flag repeats the profile's value. An argparse action records which flags were given,
  so the defaults of the flags stay the `ExtractOptions` defaults the parameter page checks.
- **The `--tdat` rule.** A `.tdat` sets `detection_mode` and `detection_threshold` and no other
  field. It is refused beside a choice of either, from a flag or from a profile. To tell a choice
  from the default, `detection_mode` is `None` ("no choice") in `ExtractionFields`, as it already was
  in `ExtractRequest`; `extract_options()` resolves it to `"wavelet"`. The applied values and
  `tdat_source` stay recorded in `/settings/extraction`.
- **Storage against #330.** The profile does not live in the settings store. The store holds one
  person's preferences on one computer and is kept across updates (ADR-0095); a profile is an input
  of an analysis, which must travel with the data, the recipe and the methods section to another
  computer and another person. A run whose result depended on a name in someone's settings would
  not be reproducible from the project and the recipe. The application may later remember the
  *path* of the last profile in the store, as it may remember other recent files; that is a
  preference, not the profile.
- **No version step.** The requests keep version 1: their new fields have defaults, so a request
  written before #349 still reads and means what it meant.
- **Registered tunables.** Every field is a row of PRD §11.2; `min_separation` and `donor_side`
  had no row of their own and now do. A test reads `dataclasses.fields(ExtractOptions)` and fails
  when a field is missing from the profile, from either request, or from §11.2.

### Consequences

- Good: all seventeen fields have a surface; `tether batch` can extract with anything but the
  defaults; a recipe step is complete without a file beside it.
- Good: the `--tdat` rule is one rule in both commands, stated in the help, `docs/cli.md` and here.
- Neutral: the profile's filename is not recorded in `/settings/extraction`, only its values. The
  values are what reproduce a run; recording the name would be a new provenance key.
- Neutral: a batch re-run with a changed profile does not re-extract a movie whose extraction is
  complete, as before for every option (ADR-0056); `docs/cli.md` says to use a new `--out-dir`.
- Bad: the seventeen defaults are written twice, in `ExtractOptions` and in `ExtractionFields`,
  because the services import only the standard library. A test compares them field by field.

## More information

- ADR-0100 (service commands and the request text form), ADR-0095 (the settings store), ADR-0056
  (batch checkpoints), ADR-0022 (per-mode minimum separation), ADR-0070 (the donor-side check).
- Number 0127 was taken as one above the highest record on any branch (0126), because
  `claim.py reserve-adr` cannot create its ref from a cloud container.
