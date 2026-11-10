<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0068 — A project holds one experiment, and a condition can span projects

- **Status:** accepted
- **Date:** 2026-11-10
- **Deciders:** bioedca
- **PRD anchor:** §5.1 (project, experiment, condition), §3.1 UC3, §5.4, §7.5, §7.6, §7.7, §7.11
- **Milestone:** M12

## Context and problem statement

M12 gives a project the entry point that appends a second movie
([#347](https://github.com/bioedca/tether/issues/347)). When a project can hold several movies,
something must say which movies go into one file. On `main` at `3f9b6bb` the plan gave three
different answers:

- The description of the milestone "M12 - Project data model & write safety" said "one `.tether`
  per experiment/condition", which names two different units.
- PRD UC3 said that a condition "spans ≈100 videos across multiple days and files".
- The how-to guides told the user to keep conditions in separate projects, and `tether batch`
  writes one project for each movie (`src/tether/cli.py:339`).

The correction pass ([#360](https://github.com/bioedca/tether/issues/360),
[#624](https://github.com/bioedca/tether/issues/624)), the ranker loop (PRD §7.5), the batch
output ([#653](https://github.com/bioedca/tether/issues/653)) and plot pooling
([#660](https://github.com/bioedca/tether/issues/660)) each need one answer before they are built.
What does one `.tether` project hold, and which features must work across project files?

## Decision drivers

- **The single-writer lock is per file** (PRD §5.4, ADR-0055). A file has one writer at a time, so
  the more a file holds, the more work one lock stops.
- **The file is the unit of sync.** A project is one file on OneDrive or a network share, and a
  OneDrive conflict copy duplicates the whole file (§5.4). The contents of one acquisition session
  set a limit to its size; the length of a study does not.
- **A condition outlives one day.** UC3 and goal G5 describe a condition of ≈100 videos over many
  days. The identity of a condition does not depend on a file: it is the §5.1 condition key,
  content-hashed into `condition_id`, with `date`, `replicate` and source file free to vary
  inside it (ADR-0033).
- **The PRD already uses the experiment as a unit.** Goal G2 asks for "a single self-describing
  project file per experiment", `/settings` holds the parameters "per experiment", and `/traces` is
  padded to the experiment-max frame count because "one experiment spans many movies".
- **The schema already allows it.** `/movies` and `/conditions`, declared at M0, hold any number of
  rows (ADR-0005). This decision needs no new structure.

## Considered options

- **A. One experiment for each project.** One day or one session of acquisition. A project can hold
  several conditions, and a condition can span several projects.
- **B. One condition for each project.** Every movie of the condition, from every day, in one file.
  No feature has to work across files.
- **C. One movie for each project, as today.** A project with several movies is optional.

## Decision outcome

Chosen option: **A**, the maintainer's decision of 2026-11-10 (usability audit, question D1,
recorded on [#623](https://github.com/bioedca/tether/issues/623)). It agrees with UC3 and G5, where
a condition outlives one day, and with G2, where a project is an experiment. It also keeps each
single-writer file to the data of one session.

Option B makes the file and its lock grow with the study. One lock would then cover every day of
a condition, so the whole study would have one writer at a time, and every OneDrive sync and
conflict copy would carry the whole study. Option C makes ≈100 files for each condition. The features in the table
below would then have to work across files in any case, and no file would hold the movies of one
day together with their shared calibration and settings.

### The three words

- An **experiment** is one day or one session of acquisition: the movies recorded together, with
  their calibration and their extraction settings.
- A **project** is the `.tether` file that holds one experiment: its movies, their molecules and
  traces, and every label, correction and idealization made on them. "Experiment file" and
  "project file" name the same file.
- A **condition** is one chemistry/optics tuple, identified by the PRD §5.1 condition key. The key
  is the identity of the condition in every project: two projects whose `/conditions` rows carry
  the same confirmed key hold the same condition. A key parsed from a filename is provisional until
  the validation step (PRD §9.3) confirms or corrects it. The parse gives no buffer, temperature or
  laser power (`tether.io.filename.parse_filename`), so two provisional keys can agree for two
  different conditions, and a feature that joins projects by the key joins them only on confirmed
  keys. Keep-separate (ADR-0033) applies across projects as it does inside one: a key that differs
  in one field is a different condition, and nothing merges two conditions without a confirmation.

### What each unit governs

| Unit | It governs |
|---|---|
| Project | the single-writer lock (§5.4); the size of the synced file; what a user opens; what an extraction, an import or a batch run writes into; `/settings` and `/calibration` |
| Condition | the correction factors (§7.2); the ranker model (§7.5); the category list and its integer↔category lookup (§7.6); plot selection and pooling (§7.7) |

### Features that must work across project files for one condition

| Feature | What crossing files means | Carried by |
|---|---|---|
| Plot pooling | A selection names several projects, reads each one read-only, and pools the molecules of one condition | [#660](https://github.com/bioedca/tether/issues/660), with [#369](https://github.com/bioedca/tether/issues/369) and [#374](https://github.com/bioedca/tether/issues/374) |
| The ranker model follows the condition | One standalone model file for each condition (ADR-0035), referenced from every project that holds the condition, with its own owner lock | [#561](https://github.com/bioedca/tether/issues/561), [#640](https://github.com/bioedca/tether/issues/640) |
| The ranker's training set | A retrain refits on every human label of the condition (ADR-0035), and those labels are now in several projects | [#678](https://github.com/bioedca/tether/issues/678) |
| The per-condition category list | The list and its integer↔category lookup name the same categories, with the same codes, in every project that holds the condition | [#679](https://github.com/bioedca/tether/issues/679), with [#633](https://github.com/bioedca/tether/issues/633) and [#367](https://github.com/bioedca/tether/issues/367) |

### Consequences

- Good: one answer for the M12 append (#347), the batch destination (#653), the extraction queue in
  the application ([#645](https://github.com/bioedca/tether/issues/645)), the Deep-LASI
  reconstruction (#347), the corrections (#624) and the plot builder (#660). Each of them reads the
  definition here and decides nothing about it.
- Good: no schema change. The definition uses the M0 groups as they are, so the additive-only
  freeze (ADR-0005) is not touched.
- Bad / trade-off: the four features in the table above must work across files. Option B would have
  needed none of them.
- Bad / trade-off: the data of one condition is in several files. A file that is moved, or that is
  a OneDrive placeholder not on the disk, cannot give its molecules to a pooled plot or its labels to
  a retrain until it is available. §5.4 forbids hydrating a placeholder automatically, so a
  cross-file feature that cannot read a file names that file instead of continuing on part of
  the data.
- Bad / trade-off: each project keeps its own copy of the per-condition data it stores, such as the
  category list, so two projects of one condition can disagree. #679 carries the list; the
  correction factors are the open point below.
- Open, for #624: D2 put the correction factors "per condition inside the project". With one project
  for each day, that gives a condition that spans three projects three estimates, one for each day.
  PRD §5.1 says that α has no scope finer than the condition, and the per-day scope was an option
  that D2 did not choose. #624 decides which of the two holds, and its ADR records it. This record
  does not decide it.
- Until the code reaches this decision, `tether extract` and `tether batch` write one project for
  each movie. That is a limit of the commands, not a rule about what a project holds. #347 adds the
  append, #653 gives `tether batch` one destination project, and #645 does the same in the
  application.
- Follow-up: the M12 milestone description says "one `.tether` per experiment/condition". Only the
  maintainer can edit it cleanly; the replacement text is on the pull request that adds this record.
  The how-to guides that say "holds one movie" change with #347, under
  [#601](https://github.com/bioedca/tether/issues/601).

## More information

- PRD §5.1 (the definition and the group skeleton), §3.1 UC3, §5.4, §7.5, §7.6, §7.11.
- [ADR-0005](0005-m0-schema-freeze.md) (the M0 schema freeze),
  [ADR-0033](0033-condition-identity-and-rekey.md) (condition identity and keep-separate),
  [ADR-0035](0035-portable-model-persistence.md) (the portable model file and refit on accumulated
  labels), [ADR-0055](0055-gui-session-writer-ownership.md) (the application session owns the project's
  single-writer lock).
- The audit that asked the question: [#622](https://github.com/bioedca/tether/issues/622).
