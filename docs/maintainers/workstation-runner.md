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

## Who can read the state folder

The pytest log in `logs/` can name private files, so only your account must read it.

- **Linux, macOS and WSL.** The runner makes the state folder, `logs/` and `results/` owner-only
  (`700`), and the log file and the check record owner-only (`600`), whatever your `umask` is.
  When one of these folders already has group or other permission bits, the runner removes them
  before it runs and prints a `note:` line. It does not refuse the run. A `--results` folder
  outside the state folder holds only sealed results, so the runner does not change it.
- **Windows.** The runner does not change permissions. A folder that you make in your user
  profile (`%USERPROFILE%`) inherits the ACL of the profile folder. By default, that ACL gives
  full control to `SYSTEM`, to `Administrators` and to your account, and nothing to `Users` or
  `Authenticated Users`. Thus other standard accounts cannot read the log, but an administrator
  can. Put the state folder in your profile. A folder outside the profile, for example in `C:\`,
  inherits the ACL of `C:\`, which by default lets every signed-in account read it.
- **Check it.** On Windows, run `icacls <state folder>` and look for entries other than
  `SYSTEM`, `Administrators` and your account. An entry that a person added to the profile folder
  is inherited too. On POSIX, run `ls -ld <state folder> <state folder>/logs`.

The data folder and its mapping file keep the permissions that you give them. They name the
files too, so give them the same protection.

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

### Public archives in the data folder

Some `workstation` checks use public data that is too large for CI. The reference-data manifest
(`tests/reference_data/manifest.toml`) names each such archive with the tier `workstation`, its
size and its SHA-256, and the check refuses a file with a different SHA-256. These names are
public, so they can be in the repository. Put each archive at its manifest path below the data
folder, and give it the neutral id that its check uses:

| Neutral id | Manifest path below the data folder | Check (#716) |
| --- | --- | --- |
| `dlasi-02` | `zenodo-7561162/Figure_2.zip` | one-colour Deep-LASI set: import and molecule count |
| `dlasi-03` | `zenodo-7561162/Figure_3.zip` | two-colour Deep-LASI set: import, FRET states and dwell times |

```json
{
  "dlasi-02": "zenodo-7561162/Figure_2.zip",
  "dlasi-03": "zenodo-7561162/Figure_3.zip"
}
```

To get the two archives (1.5 GB together) in PowerShell, in the data folder:

```powershell
New-Item -ItemType Directory -Force zenodo-7561162 | Out-Null
foreach ($n in 'Figure_2.zip', 'Figure_3.zip') {
  curl.exe -L -o "zenodo-7561162\${n}" "https://zenodo.org/records/7561162/files/${n}?download=1"
}
Get-FileHash -Algorithm SHA256 zenodo-7561162\*.zip
```

Compare each hash with the `sha256` of its manifest entry. Add the two neutral ids to the
mapping file next to the private ones.

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

4. Make the schedule with one of the two methods below. Both run each night, because the
   speed history (below) needs one record each night (maintainer decision of 2026-11-11, #723).
   Both start a missed run at the next start of the computer. The nightly run always runs. Only
   the catch-up run of the cron method uses `--min-interval-hours 20`, so it does nothing when a
   run was less than 20 hours ago, and it can never move or skip the next nightly run.

**Windows (Task Scheduler).** In PowerShell, as the maintainer:

```powershell
$state  = "$env:USERPROFILE\tether-workstation"
$data   = "<data folder>"
$script = "$state\clone\scripts\workstation\run_private_checks.py"
$python = (Get-Command python).Source
$arguments = "`"$script`" run --state `"$state`" --data `"$data`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName "Tether private checks" -Action $action -Trigger $trigger `
  -Settings $settings
```

`-StartWhenAvailable` is the catch-up: it starts a missed nightly run when the computer is
available again, and the next nightly run still starts on time. A task that you installed with
the older weekly trigger changes with
`Set-ScheduledTask -TaskName "Tether private checks" -Trigger (New-ScheduledTaskTrigger -Daily -At 3am)`. If `micromamba` is not on the
`PATH` of the task, add `--micromamba <full path>` to `$arguments`. The runner puts the
environment's own folders first on `PATH` for the checks, as `conda activate` does.

**WSL (cron).** Run `crontab -e` and add two lines (one line each):

```text
0 3 * * * python3 "$HOME/tether-workstation/clone/scripts/workstation/run_private_checks.py" run --state "$HOME/tether-workstation" --data "<data folder>" --micromamba "<micromamba path>" >> "$HOME/tether-workstation/logs/cron.log" 2>&1
@reboot sleep 600 && python3 "$HOME/tether-workstation/clone/scripts/workstation/run_private_checks.py" run --state "$HOME/tether-workstation" --data "<data folder>" --micromamba "<micromamba path>" --min-interval-hours 20 >> "$HOME/tether-workstation/logs/cron.log" 2>&1
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
2. If `result` is `null`, the output has `invalid`, or `age_hours` is more than 36 (a day and a
   half), the schedule did not run correctly: use the title
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
   cause. When a scheduled run completes again, `passed` also has the title
   `test(workstation): the scheduled run failed on main`, so an open run-level issue gets its
   recovery comment the same way. A run completes when its checks ran, also when one of them
   failed: that check has its own issue. A result older than 36 hours does not count (step 2).
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

## The speed history

A scheduled run also measures speed and memory on the workstation (#723). The CI comparison of
#722 finds a large regression in one pull request; the history finds a slow drift over many
changes, on the computer that is the reference for speed (maintainer decision Q28).

**What a run measures.**

1. `tests/test_workstation_perf.py` runs the full pipeline on each acquisition whose neutral id
   has the role `speed` in the mapping file: extraction, corrections, idealization, analysis,
   save and export. Give the role to at least three full acquisitions. The roles `tmap` and
   `tdat` of the same id, when they are present, go to the extraction:

    ```json
    {"lab-11": {"speed": "series-b/acquisition-11.tif", "tmap": "series-b/beads.tmap"}}
    ```

    Each stage runs in its own process (`scripts/workstation/perf_stage.py`), so each stage has
    its own peak memory. Idealization uses the sidecar: add
    `--sidecar-python <the python of a sidecar environment>` to the arguments of the task, or
    set `TETHER_SIDECAR_PYTHON` for your account. An installed Tether has one in its
    `envs/sidecar` folder; `scripts/setup_sidecar.py` builds one from a checkout.
2. The runner runs the benchmark suite of #722 (`benchmarks/bench.py`, groups `default` and
   `gui`, one round) on the same commit.
3. It appends one record to `<state>/perf-history.jsonl`, one JSON object on each line.

**A record** (format `tether-workstation-history/1`, checked by `tests/test_perf_history.py`)
holds these fields and nothing more:

| Field | What it holds |
|---|---|
| `schema`, `commit`, `on_main`, `date` | the format, the 40-hex commit, `true` for a verified commit of `main`, the UTC time |
| `hardware` | operating system, machine, processor, processor count, memory, `disk` (`ssd` or `hdd`, the disk of the data folder) and `gpu` |
| `acquisitions` | for each neutral id: `<stage>_s` and `<stage>_peak_bytes` for the six stages, and `tether_bytes` |
| `benchmarks` | for each case and size of #722 (`extraction/large`): `time_s` and `peak_bytes` |
| `benchmark_errors` | the cases that did not run, and `<group>/run` for a group that did not run at all |
| `projection` | `per_movie_s` (the median over the acquisitions of extraction + corrections + idealization), `condition_movies` (100), `condition_hours`, `window_hours` (12, ADR-0032) and `fits_window` |

On Linux a peak is the peak resident set of the stage process or of one of its children. On
Windows it is the peak committed memory of the stage process and its children together. Compare
values only on one computer.

The history holds neutral ids, stage names, case names and numbers: no file name and no trace.
The runner refuses to write a record that contains a name of the mapping file. So you can attach
a copy of the history, or its plot, to an issue.

**The report rule** (maintainer decision Q27). A value is a slowdown when it is larger than 1.2
times the median of the same value in the last 10 records of `main` on the same hardware
(processor, memory, disk, GPU). A value with fewer than 3 earlier values in those 10 records only
records. A peak
memory below 1 MiB only records. The runner adds two checks to the result file:

- `perf-history/acquisitions` fails on a slowdown of an acquisition value, and when the
  projection does not fit the overnight window. Its datasets give `<value>_ratio` for each
  slowdown.
- `perf-history/benchmarks` fails on a slowdown of a benchmark value, and when a benchmark case
  or group did not run.

A check that fails opens or updates its issue through the steps above (#698). A history that
cannot be read gives both checks the outcome `error`; the runner does not change that file.

**See the history.** Run these with the Tether environment:

```sh
python <state>/clone/scripts/workstation/perf_history.py <state>/perf-history.jsonl
python <state>/clone/scripts/workstation/plot_perf_history.py <state>/perf-history.jsonl --out history.png
```

The first prints the newest record's projection and its slowdowns. The second draws the stage
times, the stage peaks and the projection for each record of `main`.

**Show that the rule works.** The runner runs only commits of `main`. To show a slowdown, make a
throw-away branch in a scratch clone of the repository, not in the runner's clone, and make one
stage slower there. Copy the history, then run from that scratch clone:

```sh
python scripts/workstation/run_private_checks.py run --dry-run --speed --state <scratch state> --data <data folder> --history <copy of the history>
```

A dry run measures the code of the checkout that you start it from. The record that it appends
says `on_main: false`, so it is never part of a median. The
result shows `perf-history/acquisitions` failed, with the ratio of the slower stage.

**The workstation and the reference hardware.** PRD §8 names a laptop as the reference hardware
floor. The workstation is the reference computer for speed (Q28). These are the differences, so
a reader can scale a time from the history:

| | PRD §8 reference floor | Workstation |
|---|---|---|
| Processor | not named | Intel Core i7-14650HX, 16 cores, 24 threads |
| Memory | 16 GB | 16 GB (2 × 8 GB at 5600 MT/s; Windows shows 15.7 GB usable) |
| Disk | spinning hard disk drive, about 100 GB free | 1 TB NVMe solid-state drive |
| GPU | NVIDIA RTX 4060 Laptop GPU, 8 GB | NVIDIA GeForce RTX 4060 Laptop GPU, and an integrated Intel UHD Graphics |

The memory and the GPU model are the same. The operating system is Windows 11. The disk is the
important difference: an NVMe solid-state drive reads and writes much faster than a spinning
disk. A stage that waits on the disk, for example `extract` (it reads the movie) or `save` (it
writes the `.tether`), can be faster on the workstation than on the reference floor. Thus, a
time from the history can be shorter than the time of the same stage on a computer at the floor.
The size of the GPU memory was not measured on the workstation, because Windows reports a
maximum of 4 GB for it.

The hardware of each record is in its `hardware` field, so a change of the workstation shows in
the history and starts a new baseline.
