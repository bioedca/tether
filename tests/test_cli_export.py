# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether export table|traces|subset`` (#658, FR-EXPORT §7.9, PRD §4.2 CLI).

Each form must write what its ``tether.project.export`` function writes for the same
arguments, apart from time stamps, so every parity test here runs the function into one
folder and the command into another and compares the two. The rest pins what only the
command adds: its provenance file, the read-only promise (source project and its lock
file unchanged, even while another user holds the lock), the ``--overwrite`` refusal,
the refusal to write over the source, output naming for several projects, and the exit
codes 0/1/2.

Headless (no Qt); gated on ``h5py`` like the export tests it mirrors.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.cli_export import INTENSITY_QUANTITIES, TRACES_SUFFIX  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.project.export import (  # noqa: E402
    export_deeplasi_txt,
    export_molecule_table_csv,
    export_subset_tether,
)
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS  # noqa: E402

_SIDECAR = ".provenance.json"

#: The one subset root attribute that is a time stamp (the sidecar's is ``created_utc``).
_SUBSET_TIME_ATTR = "tether_subset_created_utc"


def _states() -> np.ndarray:
    """Four molecules over twelve frames, with transitions (three states)."""
    return np.array(
        [
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0, 0],
            [2, 2, 1, 1, 0, 0, 0, 1, 1, 2, 2, 2],
        ],
        dtype="int64",
    )


@pytest.fixture
def source(tmp_path):
    """A ``src/exp.tether`` with four molecules, the second one rejected, and its keys."""
    folder = tmp_path / "src"
    folder.mkdir()
    project, keys = build_store_with_model(
        folder, _states(), MEANS, rejected=[False, True, False, False]
    )
    return project.path, keys


def _sidecar(path) -> dict:
    data = json.loads((path.parent / (path.name + _SIDECAR)).read_text(encoding="utf-8"))
    data.pop("created_utc")
    return data


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value(value: object) -> object:
    """A comparable form of an HDF5 value: object (string) arrays by their items."""
    array = np.asarray(value)
    if array.dtype.hasobject:
        return repr(array.tolist())
    return (str(array.dtype), array.shape, array.tobytes())


def _h5_contents(path) -> dict[str, object]:
    """Every dataset value and attribute of an HDF5 file, time stamp left out."""
    contents: dict[str, object] = {}

    def visit(name: str, obj: object) -> None:
        for key, value in obj.attrs.items():
            contents[f"{name}@{key}"] = _value(value)
        if isinstance(obj, h5py.Dataset):
            contents[name] = _value(obj[()])

    with h5py.File(path, "r") as f:
        for key, value in f.attrs.items():
            if key != _SUBSET_TIME_ATTR:
                contents[f"/@{key}"] = _value(value)
        f.visititems(visit)
    return contents


# --------------------------------------------------------------------------- #
# Parity with the functions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("flags", "kwargs"),
    [
        ([], {}),
        (["--exclude-rejected"], {"include_rejected": False}),
        (["--intensity-quantity", "raw"], {"intensity_quantity": "raw"}),
    ],
)
def test_table_equals_the_function_output(source, tmp_path, capsys, flags, kwargs) -> None:
    path, _ = source
    expected = export_molecule_table_csv(path, tmp_path / "expected.csv", **kwargs)
    out = tmp_path / "molecules.csv"

    assert main(["export", "table", str(path), "-o", str(out), *flags]) == 0

    assert out.read_bytes() == expected.path.read_bytes()
    assert _sidecar(out) == _sidecar(expected.path)
    assert capsys.readouterr().out == (f"Exported {expected.n_molecules} molecule(s) -> {out}\n")


@pytest.mark.parametrize(
    ("flags", "kwargs"),
    [
        ([], {}),
        (["--include-rejected"], {"include_rejected": True}),
        (["--intensity-quantity", "raw"], {"intensity_quantity": "raw"}),
    ],
)
def test_traces_equal_the_function_output(source, tmp_path, flags, kwargs) -> None:
    path, _ = source
    expected = export_deeplasi_txt(path, tmp_path / "expected.txt", **kwargs)
    folder = tmp_path / "traces"

    assert main(["export", "traces", str(path), "-o", str(folder), *flags]) == 0

    out = folder / f"exp{TRACES_SUFFIX}"
    assert out.read_bytes() == expected.path.read_bytes()
    assert _sidecar(out) == _sidecar(expected.path)


def test_traces_molecule_keys_map_to_molecule_keys(source, tmp_path) -> None:
    path, keys = source
    chosen = [keys[3], keys[0]]
    expected = export_deeplasi_txt(path, tmp_path / "expected.txt", molecule_keys=chosen)
    folder = tmp_path / "traces"
    argv = ["export", "traces", str(path), "-o", str(folder)]

    assert main([*argv, "--molecule-key", chosen[0], "--molecule-key", chosen[1]]) == 0

    out = folder / f"exp{TRACES_SUFFIX}"
    assert out.read_bytes() == expected.path.read_bytes()
    assert _sidecar(out)["parameters"]["molecule_keys"] == chosen


@pytest.mark.parametrize(
    ("flags", "kwargs"),
    [
        ([], {}),
        (["--include-rejected"], {"include_rejected": True}),
        (["--include-raw"], {"include_raw": True}),
    ],
)
def test_subset_equals_the_function_output(source, tmp_path, flags, kwargs) -> None:
    path, _ = source
    expected = export_subset_tether(path, tmp_path / "expected.tether", **kwargs)
    out = tmp_path / "subset.tether"

    assert main(["export", "subset", str(path), "-o", str(out), *flags]) == 0

    assert _h5_contents(out) == _h5_contents(expected.path)
    assert _sidecar(out) == _sidecar(expected.path)


def test_subset_molecule_keys_map_to_molecule_keys(source, tmp_path) -> None:
    path, keys = source
    expected = export_subset_tether(path, tmp_path / "expected.tether", molecule_keys=[keys[2]])
    out = tmp_path / "subset.tether"

    assert main(["export", "subset", str(path), "-o", str(out), "--molecule-key", keys[2]]) == 0

    assert _h5_contents(out) == _h5_contents(expected.path)
    assert _sidecar(out)["parameters"]["n_molecules"] == 1


def test_intensity_quantity_choices_are_the_library_keys() -> None:
    """The parser's literal choices are the layers the export functions accept."""
    assert set(INTENSITY_QUANTITIES) == set(INTENSITY_QUANTITY_LAYERS)


# --------------------------------------------------------------------------- #
# Provenance and the read-only promise
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("form", "name", "kind"),
    [
        ("table", "molecules.csv", "molecule-table-csv"),
        ("traces", f"exp{TRACES_SUFFIX}", "deeplasi-txt"),
        ("subset", "subset.tether", "subset-tether"),
    ],
)
def test_each_form_writes_its_provenance_file(source, tmp_path, form, name, kind) -> None:
    path, _ = source
    out = tmp_path / "out"
    target = out if form == "traces" else out / name
    out.mkdir()

    assert main(["export", form, str(path), "-o", str(target)]) == 0

    stamp = json.loads((out / (name + _SIDECAR)).read_text(encoding="utf-8"))
    assert stamp["tether_export"] == kind
    assert stamp["source_project"] == "exp.tether"
    assert stamp["created_utc"]


@pytest.mark.parametrize("form", ["table", "traces", "subset"])
def test_the_source_and_its_lock_file_are_unchanged(source, tmp_path, form) -> None:
    """Export reads a project another user holds and leaves it and its lock alone."""
    path, _ = source
    holder = lock.LockInfo(
        host="another-host",
        user="another-user",
        pid=4242,
        timestamp="2026-11-14T00:00:00+00:00",
        nonce="held-by-someone-else",
    )
    lock_file = lock.lock_path(path)
    lock_file.write_text(json.dumps(holder.to_dict(), indent=2), encoding="utf-8")
    before = (_digest(path), _digest(lock_file), os.stat(path).st_mtime_ns)
    out = tmp_path / "out"
    target = out if form == "traces" else out / f"export.{form}"
    out.mkdir()

    assert main(["export", form, str(path), "-o", str(target)]) == 0

    assert (_digest(path), _digest(lock_file), os.stat(path).st_mtime_ns) == before
    assert lock.read_lock(path) == holder
    assert sorted(p.name for p in path.parent.iterdir()) == ["exp.tether", lock_file.name]


# --------------------------------------------------------------------------- #
# Overwrite and the source refusal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("form", ["table", "traces", "subset"])
def test_an_existing_output_is_kept_without_overwrite(source, tmp_path, capsys, form) -> None:
    path, _ = source
    out = tmp_path / "out"
    out.mkdir()
    target = out if form == "traces" else out / f"export.{form}"
    written = out / f"exp{TRACES_SUFFIX}" if form == "traces" else target
    written.write_bytes(b"keep me")
    argv = ["export", form, str(path), "-o", str(target)]

    assert main(argv) == 1
    assert written.read_bytes() == b"keep me"
    assert "already exists; pass --overwrite to replace it" in capsys.readouterr().err

    assert main([*argv, "--overwrite"]) == 0
    assert written.read_bytes() != b"keep me"


def test_an_existing_provenance_file_is_kept_without_overwrite(source, tmp_path) -> None:
    path, _ = source
    out = tmp_path / "molecules.csv"
    sidecar = tmp_path / ("molecules.csv" + _SIDECAR)
    sidecar.write_text("{}", encoding="utf-8")

    assert main(["export", "table", str(path), "-o", str(out)]) == 1
    assert not out.exists()
    assert sidecar.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("form", ["table", "subset"])
def test_a_destination_equal_to_the_source_is_refused(source, capsys, form) -> None:
    path, _ = source
    before = _digest(path)

    assert main(["export", form, str(path), "-o", str(path), "--overwrite"]) == 1

    assert _digest(path) == before
    err = capsys.readouterr().err
    assert f"{form} out_path {path} is the source project; refusing to overwrite" in err


def test_the_subset_refusal_is_the_function_message(source, capsys) -> None:
    path, _ = source
    with pytest.raises(ValueError) as excinfo:
        export_subset_tether(path, path, overwrite=True)

    assert main(["export", "subset", str(path), "-o", str(path), "--overwrite"]) == 1

    assert capsys.readouterr().err == f"tether export subset: {excinfo.value}\n"


def test_an_export_may_not_replace_another_input_project(source, tmp_path, capsys) -> None:
    """With ``--overwrite``, ``exp``'s subset must not replace ``exp-subset.tether`` given as
    a second input: that would destroy the input and then export the replacement."""
    path, _ = source
    other = _second(tmp_path, name="exp-subset.tether")
    folder = other.parent
    before = (_digest(path), _digest(other))

    argv = ["export", "subset", str(path), str(other), "-o", str(folder), "--overwrite"]
    assert main(argv) == 1

    assert (_digest(path), _digest(other)) == before
    assert not (folder / "exp-subset-subset.tether").exists()
    assert not (folder / ("exp-subset.tether" + _SIDECAR)).exists()
    assert f"is the input project {other}; refusing to overwrite it" in capsys.readouterr().err


@pytest.mark.parametrize("form", ["table", "subset"])
def test_an_export_may_not_replace_a_project_lock_file(source, tmp_path, form, capsys) -> None:
    """``-o exp.tether.lock --overwrite`` must not truncate another user's lock record."""
    path, _ = source
    holder = lock.LockInfo(
        host="another-host",
        user="another-user",
        pid=4242,
        timestamp="2026-11-14T00:00:00+00:00",
        nonce="held-by-someone-else",
    )
    lock_file = lock.lock_path(path)
    lock_file.write_text(json.dumps(holder.to_dict(), indent=2), encoding="utf-8")
    before = _digest(lock_file)

    argv = ["export", form, str(path), "-o", str(lock_file), "--overwrite"]
    assert main(argv) == 1

    assert _digest(lock_file) == before
    assert lock.read_lock(path) == holder
    assert not lock_file.with_name(lock_file.name + _SIDECAR).exists()
    assert "is the lock file of the input project" in capsys.readouterr().err


def test_a_hard_linked_alias_of_another_input_is_refused(source, tmp_path) -> None:
    path, _ = source
    other = _second(tmp_path)
    folder = tmp_path / "exports"
    folder.mkdir()
    try:
        os.link(other, folder / "exp.csv")
    except OSError:  # pragma: no cover - a file system without hard links
        pytest.skip("hard links are not available here")
    before = _digest(other)

    argv = ["export", "table", str(path), str(other), "-o", str(folder), "--overwrite"]
    assert main(argv) == 1
    assert _digest(other) == before
    assert not (folder / "other.csv").exists()


def test_a_hard_linked_alias_of_the_source_is_refused(source, tmp_path) -> None:
    path, _ = source
    alias = tmp_path / "alias.csv"
    try:
        os.link(path, alias)
    except OSError:  # pragma: no cover - a file system without hard links
        pytest.skip("hard links are not available here")
    before = _digest(path)

    assert main(["export", "table", str(path), "-o", str(alias), "--overwrite"]) == 1
    assert _digest(path) == before


# --------------------------------------------------------------------------- #
# Several projects
# --------------------------------------------------------------------------- #


def _second(tmp_path, name: str = "other.tether"):
    folder = tmp_path / "second"
    folder.mkdir(exist_ok=True)
    project, _ = build_store_with_model(folder, _states(), MEANS, name=name)
    return project.path


@pytest.mark.parametrize(
    ("form", "names"),
    [
        ("table", ["exp.csv", "other.csv"]),
        ("traces", [f"exp{TRACES_SUFFIX}", f"other{TRACES_SUFFIX}"]),
        ("subset", ["exp-subset.tether", "other-subset.tether"]),
    ],
)
def test_several_projects_go_into_a_folder_named_after_each(
    source, tmp_path, capsys, form, names
) -> None:
    path, _ = source
    other = _second(tmp_path)
    folder = tmp_path / "exports" / "new"

    assert main(["export", form, str(path), str(other), "-o", str(folder)]) == 0

    for name in names:
        assert (folder / name).is_file()
        assert (folder / (name + _SIDECAR)).is_file()
    printed = capsys.readouterr().out.splitlines()
    assert [line.rsplit(" -> ", 1)[1] for line in printed] == [str(folder / n) for n in names]


def test_one_project_into_an_existing_folder_is_named_after_it(source, tmp_path) -> None:
    path, _ = source
    folder = tmp_path / "exports"
    folder.mkdir()

    assert main(["export", "table", str(path), "-o", str(folder)]) == 0
    assert (folder / "exp.csv").is_file()


def test_a_failed_project_does_not_stop_the_others(source, tmp_path, capsys) -> None:
    path, _ = source
    broken = tmp_path / "broken.tether"
    broken.write_bytes(b"not an HDF5 file")
    folder = tmp_path / "exports"

    assert main(["export", "table", str(broken), str(path), "-o", str(folder)]) == 1

    assert (folder / "exp.csv").is_file()
    assert not (folder / "broken.csv").exists()
    assert f"tether export table: {broken}: " in capsys.readouterr().err


def test_projects_with_the_same_name_are_refused_with_code_2(source, tmp_path, capsys) -> None:
    path, _ = source
    twin = _second(tmp_path, name="exp.tether")
    folder = tmp_path / "exports"

    assert main(["export", "table", str(path), str(twin), "-o", str(folder)]) == 2

    assert not folder.exists()
    assert "both map to" in capsys.readouterr().err


def test_names_that_differ_only_in_case_are_refused_with_code_2(source, tmp_path) -> None:
    """``EXP.csv`` and ``exp.csv`` are one file on Windows and macOS."""
    path, _ = source
    twin = _second(tmp_path, name="EXP.tether")

    assert main(["export", "table", str(path), str(twin), "-o", str(tmp_path / "out")]) == 2


def test_an_output_folder_that_cannot_be_made_fails_with_code_1(source, tmp_path, capsys) -> None:
    path, _ = source
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder", encoding="utf-8")

    assert main(["export", "traces", str(path), "-o", str(blocker / "traces")]) == 1
    assert capsys.readouterr().err.startswith("tether export traces: ")


@pytest.mark.parametrize("form", ["table", "traces", "subset"])
def test_an_unreadable_output_path_fails_with_code_1(source, tmp_path, capsys, form) -> None:
    """An error while checking ``-o`` is one stderr line and exit 1, never a traceback.

    A path component longer than the file system allows makes ``Path.is_dir`` raise
    ``OSError`` (``ENAMETOOLONG``) before any export starts.
    """
    path, _ = source
    output = tmp_path / ("x" * 300) / "out"

    assert main(["export", form, str(path), "-o", str(output)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"tether export {form}: ")
    assert len(err.strip().splitlines()) == 1


def test_several_projects_and_an_output_file_is_refused_with_code_2(source, tmp_path) -> None:
    path, _ = source
    other = _second(tmp_path)
    existing = tmp_path / "molecules.csv"
    existing.write_text("x", encoding="utf-8")

    assert main(["export", "table", str(path), str(other), "-o", str(existing)]) == 2
    assert existing.read_text(encoding="utf-8") == "x"


def test_traces_refuses_a_txt_output_name_with_code_2(source, tmp_path, capsys) -> None:
    path, _ = source
    target = tmp_path / "movie-donc-accc-w.txt"

    assert main(["export", "traces", str(path), "-o", str(target)]) == 2

    assert not target.exists()
    assert "names a folder" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #


def test_an_unknown_molecule_key_fails_with_code_1(source, tmp_path, capsys) -> None:
    path, _ = source
    out = tmp_path / "subset.tether"

    assert main(["export", "subset", str(path), "-o", str(out), "--molecule-key", "nope"]) == 1

    assert not out.exists()
    err = capsys.readouterr().err
    assert err.startswith("tether export subset: no molecule with molecule_key(s)")


def test_a_missing_project_fails_with_code_1(tmp_path, capsys) -> None:
    out = tmp_path / "molecules.csv"

    assert main(["export", "table", str(tmp_path / "absent.tether"), "-o", str(out)]) == 1

    assert not out.exists()
    assert "is not a readable .tether HDF5 project" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["export"],
        ["export", "table", "exp.tether"],
        ["export", "pictures", "exp.tether", "-o", "x"],
        ["export", "table", "exp.tether", "-o", "x", "--molecule-key", "k"],
        ["export", "traces", "exp.tether", "-o", "x", "--intensity-quantity", "bright"],
    ],
)
def test_usage_errors_exit_2(argv) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2


def test_export_warns_about_a_onedrive_conflict_copy_of_the_source(source, tmp_path, capsys):
    # #560: an export reads the source project, so it names a sync-conflict sibling too.
    path, _ = source
    copy = path.with_name(f"{path.stem} (1){path.suffix}")
    copy.write_bytes(b"copy")

    assert main(["export", "table", str(path), "-o", str(tmp_path / "m.csv")]) == 0

    err = capsys.readouterr().err
    assert (
        f"  warning: 1 possible OneDrive/SharePoint conflict copy beside {path.name}: "
        f"{copy.name}." in err
    )
    assert copy.read_bytes() == b"copy"

    copy.unlink()
    assert main(["export", "table", str(path), "-o", str(tmp_path / "n.csv")]) == 0
    assert "conflict" not in capsys.readouterr().err
