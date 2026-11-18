<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0100 — Service commands are the one way to run an action

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (issue #892 approved 2026-11-14, track A of the workflow plan #890)
- **PRD anchor:** §7.11 (FR-BATCH, the parity rule), §7.13 (FR-WORKFLOW-7), §4.2
- **Milestone:** M14

## Context and problem statement

The maintainer requires that the whole workflow runs from the application and has full
command-line and batch parity (§7.11, FR-WORKFLOW-7). Before this decision the command line
had two commands, `tether extract` and `tether batch`, whose handlers in `src/tether/cli.py`
called the core directly. The application also calls the core directly from Qt code, and each
new section would add more such calls. Parity was then a promise that nothing checked: an
action could exist in the window and not on the command line, or the two could give different
results for the same input.

Diagram G05 (`docs/design/05-components.dot`) puts a layer of **application services** between
the desktop UI and the persistent assets. What is that layer in code, what may it import, what
does a request look like as text, how does that text change over time, and what is a recipe
file?

## Decision drivers

- **One implementation for each action.** The window, the command line and a recipe must run
  the same code, or the "equal stores" test of #900 cannot pass.
- **Headless means no Qt.** A batch machine or a CI runner must be able to run every action with
  no display and no Qt binding loaded (§7.11: every module is usable without the GUI).
- **A request must outlive the program that wrote it.** A recipe is a record of what was done. A
  later Tether must read it, or refuse it with a reason, and never silently read it differently.
- **Fast start.** `tether --version` must not load the imaging and HDF5 stack.
- **No behaviour change for the existing commands.** The existing command-line tests pass with no
  change of an expected value.

## Decision

1. **Layers (G05).** The *desktop UI* (`tether.gui`) presents the workflow. The *application
   services* (`tether.services`) do the work: one **service command** for each action. The
   *persistent assets* (the `.tether` store, movies, maps, fits) and the *adapters* (readers,
   the tMAVEN sidecar) are reached only through the core packages (`tether.io`,
   `tether.imaging`, `tether.fret`, `tether.idealize`, `tether.ml`, `tether.analysis`,
   `tether.project`). The command line (`tether.cli`) is a front end at the same level as the
   UI. The job coordinator of G05 (#895) will schedule service commands; it is not part of this
   decision.
2. **The import rule.** `tether.services` imports no Qt binding, no library built on one
   (pyqtgraph, napari and the like) and no part of `tether.gui`, at any depth, including
   imports inside functions. It imports the core only inside the function that runs a command,
   so an import of the package loads no third-party module. `tests/test_services.py`
   checks the sources and, in a fresh interpreter, imports the package and every core module a
   command imports and asserts that no Qt module is loaded.
3. **A command.** A command is a `ServiceCommand` in `tether.services.registry.COMMANDS`: a
   name, a request type, a result type, the function that runs it, and a one-line summary.
   The name is the command-line subcommand. `tether.cli` builds one subparser for each
   registered command, in registry order, from a route table; a test fails when a registered
   command has no route, and `build_parser` fails too. The command-line reference page lists
   the registry, and a test compares the two. Expected failures are `ServiceError`s:
   `RequestRefused` (nothing was read or written; `tether batch` exits 2) and `CommandFailed`
   (the command ran and failed; exit 1). The command line prints the result; the command does
   not print.
4. **A request and its text form.** A request is a frozen, keyword-only dataclass whose fields
   are `str`, `int`, `float`, `bool`, `None`-able forms of these, or a tuple of `str`. Paths are
   strings, as the user wrote them. Its text form is one JSON object with three keys,
   `command`, `version` and `fields`, written with sorted keys, no spaces, UTF-8 and no `NaN`
   or infinity. **Every field is written, including fields at their default**, so a later
   change of a default never changes what a stored request means. A reader refuses invalid
   JSON, an unknown command, an unknown key or field, a value of the wrong type (a `bool` is
   not a number, a number is not a `bool`, an integer is accepted for a `float`), a missing
   field that has no default, and a version it does not read. Each registered request type
   converts to text and back with no change (a seeded property test).
5. **The version rule.** Each request type has its own integer `VERSION`, starting at 1.
   Adding a field with a default that gives the old behaviour keeps the version: an old text
   lacks the field and reads as before. Removing or renaming a field, changing its type, or
   changing the meaning of a value increases the version. A reader refuses a version it does
   not read. When a version increases, the reader of the old version stays and converts the old
   text to the new request, so a recipe never becomes unreadable by an upgrade alone.
6. **The recipe file.** A recipe is a UTF-8 JSON file:

   ```json
   {"format": "tether-recipe", "version": 1, "steps": [<request>, <request>]}
   ```

   `format` names the file kind, `version` is the version of the file format (separate from the
   request versions), and `steps` is the ordered list of request text forms. Indentation is
   free. A relative path in a step is relative to the folder of the recipe file. Each step may
   carry one more key, `policy`, which only the ADR of #894 (steps that need approval) defines;
   a reader refuses it until that record defines it. A recipe holds no account secret. `tether run` (#899) reads
   and runs it; a publish step is refused (§7.11, card B3).

## Consequences

- `tether extract` and `tether batch` are the first two commands
  (`tether.services.extract`, `tether.services.batch`). Their output, exit codes and messages
  did not change; the existing command-line tests pass unchanged.
- A new action is a new request type, a run function and a registry entry, and the command line
  gets a route at the same time. #655, #656, #657 and #659 add commands this way.
- **The ratchet.** `tests/test_gui_core_ratchet.py` lists each import from `src/tether/gui` into
  a core package that does not go through a service command (imports under `TYPE_CHECKING`
  are not calls and are not counted). A new one fails the test, and so does an entry that is no
  longer there, so the list only gets shorter. Each section issue (#897 and the C to G tracks)
  removes its entries when it moves its calls onto commands.
- The request format is a stored format: a change to it follows the version rule above, and
  this record is the place to change the rule.
- Names: #343 (the background-task service) becomes the Qt adapter of the job coordinator
  #895 and runs service commands; #340 (the section registry) describes sections, not
  commands. Neither defines a second command list.

## Considered options

- **Keep calling the core from each front end and test parity case by case.** No code to
  write now, but nothing stops a new action from skipping the command line, which is the
  problem this record exists to solve.
- **Generate the command line from the request dataclasses.** Fewer lines, but it would change
  every help text and default string that `docs/cli.md` and its guard tests pin, and #892
  requires no change for the existing commands. A route table that builds the request from
  hand-written `argparse` options keeps the surface and still fails when a route is missing.
- **Pickle, or the repr of a dataclass, as the text form.** Neither is safe or stable to read
  back, and neither is readable by a person who shares a recipe.
- **Omit fields at their default from the text form.** Shorter recipes, but a changed default
  would then change the meaning of every recipe written before it.
- **Put the services under `tether.project`.** The project package is the store; G05 shows the
  services as their own layer, and the import rule is simpler to state and to check for a
  package of its own.
