# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the checks that need private laboratory data on the maintainer's workstation (#698).

Some checks need data that stays private and is too large for a runner. A scheduled task on the
maintainer's reference workstation runs this script. No GitHub self-hosted runner exists, so no
pull request can run code on that computer (ADR-0071). ``run`` does these steps in order:

1. Fetch ``main`` (only ``main``) of the canonical repository into the runner's own clone, and
   refuse a commit that is not reachable from ``origin/main`` or a clone that is not clean.
2. Build the locked environment from that commit's ``conda-lock.yml`` when the lock changed, and
   install that commit's ``tether`` into it.
3. Run ``pytest -m workstation`` in the clone. The tests find the data through
   ``$TETHER_PRIVATE_DATA``: a directory that holds ``tether-private-data.json``, which maps
   neutral ids (``lab-01``) to file names. The output of pytest goes to a local log only.
4. Write one result file: commit, date, tool versions, hardware summary, and for each check its
   outcome, its neutral ids and its summary numbers. No traces, no images, no file names: a
   result that contains a mapped file name is withheld.
5. Print a short summary that contains no mapped file name.

``run --dry-run`` skips steps 1 and 2 and runs only the self-check, with this interpreter, against
the data directory it gets. The required CI matrix tests the runner that way. ``latest`` prints, as
JSON, the newest result and the issue titles an agent opens or updates for it
(``docs/maintainers/workstation-runner.md``).

On POSIX the state folder, its ``logs`` and ``results`` folders, the pytest log and the check
record are owner-only: the log can name private files. A state folder with group or other
permission bits is corrected, and the run says so. On Windows the runner relies on the ACL that
the folder inherits; the maintainer page says what that gives.

Exit status: 0 all checks passed, 1 a check did not pass, 2 error, 3 refused, 4 result withheld.
A run that gets as far as reading the mapping file writes a result file whatever its status, so
a refusal or a broken environment build shows in ``latest`` at once.
This file uses only the standard library: ``tests/conftest.py`` loads it in every environment.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import numbers
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

if os.name == "nt":
    import msvcrt
else:
    import fcntl

REPO = Path(__file__).resolve().parents[2]
CANONICAL_REMOTE = "https://github.com/bioedca/tether.git"
MAIN_REFSPEC = "+refs/heads/main:refs/remotes/origin/main"
DATA_ENV = "TETHER_PRIVATE_DATA"
RECORD_ENV = "TETHER_WORKSTATION_RECORD"
MAPPING_FILE = "tether-private-data.json"
SELF_CHECK = "tests/test_workstation_selfcheck.py"
RESULT_SCHEMA = "tether-workstation-result/1"
#: The test tooling of the required matrix (ci.yml "Install test tooling"); a test keeps them equal.
TEST_TOOLS = ("pytest==9.1.1", "pytest-qt==4.5.0")
TOOL_PACKAGES = ("tether", "numpy", "scipy", "numba", "h5py", "tifffile", "scikit-image", "pytest")
LOCK_STAMP = "tether-lock.sha256"
PRIVATE = "<private>"
#: Shorter names are not searched for: they are too generic to name an acquisition.
MIN_TOKEN = 5
#: A git fetch or checkout (which can download LFS files) that takes longer is a run error, so a
#: stalled network never keeps a run, and its lock, alive with no result.
NETWORK_TIMEOUT_S = 1800

NEUTRAL_ID = re.compile(r"[a-z]+-[0-9]{2,}")
ROLE = re.compile(r"[a-z][a-z0-9_]{0,31}")
METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
MODES = ("scheduled", "dry-run")
STATUSES = ("passed", "failed", "error", "refused", "withheld")
_RANK = {None: -1, "passed": 0, "skipped": 1, "failed": 2, "error": 3}
OUTCOMES = tuple(k for k in _RANK if k)
RUN_ISSUE_TITLE = "test(workstation): the scheduled run failed on main"
#: A newest result older than this means that the weekly schedule did not run (a week and a day).
STALE_HOURS = 192
#: The permission bits a folder or a file of the state folder may not have on POSIX.
GROUP_OTHER = 0o077

EXIT_OK, EXIT_FAILED, EXIT_ERROR, EXIT_REFUSED, EXIT_WITHHELD = 0, 1, 2, 3, 4


class Refused(Exception):
    """The runner does not run this commit or this clone."""


class MappingError(ValueError):
    """The name-mapping file is malformed. A message never quotes a value of the file."""


# --- The name-mapping file --------------------------------------------------------------------


def load_mapping(data_dir: Path) -> dict[str, dict[str, str]]:
    """Read the mapping: neutral id -> file name, or neutral id -> {role: file name}.

    A bare file name gets the role ``file``. Names are relative to the data directory.
    """
    try:
        raw = json.loads((Path(data_dir) / MAPPING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MappingError(f"cannot read {MAPPING_FILE} ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or not raw:
        raise MappingError(f"{MAPPING_FILE} must be a non-empty JSON object")
    mapping: dict[str, dict[str, str]] = {}
    for index, (key, value) in enumerate(raw.items(), 1):
        if not NEUTRAL_ID.fullmatch(key):
            raise MappingError(f"entry {index}: the key is not a neutral id such as lab-01")
        files = {"file": value} if isinstance(value, str) else value
        if not isinstance(files, dict) or not files:
            raise MappingError(f"{key}: the value must be a file name or an object of file names")
        mapping[key] = {}
        for role, name in files.items():
            if not ROLE.fullmatch(role):
                raise MappingError(f"{key}: a role is not a lower-case word")
            if not isinstance(name, str) or not name.strip():
                raise MappingError(f"{key}/{role}: the file name is empty")
            rel = PurePosixPath(name.replace("\\", "/"))
            if rel.is_absolute() or PureWindowsPath(name).anchor or ".." in rel.parts:
                raise MappingError(f"{key}/{role}: the file name must be relative to the directory")
            mapping[key][role] = rel.as_posix()
    return mapping


def private_tokens(mapping: dict[str, dict[str, str]]) -> frozenset[str]:
    """The strings no result and no console line may contain: each name, each of its folder
    names, its file name and its stem, also as pytest writes them into a node id (a parameter
    ``acquisition-\u00e9clair.tif`` becomes ``acquisition-\\xe9clair.tif``)."""
    tokens = set()
    for files in mapping.values():
        for rel in files.values():
            path = PurePosixPath(rel)
            tokens.update({rel, rel.replace("/", "\\"), path.name, path.stem, *path.parent.parts})
    tokens = {t for t in tokens if len(t) >= MIN_TOKEN}
    return frozenset(tokens | {t.encode("unicode_escape").decode("ascii") for t in tokens})


def redact(text: str, tokens: frozenset[str]) -> str:
    for token in sorted(tokens, key=len, reverse=True):
        text = text.replace(token, PRIVATE)
    return text


#: POSIX only: Windows has no such flag, so there the open flags stay as they were (#884).
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def open_private(path: Path, *, append: bool = False):
    """Open *path* for writing text; a new file is owner-only on POSIX (it can name files).

    On POSIX a symbolic link at *path* is refused, so that the open cannot truncate or append to
    the file that the link points at (#884).
    """
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC) | NOFOLLOW
    try:
        fd = os.open(path, flags | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        if NOFOLLOW and os.path.islink(path):
            raise RuntimeError(
                f"refused to open {Path(path).name}: it is a symbolic link"
            ) from None
        raise
    if os.name != "nt":
        os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) & ~GROUP_OTHER)
    return os.fdopen(fd, "a" if append else "w", encoding="utf-8")


def private_dir(folder: Path) -> bool:
    """Create *folder* owner-only, or correct it to owner-only, on POSIX.

    Returns ``True`` when an existing folder had group or other bits that were removed. On Windows
    nothing changes: the folder keeps the ACL that it inherits.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        return False
    mode = stat.S_IMODE(folder.stat().st_mode)
    if not mode & GROUP_OTHER:
        return False
    os.chmod(folder, mode & ~GROUP_OTHER)
    return True


def append_record(path: Path, entry: dict) -> None:
    with open_private(path, append=True) as fh:
        fh.write(json.dumps(entry) + "\n")


class PrivateData:
    """What a ``workstation`` test gets from the ``private_data`` fixture (tests/conftest.py)."""

    def __init__(self, root: Path, nodeid: str, record: Path | None = None) -> None:
        self.root = Path(root)
        self.mapping = load_mapping(self.root)
        self._nodeid = nodeid
        self._record = record

    @property
    def ids(self) -> list[str]:
        return sorted(self.mapping)

    def roles(self, neutral_id: str) -> list[str]:
        return sorted(self._files(neutral_id))

    def path(self, neutral_id: str, role: str = "file") -> Path:
        """The file of *neutral_id* (and *role*). The result file lists the ids a check opens."""
        files = self._files(neutral_id)
        if role not in files:
            raise KeyError(f"{neutral_id} has no file with the role {role!r}")
        self._write({"kind": "open", "id": neutral_id})
        return self.root / files[role]

    def record(self, neutral_id: str, **values: float) -> None:
        """Record summary numbers (counts, medians, rates) of *neutral_id* in the result file."""
        self._files(neutral_id)
        clean: dict[str, float] = {}
        for name, value in values.items():
            if not METRIC.fullmatch(name):
                raise ValueError(f"{name!r} is not a lower-case metric name")
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                raise ValueError(f"{name}: a summary number must be a real number")
            clean[name] = int(value) if isinstance(value, numbers.Integral) else float(value)
            if not math.isfinite(clean[name]):
                raise ValueError(f"{name}: a summary number must be finite")
        self._write({"kind": "numbers", "id": neutral_id, "numbers": clean})

    def _files(self, neutral_id: str) -> dict[str, str]:
        if neutral_id not in self.mapping:
            raise KeyError(f"{neutral_id!r} is not in {MAPPING_FILE}")
        return self.mapping[neutral_id]

    def _write(self, entry: dict) -> None:
        if self._record is not None:
            append_record(self._record, {"nodeid": self._nodeid, **entry})


# --- Step 1: a clean clone at a commit of main -----------------------------------------------


def _run_bounded(cmd: list[str], cwd: Path, timeout: float | None) -> subprocess.CompletedProcess:
    """``subprocess.run`` with the output in files, so that ``timeout`` also stops the wait.

    After a timeout, ``subprocess.run`` kills the child. On Windows it then reads captured pipes to
    their end, with no limit, and a grandchild that holds a pipe (``git-remote-https``,
    ``git-lfs``) keeps that end away. A file has no end to wait for: the wait stops with the child.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=err, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{' '.join(cmd[:2])} did not finish in {timeout:g} s") from None
        texts = []
        for fh in (out, err):
            fh.seek(0)
            texts.append(fh.read().decode("utf-8", "replace"))
    return subprocess.CompletedProcess(cmd, proc.returncode, *texts)


def _git(cwd: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return _run_bounded(["git", *args], cwd, timeout)


def _git_out(cwd: Path, *args: str, timeout: float | None = None) -> str:
    proc = _git(cwd, *args, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _same_repo(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git")


def prepare_clone(clone: Path, remote: str, commit: str | None = None) -> str:
    """Check out *commit* (default: the tip of ``main``) in *clone*, or raise :class:`Refused`.

    Only ``refs/heads/main`` is fetched, so no pull-request ref ever reaches the clone. Returns
    the 40-hex commit.
    """
    clone = Path(clone)
    if not (clone / ".git").exists():
        clone.mkdir(parents=True, exist_ok=True)
        _git_out(clone, "init", "--quiet")
        _git_out(clone, "remote", "add", "origin", remote)
    elif _same_repo(_git_out(clone, "remote", "get-url", "origin")) != _same_repo(remote):
        raise Refused("the origin of the clone is not the canonical repository")
    if _git_out(clone, "status", "--porcelain", "--untracked-files=all"):
        raise Refused("the clone has local changes; the runner runs only a clean checkout")
    _git_out(
        clone, "fetch", "--quiet", "--no-tags", "origin", MAIN_REFSPEC, timeout=NETWORK_TIMEOUT_S
    )
    tip = _git_out(clone, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}")
    target = tip
    if commit is not None:
        if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise Refused("--commit must be a hexadecimal commit id")
        found = _git(clone, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
        if found.returncode != 0:
            raise Refused(f"commit {commit} is not in the history of origin/main")
        target = found.stdout.strip()
        if _git(clone, "merge-base", "--is-ancestor", target, tip).returncode != 0:
            raise Refused(f"commit {target} is not reachable from origin/main")
    _git_out(clone, "checkout", "--quiet", "--detach", target, timeout=NETWORK_TIMEOUT_S)
    if _git_out(clone, "rev-parse", "HEAD") != target:
        raise Refused("the checkout did not land on the verified commit")
    return target


# --- Step 2: the locked environment ----------------------------------------------------------


def env_python(prefix: Path) -> Path:
    return prefix / "python.exe" if os.name == "nt" else prefix / "bin" / "python"


#: The folders ``conda activate`` puts first on PATH on Windows, in its order. The installed
#: launchers do the same (packaging/scripts/post_install.bat, issue #553): without them MKL can
#: load another program's DLLs and crash at the first BLAS call.
WINDOWS_ACTIVATION_DIRS = (
    "",
    r"Library\mingw-w64\bin",
    r"Library\usr\bin",
    r"Library\bin",
    "Scripts",
    "bin",
)


def activated_env(prefix: Path | None, *, windows: bool = os.name == "nt") -> dict[str, str]:
    """This process's environment with *prefix*'s folders first on PATH, as activation does."""
    env = dict(os.environ)
    if prefix is not None:
        if windows:
            folders = [str(PureWindowsPath(prefix, sub)) for sub in WINDOWS_ACTIVATION_DIRS]
        else:
            folders = [str(Path(prefix) / "bin")]
        env["PATH"] = (";" if windows else os.pathsep).join([*folders, env.get("PATH", "")])
    return env


def _run_logged(cmd: list[str], log, *, check: bool = True, **kwargs) -> int:
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs).returncode
    if check and code != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} {cmd[1]} failed (exit {code}); see the local log")
    return code


def ensure_env(clone: Path, prefix: Path, micromamba: str, log) -> tuple[Path, str]:
    """Rebuild *prefix* from the clone's lock when the lock changed; install the clone's tether."""
    lock = clone / "conda-lock.yml"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    stamp = prefix / LOCK_STAMP
    python, env = env_python(prefix), activated_env(prefix)
    if not (stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest):
        if prefix.exists():
            if not (prefix / "conda-meta").is_dir():
                raise RuntimeError(f"{prefix} is not a conda environment; it is not replaced")
            shutil.rmtree(prefix)
        create = [micromamba, "create", "--yes", "--prefix", str(prefix), "--file", str(lock)]
        _run_logged(create, log)
        _run_logged([str(python), "-m", "pip", "install", *TEST_TOOLS], log, env=env)
        stamp.write_text(digest + "\n", encoding="utf-8")
    install = [str(python), "-m", "pip", "install", "--no-deps", "-e", str(clone)]
    _run_logged(install, log, env=env)
    return python, digest


# --- Steps 3 and 4: the checks and the result file -------------------------------------------


def collect_checks(record: Path) -> list[dict]:
    """One entry per test: worst outcome over its phases, total duration, ids and numbers."""
    checks: dict[str, dict] = {}
    lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
    for number, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
        except ValueError:  # pytest stopped while it wrote this line; never quote the line
            raise RuntimeError(f"the check record is cut off at line {number}") from None
        entry = checks.setdefault(
            rec["nodeid"],
            {"check": rec["nodeid"], "outcome": None, "duration_s": 0.0, "datasets": {}},
        )
        if rec["kind"] == "report":
            entry["duration_s"] = round(entry["duration_s"] + float(rec["duration"]), 3)
            outcome = rec["outcome"]
            if outcome == "failed":
                outcome = "failed" if rec["when"] == "call" else "error"
            if _RANK[outcome] > _RANK[entry["outcome"]]:
                entry["outcome"] = outcome
        else:
            entry["datasets"].setdefault(rec["id"], {}).update(rec.get("numbers", {}))
    for entry in checks.values():
        entry["outcome"] = entry["outcome"] or "error"
    return [checks[k] for k in sorted(checks)]


def tool_versions(python: Path, env: dict[str, str]) -> dict[str, str | None]:
    code = (
        "import importlib.metadata as m, json, platform\n"
        "out = {'python': platform.python_version()}\n"
        f"for name in {TOOL_PACKAGES!r}:\n"
        "    try:\n        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n        out[name] = None\n"
        "print(json.dumps(out))\n"
    )
    cmd = [str(python), "-c", code]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
    return json.loads(proc.stdout)


def _cpu_name() -> str | None:
    if sys.platform == "win32":
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            return str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def _memory_gb() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in ("total", "avail", "pagef", "apagef", "virt", "avirt", "aext")
            ]

        status = _Status()
        status.length = ctypes.sizeof(_Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.total / 2**30, 1)
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20, 1)
    return None


def hardware() -> dict:
    """A summary of the computer. It never holds the host name or a user name."""
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu_name(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
    }


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _matches(pattern: re.Pattern, value: object, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    return isinstance(value, str) and bool(pattern.fullmatch(value))


_HEX64 = re.compile(r"[0-9a-f]{64}")
_HARDWARE = {"os", "machine", "cpu", "cpu_count", "memory_gb"}
_TOP = {"schema", "mode", "status", "commit", "on_main", "started", "finished", "duration_s"}
_TOP |= {"lock_sha256", "tools", "hardware", "checks"}
_CHECK = {"check", "outcome", "duration_s", "datasets"}


def validate_result(result: object) -> list[str]:
    """The problems of a result against the format ``tether-workstation-result/1`` (ADR-0071)."""
    if not isinstance(result, dict) or set(result) != _TOP:
        return [f"the top level must have exactly the keys {sorted(_TOP)}"]
    tools, checks = result["tools"], result["checks"]
    rules = {
        "schema": result["schema"] == RESULT_SCHEMA,
        "mode": result["mode"] in MODES,
        "status": result["status"] in STATUSES,
        "commit": _matches(_HEX40, result["commit"], optional=True),
        "on_main": isinstance(result["on_main"], bool),
        "started": _matches(_STAMP, result["started"]),
        "finished": _matches(_STAMP, result["finished"]),
        "duration_s": _number(result["duration_s"]),
        "lock_sha256": _matches(_HEX64, result["lock_sha256"], optional=True),
        "tools": isinstance(tools, dict) and all(_text(v) for v in tools.values()),
        "hardware": isinstance(result["hardware"], dict) and set(result["hardware"]) <= _HARDWARE,
        "checks": isinstance(checks, list),
    }
    problems = [f"{key} is not valid" for key, ok in rules.items() if not ok]
    if result["mode"] == "dry-run" and result["on_main"] is True:
        problems.append("a dry run never claims a verified commit of main")
    for i, check in enumerate(checks if isinstance(checks, list) else []):
        if not isinstance(check, dict) or set(check) != _CHECK:
            problems.append(f"checks[{i}] must have exactly the keys {sorted(_CHECK)}")
            continue
        if not isinstance(check["check"], str) or check["outcome"] not in OUTCOMES:
            problems.append(f"checks[{i}] has no valid check name or outcome")
        if not _number(check["duration_s"]) or not isinstance(check["datasets"], dict):
            problems.append(f"checks[{i}] has no valid duration_s or datasets")
            continue
        for nid, values in check["datasets"].items():
            if not NEUTRAL_ID.fullmatch(nid) or not isinstance(values, dict):
                problems.append(f"checks[{i}] lists a dataset that is not a neutral id")
            elif not all(METRIC.fullmatch(k) and _number(v) for k, v in values.items()):
                problems.append(f"checks[{i}].{nid} holds a value that is not a summary number")
    return problems


def _strings(value: object):
    """Every key and every string value in *value*, decoded (JSON escaping would hide a name)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def seal(result: dict, tokens: frozenset[str]) -> tuple[dict, str]:
    """Serialize *result*; withhold the checks (then the summaries) when a private name shows."""
    for strip in ((), ("checks",), ("checks", "tools", "hardware")):
        for key in strip:
            result = {**result, key: [] if key == "checks" else {}, "status": "withheld"}
        text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        named = any(token in s for s in _strings(result) for token in tokens)
        if not named and not any(token in text for token in tokens):
            return result, text
    raise RuntimeError("the result still names a private file after it was withheld")


def _utc(stamp: float) -> str:
    return dt.datetime.fromtimestamp(stamp, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def newest_result(results: Path) -> Path | None:
    files = sorted(Path(results).glob("*Z-*.json")) if Path(results).is_dir() else []
    return files[-1] if files else None


@contextlib.contextmanager
def _run_lock(state: Path):
    """One run at a time. The operating system holds the lock for as long as the process lives,
    so a live run keeps it however long it takes and a run that dies releases it."""
    with open(state / "run.lock", "a+b") as fh:
        fh.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another run holds the lock") from None
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def run(args: argparse.Namespace) -> int:
    # Absolute before anything else: pytest runs in another folder and gets these through $ENV.
    state, data = (Path(p).expanduser().resolve() for p in (args.state, args.data))
    results = Path(args.results).expanduser().resolve() if args.results else state / "results"
    # Owner-only on POSIX: the log names files. A --results folder elsewhere holds only
    # sealed results, so it keeps the permissions that its owner gave it.
    # The note names the folder by its role: a path is printed only through say(), which
    # redacts, and the mapping is not read yet.
    private = {state: "the state folder", state / "logs": "logs/"}
    if results == state / "results":
        private[results] = "results/"
    for folder, role in private.items():
        if private_dir(folder):
            print(f"note: removed the group and other permissions of {role}")
    results.mkdir(parents=True, exist_ok=True)
    newest = newest_result(results)
    if args.min_interval_hours and newest is not None:
        age = (time.time() - newest.stat().st_mtime) / 3600
        if age < args.min_interval_hours:
            print(f"skipped: the newest result is {age:.1f} h old")
            return EXIT_OK
    tokens = private_tokens(load_mapping(data))

    def say(line: str) -> None:
        print(redact(line, tokens), flush=True)

    started = time.time()
    stamp = _utc(started).replace("-", "").replace(":", "")
    log_path = state / "logs" / f"{stamp}.log"
    record = state / f"{stamp}.record.jsonl"
    commit, lock_sha, code, checks, tools, status = None, None, None, [], {}, None
    on_main = False  # true only once prepare_clone() has verified the commit
    with _run_lock(state), open_private(log_path) as log:
        try:
            if args.dry_run:
                proc = _git(REPO, "rev-parse", "HEAD")
                commit = proc.stdout.strip() if proc.returncode == 0 else None
                python, source, paths = Path(sys.executable), REPO, [SELF_CHECK]
            else:
                commit = prepare_clone(state / "clone", CANONICAL_REMOTE, args.commit)
                on_main = True
                source, paths = state / "clone", []
                python, lock_sha = ensure_env(source, state / "env", args.micromamba, log)
            say(f"running the workstation checks at {commit or 'an unknown commit'}")
            env = activated_env(None if args.dry_run else state / "env")
            env.update(
                {DATA_ENV: str(data), RECORD_ENV: str(record), "QT_QPA_PLATFORM": "offscreen"}
            )
            cmd = [str(python), "-m", "pytest", "-m", "workstation", "-p", "no:cacheprovider"]
            code = _run_logged([*cmd, "-rA", *paths], log, check=False, cwd=source, env=env)
            checks = collect_checks(record)
            tools = tool_versions(python, env)
        except Refused as exc:
            say(f"refused: {exc}")
            status = "refused"
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            say(f"error: {exc}")
            status = "error"
        finally:
            record.unlink(missing_ok=True)
    finished = time.time()
    if status is None:
        status = "error"
        if code in (0, 1) and checks:
            status = "passed" if all(c["outcome"] == "passed" for c in checks) else "failed"
    result = {
        "schema": RESULT_SCHEMA,
        "mode": "dry-run" if args.dry_run else "scheduled",
        "status": status,
        "commit": commit,
        "on_main": on_main,
        "started": _utc(started),
        "finished": _utc(finished),
        "duration_s": round(finished - started, 1),
        "lock_sha256": lock_sha,
        "tools": tools,
        "hardware": hardware(),
        "checks": checks,
    }
    problems = validate_result(result)
    if problems:
        raise RuntimeError("the result does not match its format: " + "; ".join(problems))
    result, text = seal(result, tokens)
    out = results / f"{stamp}-{(commit or 'none')[:12]}.json"
    out.with_suffix(".tmp").write_text(text, encoding="utf-8")
    os.replace(out.with_suffix(".tmp"), out)
    for check in result["checks"]:
        say(f"  {check['outcome']:<8} {check['check']}  [{', '.join(check['datasets'])}]")
    say(f"status: {result['status']}; result file: {out}")
    say(f"the pytest log stays on this computer: {log_path}")
    exits = {"passed": EXIT_OK, "failed": EXIT_FAILED, "refused": EXIT_REFUSED}
    return {**exits, "withheld": EXIT_WITHHELD}.get(result["status"], EXIT_ERROR)


# --- latest: what an agent reads ---------------------------------------------------------------


def issue_title(check: str) -> str:
    """The one issue title of one check (the one-issue-per-check rule of #697)."""
    return f"test(workstation): {check} failed on main"


def latest(results: Path) -> dict:
    newest = newest_result(results)
    if newest is None:
        return {"result": None}
    data = json.loads(newest.read_text(encoding="utf-8"))
    problems = validate_result(data)
    if problems:
        return {"result": newest.name, "invalid": problems}
    age_hours = round((time.time() - newest.stat().st_mtime) / 3600, 1)
    report = [c for c in data["checks"] if c["outcome"] != "passed"]
    issues = [{"title": issue_title(c["check"]), **c} for c in report]
    passed = [issue_title(c["check"]) for c in data["checks"] if c["outcome"] == "passed"]
    if data["status"] in ("error", "refused", "withheld") or not data["checks"]:
        issues.append({"title": RUN_ISSUE_TITLE, "check": None, "outcome": data["status"]})
    elif data["mode"] == "scheduled" and age_hours <= STALE_HOURS:
        # The scheduled run completed again (whatever its checks did), so an open run-level
        # issue gets its one recovery comment. A failed check has its own issue.
        passed.append(RUN_ISSUE_TITLE)
    return {
        "result": newest.name,
        "mode": data["mode"],
        "status": data["status"],
        "commit": data["commit"],
        "finished": data["finished"],
        "age_hours": age_hours,
        "open_or_update": issues,
        "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="run the workstation checks and write one result file")
    go.add_argument("--state", default=str(Path.home() / ".tether-workstation"),
                    help="the runner's own folder: clone, env, logs, results")  # fmt: skip
    go.add_argument("--data", default=os.environ.get(DATA_ENV), help=f"default: ${DATA_ENV}")
    go.add_argument("--results", help="where result files go (default: STATE/results)")
    go.add_argument("--commit", help="a commit of main to run (default: the tip of main)")
    go.add_argument("--micromamba", default="micromamba", help="the micromamba executable")
    go.add_argument("--min-interval-hours", type=float, default=0.0,
                    help="do nothing when the newest result is younger than this")  # fmt: skip
    go.add_argument("--dry-run", action="store_true", help="the self-check only, with this Python")
    show = sub.add_parser("latest", help="print the newest result as JSON for an agent")
    show.add_argument("--results", required=True)
    args = parser.parse_args(argv)
    if args.command == "latest":
        print(json.dumps(latest(Path(args.results)), indent=2))
        return EXIT_OK
    if not args.data:
        parser.error(f"give --data or set ${DATA_ENV}")
    if args.dry_run and args.commit:
        parser.error("--commit needs a scheduled run; a dry run runs this checkout")
    try:
        return run(args)
    except (MappingError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
