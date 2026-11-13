# Running the private-data checks

**Who this page is for.** The maintainer, who has access to the reference workstation and to the
private laboratory data on it, and an agent session with access to that computer. Users and
contributors do not need this page. The decision is [ADR-0071](../adr/0071-private-data-checks-run-from-a-workstation-task.md).

Some checks need data that is not public and is too large for a CI runner, for example the
movie of [oracle (a)](../validation.md). These checks have the `workstation` pytest marker. They
run in no CI job. A scheduled task on the workstation runs them with
`scripts/workstation/run_private_checks.py`. No GitHub self-hosted runner is registered, so a
pull request cannot run code on that computer.

## What one run does

1. It fetches `main` (and only `main`) into its own clone. It refuses a commit that is not on
   `main`, and it refuses a clone that has local changes.
2. It builds the locked environment from `conda-lock.yml` when the lock changes, and it
   installs Tether from the clone.
3. It runs `pytest -m workstation` in the clone. The output of pytest goes to a local log.
4. It writes one result file and prints a short summary.

The runner keeps everything in one **state folder**: `clone/`, `env/`, `logs/` and `results/`.

## The data folder

Put the private data in one folder. Put a file `tether-private-data.json` in that folder. It
gives each acquisition a neutral id and names its files, relative to the folder:

```json
{
  "lab-01": "series-a/acquisition-1.tif",
  "lab-02": {"movie": "series-a/acquisition-2.tif", "export": "series-a/acquisition-2.mat"}
}
```

- A neutral id is lower-case letters, a hyphen and two or more digits: `lab-01`, `bead-01`.
- A file has the role `file` when the value is one name. Use an object for more files.
- The mapping file and the file names stay on the workstation. Do not put them in the
  repository, an issue, a pull request, a chat or a result file.

## Install the task one time

You need `git`, `micromamba` and a Python 3.11 or later on the workstation. The runner itself
uses only the Python standard library.

1. Choose a state folder, for example `%USERPROFILE%\tether-workstation` on Windows or
   `$HOME/tether-workstation` in WSL.
2. Clone `main` into `clone` in that folder:

    ```sh
    git clone --single-branch --branch main https://github.com/bioedca/tether.git <state>/clone
    ```

3. Run it one time by hand. The first run builds the environment, which takes a long time:

    ```sh
    python <state>/clone/scripts/workstation/run_private_checks.py run --state <state> --data <data folder>
    ```

4. Make the schedule with one of the two methods below. Both run weekly, and both start a
   missed run at the next start of the computer. The weekly run always runs. Only the catch-up
   run of the cron method uses `--min-interval-hours 144`, so it does nothing when a run was
   less than 6 days ago, and it can never move or skip the next weekly run.

**Windows (Task Scheduler).** In PowerShell, as the maintainer:

```powershell
$state  = "$env:USERPROFILE\tether-workstation"
$data   = "<data folder>"
$script = "$state\clone\scripts\workstation\run_private_checks.py"
$python = (Get-Command python).Source
$arguments = "`"$script`" run --state `"$state`" --data `"$data`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 3am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName "Tether private checks" -Action $action -Trigger $trigger `
  -Settings $settings
```

`-StartWhenAvailable` is the catch-up: it starts a missed weekly run when the computer is
available again, and the next weekly run still starts on time. If `micromamba` is not on the
`PATH` of the task, add `--micromamba <full path>` to `$arguments`. The runner puts the
environment's own folders first on `PATH` for the checks, as `conda activate` does.

**WSL (cron).** Run `crontab -e` and add two lines (one line each):

```text
0 3 * * 6 python3 "$HOME/tether-workstation/clone/scripts/workstation/run_private_checks.py" run --state "$HOME/tether-workstation" --data "<data folder>" --micromamba "<micromamba path>" >> "$HOME/tether-workstation/logs/cron.log" 2>&1
@reboot sleep 600 && python3 "$HOME/tether-workstation/clone/scripts/workstation/run_private_checks.py" run --state "$HOME/tether-workstation" --data "<data folder>" --micromamba "<micromamba path>" --min-interval-hours 144 >> "$HOME/tether-workstation/logs/cron.log" 2>&1
```

cron starts a command with a short `PATH`, so give the full path of `micromamba`: the output of
`command -v micromamba` in your shell. Keep the quotes, because a folder name can contain a space.

cron runs only while the WSL distribution runs and its cron service is on (`systemd=true` in
`/etc/wsl.conf`, or `sudo service cron start`). The Windows task is the more reliable method.

## Read a result

Each run writes `<state>/results/<UTC time>-<commit>.json`. To see the newest one:

```sh
python <state>/clone/scripts/workstation/run_private_checks.py latest --results <state>/results
```

A result file holds these fields, and nothing more:

| Field | What it holds |
|---|---|
| `schema` | `tether-workstation-result/1` |
| `mode`, `status` | `scheduled` or `dry-run`; `passed`, `failed`, `error`, `refused` or `withheld` |
| `commit`, `on_main` | the 40-hex commit, and `true` when the runner verified that it is on `main` |
| `started`, `finished`, `duration_s` | UTC times and the length of the run |
| `lock_sha256`, `tools` | the hash of `conda-lock.yml` and the versions of Python and the main packages |
| `hardware` | operating system, machine, processor, processor count and memory (no host name, no user name) |
| `checks` | for each test: its name, its outcome, its duration, and for each neutral id it used the summary numbers it recorded |

A check that is not `passed` makes the status `failed`, and a skip counts too: on the workstation
a skip means that data or a tool is missing. The runner exits `0` when all checks pass, `1` when
a check does not pass, `2` on an error, `3` when it refuses the commit or the clone, and `4` when
it withholds the result. A refusal and a failed environment build also write a result file, with
no checks, so that `latest` shows them at once.

**Privacy.** Before it writes a result, the runner searches it for every name of the mapping
file, each of its folder names, its file name and its stem (names shorter than 5 characters are not
searched). It searches each name also as pytest writes it into a test name, where a letter that is
not ASCII becomes an escape (`é` becomes `\xe9`). If it finds one, it removes the checks and writes `withheld`. A folder name that is also
part of a test name (for example `tests`) makes every result `withheld`: give that folder another
name. The pytest log in `<state>/logs/` can name files: it stays on the workstation. Do not attach
it to an issue.

## For an agent session: open or update the issues

An agent session on the workstation does these steps after a scheduled run. It uses the same
one-issue-per-check rule as the scheduled CI jobs (#697).

1. Run the `latest` command above. Ignore a result whose `mode` is `dry-run`.
2. If `result` is `null`, the output has `invalid`, or `age_hours` is more than 192, the
   schedule did not run correctly: use the title
   `test(workstation): the scheduled run failed on main` as in step 3.
3. For each entry of `open_or_update`, search the open issues for exactly its `title`.
    - If none exists, open one with that title. Use the *Validation-oracle failure* form for an
      oracle check and `type:test` for the others. The body has the result file name, the
      commit, the time, the check, the outcome, and the neutral ids and numbers of the entry.
      Copy only fields of the result file.
    - If one exists, add one comment with the same facts for the new result. Add at most one
      comment each day.
4. For each title in `passed` that has an open issue, add one comment that says the check
   passed, with the commit. Do not close the issue: a person or an agent closes it with the
   cause.
5. When the result has the oracle (a) check
   (`tests/test_oracle.py::test_extraction_meets_m1_acceptance_on_uckopsb`), copy its entry,
   the commit, the `finished` time and the result file name into
   `docs/validation/oracle-a-last-run.json` (mode `scheduled`), change the *Newest recorded run*
   paragraph of oracle (a) in `docs/validation.md` to agree, and open a pull request.
   `tests/test_oracle_a_record.py` checks that the two agree.

## Add a check

1. Mark the test `@pytest.mark.workstation`, and give it no other tier marker (`large`,
   `sidecar` or `deep`). Collection stops when a test has both.
2. Use the `private_data` fixture. It skips the test when the runner did not start it.

    ```python
    @pytest.mark.workstation
    def test_extraction_agrees_with_deeplasi(private_data):
        movie = private_data.path("lab-07")
        export = private_data.path("lab-07", "export")
        ...
        private_data.record("lab-07", matched_fraction=0.97, n_molecules=412)
    ```

3. Name the neutral id in an assertion message, not the file.
4. Run the self-check of the runner without the private data:

    ```sh
    python scripts/workstation/run_private_checks.py run --dry-run --state <scratch folder> --data <synthetic data folder>
    ```

The required CI matrix runs the same dry run in `tests/test_workstation_runner.py`, and
`tests/test_marker_contract.py` keeps every CI job from selecting a `workstation` test.
