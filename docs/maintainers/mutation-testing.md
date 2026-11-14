# Reading the mutation report

**Who this page is for.** The maintainer and contributors who read the monthly mutation report
or add to its list of known equivalent mutants. To read the report, you need read access to the
repository's Actions artifacts (any signed-in GitHub account has it). A change to the list is a
pull request. Users do not need this page.

Coverage shows which lines the tests run. It does not show if a test fails when a line is wrong.
A **mutation run** changes one token in the code (a *mutant*) and runs the tests that ran that
line. If no test fails, the mutant **survives**: the tests did not find the change. The run is
issue #725 of the regression plan (#695).

## What the run does

The workflow `.github/workflows/mutation.yml` runs on the 3rd of each month and on a manual
dispatch. It is not a required check, and no pull request runs it. The logic is
`scripts/run_mutation.py`.

1. It runs the default test tier one time under `coverage`. For each line, it records the
   tests that ran it (the per-test coverage contexts of #724).
2. It makes the mutants of each target file. The targets are in `.github/mutation.toml`:
   `src/tether/fret`, `src/tether/analysis`, `src/tether/imaging` and the pure-Python parts of
   `src/tether/idealize` and `src/tether/io`.
3. For each mutant, it runs only the tests that ran the line and passed in step 1. When a shared
   fixture (module, class, package or session scope) ran the line, it also runs every test that
   uses that fixture, because only the first of them ran the line. It stops at the first
   failure. A failure, an error or a run past its time limit **kills** the mutant.
4. It writes the report and uploads it as the artifact `mutation-report`.

The changes are:

| Family | Change |
|---|---|
| Comparison | `<` and `<=`, `>` and `>=`, `==` and `!=`, `is` and `is not`, `in` and `not in` |
| Arithmetic | `+` to `-`, `-` to `+`, `*` to `/`, `/` to `*`, `//` to `/`, `%` to `//`, `**` to `*`, and the same for `+=` and the other assignment operators |
| Logic | `and` to `or`, `or` to `and`, `not x` to `x` |
| Sign | `-x` to `x` |
| Number | `0` to `1`, an integer `n` to `n + 1`, `0.0` to `1.0`, a float `x` to `2x`, `True` and `False` |

The run makes no mutant in a type annotation, an f-string, an `if TYPE_CHECKING:` block, or on a
line with the comment `# pragma: no mutate`. A change that does not compile is not a mutant.

A mutant in a default value runs the tests that run the function. A mutant in a statement at
module or class level runs on import, before any test starts. So it runs every test that ran a
line of that file, and also every test of each test file that imports the module. A test that
only reads a value the import made, such as `assert DEFAULT_STATE_NUMBER_LOW == 1`, runs no line
of the file, and this second rule selects it (#883). A test file imports a module when it has
`import tether.a.b`, `from tether.a.b import ...` or `from tether.a import b` (with `b` a
submodule). It then also imports the parent packages `tether` and `tether.a`, because Python runs
their `__init__.py` first. When a package only passes a name on, as `tether.analysis` passes on
`DEFAULT_STATE_NUMBER_LOW` from `tether.analysis.state_number`, `from tether.analysis import
DEFAULT_STATE_NUMBER_LOW` imports `tether.analysis.state_number` too. When the package takes the
name from one of two modules (`if` and `else`, or `try` and `except`), both count. An import in a
helper module that the test file imports does not count.

## How to read the report

The artifact holds `mutation-report.md` (the same text is in the job summary) and
`mutation-report.json`.

- **Score** of a module = killed / (killed + survived), in percent. A timeout counts as killed.
  Uncovered, equivalent, error and not-run mutants are not in the score.
- **Uncovered**: no test ran the line. The coverage floors of #724 watch these lines.
- **Survivors**: each survivor has its file, line, function, change and the tests that ran the
  line. A survivor is a test gap or an equivalent mutant. At module or class level it can also
  be a mutant that only a test which reads the value through a helper module finds (see above).
  Fix a test gap with a test in its own issue, not in the report job.
- **Incomplete**: the run used all its time before it finished the module. The score of an
  incomplete module is not compared.

## When the run fails

The run fails when the score of a complete module falls by more than five points from the last
report on `main`. It also fails, and writes no report, when the coverage run of step 1 does not
test (a collection error, or no passing test), so a broken run never becomes the next base. Then the report job of #697 opens one issue, `ci(scheduled): mutation
failed on main`, or adds a comment to the open one. Survivors alone never fail the run.

The last report is the newest `mutation-report` artifact from `main`. An artifact is kept for 90
days. If no artifact is found, the run makes the first report and compares nothing.

## Add a known equivalent mutant

An **equivalent mutant** changes the code but cannot change an output, so no test can find it.
For example, `<` to `<=` where the two values can never be equal. Add each one to
`.github/mutation.toml`, with a reason:

```toml
[[equivalent]]
path = "src/tether/fret/example.py"
function = "trim"
line = "if n <= 0:"
change = "<= -> <"
reason = "n counts frames and is at least 1 here, so n == 0 cannot occur"
```

- `function` is the qualified name of the enclosing function (`Class.method`), or `<module>`.
- `line` is the source line without its indent. The entry follows the line when it moves.
- `change` is the text in the report. `occurrence` (default 0) selects one of two equal changes
  on the same line.

The report lists an equivalent mutant one time, in its own section, and does not count it. An
entry that matches no mutant (the code changed) is listed as stale; remove it.
`tests/test_mutation_runner.py` fails when an entry has no reason or matches no mutant.

## Run it on your computer

In the base environment, with `coverage==7.17.2` installed:

```sh
QT_QPA_PLATFORM=offscreen python scripts/run_mutation.py --out mutation-report --only src/tether/fret
```

`--only` limits the run to paths that start with the given prefixes. `--jobs` sets how many
mutants run at the same time (default: the number of processors); each worker has its own copy
of `src/`. `--previous` names an earlier `mutation-report.json` to compare with. The contexts step
runs the whole default tier one time, about four minutes.

## Why an in-repository runner

Issue #725 asked for a comparison of `mutmut`, `cosmic-ray` and a small runner in the
repository. The measurements are from 2026-11-12, on `main` at `46fb76b`, with 4 processors.

| | In-repository runner | mutmut 3.8.1 | cosmic-ray 8.9.0 |
|---|---|---|---|
| Tests for each mutant | the tests that ran the line (per-test contexts) | the tests that called the function (its trampoline records them) | one fixed test command for all mutants |
| `src/tether/fret` | 207 mutants, 7 min (4 of them for the contexts run) | 893 mutants, 12.5 min, with the test files chosen by hand | not run |
| The whole target set | 3905 mutants, 70 min | not run | about 3905 runs of the whole tier (about 90 s each), so days |
| `src` layout | yes | yes | yes |
| What the tests see | the repository, with a copy of `src/` first on `PYTHONPATH` | a copy of the project in `mutants/`; tests that read git, `.agents/` or other files outside `src/` fail there, so the whole tier could not run | the checkout, changed in place |
| Numba-compiled code | changes the source, so Numba compiles the mutant | replaces each function with a trampoline that calls a changed copy; not tried with Numba here | changes the source |
| New packages in the job | none (`coverage` is already pinned in CI) | 6 (`libcst`, `textual`, `setproctitle` and others) | 16 (`aiohttp`, `SQLAlchemy`, `GitPython` and others) |

mutmut is faster for each mutant, because it forks one process and does not import again. It
could not use the whole test tier, and it needed a hand-made list of test files. cosmic-ray has
no way to run only the covering tests. The in-repository runner uses the per-test contexts that
#725 asks for, adds no package, and runs the whole target set in about an hour with four workers.
No function in the targets is Numba-compiled today (the one Numba function is in
`_sidecar_runner.py`, which is excluded).
