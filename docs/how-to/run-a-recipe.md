---
description: "Save what you did to a project as a recipe file, and run it again with no window on a new copy of the inputs."
---

# Run a recipe again with no window

## Who this page is for

You made a project once, in the desktop application or with `tether run`, and you want to
do the same work again: on the next day's movies, on a batch machine with no display, or so a
colleague can repeat it. A **recipe** is a file that lists the requests that made the project,
and `tether run` runs them again.

## Before you start

- A terminal where the `tether` command runs
  ([Command line](../cli.md#launching-tether-after-an-installer-install)).
- The input files the recipe names, such as the movies. A recipe names files, it does not hold
  them.
- For **File → Save as recipe…**, a project whose work ran as stored jobs. A run of a recipe
  stores its jobs; a project made by `tether extract` or `tether batch` alone has none yet, and
  the application says so.

## 1. Look at a recipe

A recipe is a JSON file. This one extracts a movie and then fits the project:

```json
{
  "format": "tether-recipe",
  "version": 1,
  "steps": [
    {
      "command": "extract",
      "version": 1,
      "fields": {"movie": "movies/m01.tif", "output": "m01.tether"}
    },
    {
      "command": "idealize",
      "version": 1,
      "fields": {"project": "m01.tether", "method": "vbconhmm", "seed": 7}
    }
  ]
}
```

Each step is one request, in the form the [command-line reference](../cli.md#service-commands)
describes: the command, the version of its request, and its fields. A field that the step
leaves out takes its default, and a recipe that Tether writes states every field, so a later
change of a default never changes what it does. A relative path is read from the folder of the
recipe file, so `movies/m01.tif` is the `movies` folder beside the recipe.

## 2. Check it before it runs

```console
tether run recipe.json --dry-run
```

This prints each step, the files it reads and writes, the project that stores its job, and
each step that needs a person. It changes nothing. A recipe that is not valid is refused with
the step and the reason, before anything runs: an unknown command or field, a value of the
wrong type, a publish step, or a policy with a value missing.

## 3. Run it

```console
tether run recipe.json
```

Each step runs as a job stored in the project it writes. If a step fails, the run stops there
with exit code `1`. Fix the cause, and continue from that step:

```console
tether run recipe.json --resume
```

`--resume` skips the steps that a run of this same recipe file completed. A changed recipe is a
new recipe, so it starts again from its first step.

## 4. When a step needs a person

Some steps need a person: accepting a map, confirming the bleedthrough factor α, approving γ,
and selecting the active fit. A recipe can name a **policy** for such a step, a rule with every
value written down, and the project then records that the policy approved it. With no policy,
the run stops with exit code `4` and prints what to do next. For example:

```console
tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json
tether run recipe.json --resume
```

The approval records your name, the report it was based on, and the recipe that asked for it.
The steps that reach these approvals come with the map, correction and fit pages, so a recipe
today has no step that stops.

## 5. Save a recipe from the application

Open the project and choose **File → Save as recipe…**. The recipe holds the request of each
completed job of the project, oldest first, with each path relative to the folder you save it
in. Keep the recipe beside the inputs, copy the folder, and `tether run` makes the same project
in the copy.

## 6. Check what a recipe holds before you share it

A recipe holds the requests and nothing else: no movie data, no traces and no account secret,
and Tether refuses a recipe with a field that names one. It does hold every path it names and
every value it ran with. A path outside the recipe's folder stays a full path, which can show a
user name or a folder layout, so read the file before you send it. A publish step is never in a
recipe: a recipe can make and upload a deposit draft, but publishing it is a separate command.
