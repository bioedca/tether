---
description: "Save what you did to a project as a recipe file, and run it again with no window on a new copy of the inputs."
---

# Run a recipe again with no window

## Who this page is for

You made a project once, in the desktop application or with `tether run`. Now you want to
do the same work again, on new movies, with no display, or for a colleague. A
**recipe** is a file that lists the requests that made the project,
and `tether run` runs them again.

## Before you start

- A terminal where the `tether` command runs
  ([Command line](../cli.md#launching-tether-after-an-installer-install)).
- The input files the recipe names, such as the movies. A recipe names files, it does not hold
  them.
- For **File → Save as recipe…**, a project whose work ran as stored jobs. A recipe run
  stores them. A project made by `tether extract` or `tether batch` alone has none yet.
  The application then says so.

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

Each step is one request, as the [command-line reference](../cli.md#service-commands) shows
it. It names the command, the version of its request, and its fields. A field that a step
leaves out takes its default. A recipe that Tether writes states every field, so a new default
never changes it. A relative path starts at the folder of the recipe file. Here,
`movies/m01.tif` is in the `movies` folder beside the recipe.

## 2. Check it before it runs

```console
tether run recipe.json --dry-run
```

This prints each step, its files, the project that stores its job, and each step that needs
a person. It changes nothing. A recipe that is not valid is refused before anything runs, with
the step and the reason. A wrong field or value, a publish step, or a policy
with a value missing is refused.

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
and selecting the active fit. A recipe can name a **policy** for such a step. A policy is a
rule with every value written down. The project then records that the policy approved the
step. With no policy, the run stops with exit code `4` and prints what to do next:

```console
tether approve exp.tether gamma.approve --subject KJG_231027 --report runs/gamma-KJG_231027.json
tether run recipe.json --resume
```

The approval records your name, the report it was based on, and the recipe that asked for it.
The steps that reach these approvals come with the map, correction and fit pages. Until then,
no recipe step stops.

## 5. Save a recipe from the application

Open the project and choose **File → Save as recipe…**. The recipe holds the request of each
completed job of the project, in the order the jobs completed. Each path is relative to the folder you save it
in. Keep the recipe beside the inputs, copy the folder, and `tether run` makes the same project
in the copy.

## 6. Check what a recipe holds before you share it

A recipe holds the requests and nothing else: no movie data, no traces, no account secret.
Tether refuses a recipe with a field that names a secret. A recipe does hold every path and
every value it ran with. A path outside the recipe's folder stays a full path. It can show a
user name or a folder layout, so read the file before you send it.

A recipe never publishes.
It can make and upload a deposit draft, and publishing is a separate command.
