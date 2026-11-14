# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-molecule category and analysis-window setters (#358, PRD §5.1/§7.2/§7.6).

Locks :mod:`tether.project.molecule_edits` and its :class:`~tether.project.Project`
methods against the issue's acceptance criteria: a category persists to
``/molecules.category`` and survives reopen, clearing writes ``""``, a name outside the
condition's category list is refused, a window clamps to ``frame_range`` inside
``[0, n_frames]`` by the SMD-import rule, a reset restores ``frame_range``, a manual
window survives a photobleach pass, every write refuses a foreign lock, and each change
is logged append-only, audit row first. Headless; no schema change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.io.filename import ConditionKey  # noqa: E402
from tether.io.schema import LABELS_DTYPE, MOLECULES_DTYPE, TABLE, create_project  # noqa: E402
from tether.project import Project, lock  # noqa: E402
from tether.project import conditions as C  # noqa: E402
from tether.project import molecule_edits as M  # noqa: E402
from tether.project.analysis_import import _resolve_windows  # noqa: E402
from tether.project.lock import LockedError, LockIdentity  # noqa: E402
from tether.project.photobleach import compute_photobleach  # noqa: E402

HOST_A = LockIdentity(host="HOST-A", user="alice", pid=111)
HOST_B = LockIdentity(host="HOST-B", user="bob", pid=222)
TS = "2026-11-14T12:00:00+00:00"
N_FRAMES = 100
KEY_A = ConditionKey(construct_variant="Tbox", dye="Cy3Cy5")
KEY_B = ConditionKey(construct_variant="Tbox", dye="Cy3Cy5", ligand="tRNA")


def _seed(
    tmp_path: Path,
    specs: list[tuple[str, ConditionKey, tuple[int, int]]],
    *,
    traces: np.ndarray | None = None,
) -> Path:
    """A ``.tether`` with rows ``(molecule_key, condition key, frame_range)``.

    ``analysis_window`` starts equal to ``frame_range`` (the extraction default). The
    conditions are materialized so a category list can be set; ``traces`` (donor and
    acceptor ``(n, N_FRAMES)`` corrected layers) defaults to zeros.
    """
    path = create_project(tmp_path / "exp.tether")
    rows = np.zeros(len(specs), dtype=MOLECULES_DTYPE)
    for field in MOLECULES_DTYPE.names:
        if MOLECULES_DTYPE[field].kind == "O":
            rows[field] = ""
    rows["molecule_id"] = [f"mol-{i}" for i in range(len(specs))]
    rows["molecule_key"] = [key for key, _, _ in specs]
    rows["condition_id"] = [cond.condition_id() for _, cond, _ in specs]
    rows["frame_range"] = [fr for _, _, fr in specs]
    rows["analysis_window"] = [fr for _, _, fr in specs]
    rows["bleach_frames"] = [-1, -1]
    if traces is None:
        traces = np.zeros((2, len(specs), N_FRAMES), dtype=np.float32)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE]
        table.resize((len(specs),))
        table[:] = rows
        f["traces"].create_dataset("donor_corrected", data=traces[0].astype(np.float32))
        f["traces"].create_dataset("acceptor_corrected", data=traces[1].astype(np.float32))
    for cond in {cond for _, cond, _ in specs}:
        C.register_condition(path, cond)
    return path


def _row(path: Path, molecule_key: str) -> np.ndarray:
    with h5py.File(path, "r") as f:
        table = f["molecules"][TABLE][:]
    keys = [k.decode() if isinstance(k, bytes) else k for k in table["molecule_key"]]
    return table[keys.index(molecule_key)]


def _s(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _category(path: Path, molecule_key: str) -> str:
    return _s(_row(path, molecule_key)["category"])


def _window(path: Path, molecule_key: str) -> tuple[int, int]:
    w = _row(path, molecule_key)["analysis_window"]
    return int(w[0]), int(w[1])


# --- category ----------------------------------------------------------------


def test_set_category_persists_and_survives_reopen(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    cid = KEY_A.condition_id()
    C.set_category_list(path, cid, ["static", "dynamic"])

    rows = Project.open(path).set_category("k0", "dynamic", labeler="ana", timestamp=TS)

    assert rows.shape == (1,)
    assert _s(rows[0]["event"]) == "set_category"
    assert _s(rows[0]["old_category"]) == ""
    assert _s(rows[0]["new_category"]) == "dynamic"
    assert _s(rows[0]["condition_id"]) == cid
    assert _s(rows[0]["labeler"]) == "ana"
    assert _s(rows[0]["timestamp"]) == TS
    assert _category(path, "k0") == "dynamic"
    assert Project.open(path).read_molecule_audit().shape == (1,)


def test_set_category_strips_the_name_like_the_category_list(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    M.set_category(path, "k0", "  static ", timestamp=TS)
    assert _category(path, "k0") == "static"


def test_clear_category_writes_empty_string(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    M.set_category(path, "k0", "static", timestamp=TS)

    rows = Project.open(path).clear_category("k0", timestamp=TS)

    assert _category(path, "k0") == ""
    assert _s(rows[0]["event"]) == "clear_category"
    assert _s(rows[0]["old_category"]) == "static"
    assert _s(rows[0]["new_category"]) == ""


def test_category_outside_the_condition_list_is_refused(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES)), ("k1", KEY_B, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    C.set_category_list(path, KEY_B.condition_id(), ["dynamic"])

    with pytest.raises(ValueError, match="not in condition"):
        M.set_category(path, "k0", "dynamic", timestamp=TS)  # another condition's name
    with pytest.raises(ValueError, match="not in condition"):
        M.set_category(path, "k0", "unheard-of", timestamp=TS)
    assert _category(path, "k0") == ""
    assert M.read_molecule_audit(path).shape == (0,)


def test_category_refused_while_the_condition_has_no_list(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    with pytest.raises(ValueError, match="add_category"):
        M.set_category(path, "k0", "static", timestamp=TS)


def test_the_category_list_stays_authoritative_after_removal(tmp_path: Path) -> None:
    """A name removed from the list can no longer be assigned (the list is the vocabulary)."""
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    cid = KEY_A.condition_id()
    proj = Project.open(path)
    proj.set_category_list(cid, ["static", "dynamic"])
    proj.remove_category(cid, "dynamic")
    with pytest.raises(ValueError):
        proj.set_category("k0", "dynamic", timestamp=TS)
    proj.add_category(cid, "dynamic")
    proj.set_category("k0", "dynamic", timestamp=TS)
    assert _category(path, "k0") == "dynamic"


def test_empty_category_is_refused_use_clear(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    with pytest.raises(ValueError, match="clear_category"):
        M.set_category(path, "k0", "   ", timestamp=TS)
    with pytest.raises(TypeError):
        M.set_category(path, "k0", 3, timestamp=TS)  # type: ignore[arg-type]


def test_category_edits_never_touch_labels_or_curation(tmp_path: Path) -> None:
    """``/labels.label_value`` is the accept/reject codec; a category must not enter it."""
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    M.set_category(path, "k0", "static", timestamp=TS)
    M.clear_category(path, "k0", timestamp=TS)
    with h5py.File(path, "r") as f:
        assert f["labels"][TABLE].shape == (0,)
        assert f["labels"][TABLE].dtype == LABELS_DTYPE
    assert int(_row(path, "k0")["curation_label"]) == 0


def test_duplicate_key_with_divergent_conditions_is_refused(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES)), ("k0", KEY_B, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    C.set_category_list(path, KEY_B.condition_id(), ["static"])
    with pytest.raises(ValueError, match="multiple condition_ids"):
        M.set_category(path, "k0", "static", timestamp=TS)
    assert M.read_molecule_audit(path).shape == (0,)


# --- analysis window -----------------------------------------------------------


def test_set_window_persists_and_survives_reopen(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])

    rows = Project.open(path).set_analysis_window("k0", 10, 60, reason="blink", timestamp=TS)

    assert _window(path, "k0") == (10, 60)
    assert _s(rows[0]["event"]) == "set_analysis_window"
    assert tuple(rows[0]["old_window"]) == (0, N_FRAMES)
    assert tuple(rows[0]["new_window"]) == (10, 60)
    assert _s(rows[0]["reason"]) == "blink"


@pytest.mark.parametrize(
    ("frame_range", "request_", "expected"),
    [
        ((0, N_FRAMES), (-20, 50), (0, 50)),  # clamps to 0
        ((0, N_FRAMES), (30, 500), (30, N_FRAMES)),  # clamps to n_frames
        ((10, 80), (0, 50), (10, 50)),  # clamps to the frame_range start
        ((10, 80), (40, 95), (40, 80)),  # clamps to the frame_range stop
        ((10, 80), (85, 99), (10, 80)),  # wholly outside: falls back to frame_range
        ((10, 120), (50, 130), (50, N_FRAMES)),  # a frame_range past n_frames is bounded
    ],
)
def test_window_clamps_to_frame_range_inside_n_frames(
    tmp_path: Path,
    frame_range: tuple[int, int],
    request_: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, frame_range)])
    M.set_analysis_window(path, "k0", *request_, timestamp=TS)
    assert _window(path, "k0") == expected


def test_window_clamp_is_the_smd_import_rule(tmp_path: Path) -> None:
    """With ``frame_range == [0, n_frames]`` the setter and ``_resolve_windows`` agree."""
    requests = [(-5, 40), (20, 300), (-9, -1), (99, 150), (3, 4), (0, N_FRAMES)]
    expected = _resolve_windows(np.array(requests), len(requests), N_FRAMES)
    for (start, stop), want in zip(requests, expected, strict=True):
        case = tmp_path / f"{start}_{stop}"
        case.mkdir()
        path = _seed(case, [("k0", KEY_A, (0, N_FRAMES))])
        M.set_analysis_window(path, "k0", start, stop, timestamp=TS)
        assert _window(path, "k0") == (int(want[0]), int(want[1]))


def test_window_rejects_malformed_requests_before_writing(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    with pytest.raises(ValueError, match="start < stop"):
        M.set_analysis_window(path, "k0", 50, 50, timestamp=TS)
    with pytest.raises(ValueError, match="start < stop"):
        M.set_analysis_window(path, "k0", 60, 10, timestamp=TS)
    with pytest.raises(TypeError):
        M.set_analysis_window(path, "k0", 1.5, 10, timestamp=TS)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        M.set_analysis_window(path, "k0", True, 10, timestamp=TS)
    with pytest.raises(ValueError, match="offset"):
        M.set_analysis_window(path, "k0", 1, 10, timestamp="2026-11-14T12:00:00")
    assert _window(path, "k0") == (0, N_FRAMES)
    assert M.read_molecule_audit(path).shape == (0,)


def test_window_on_a_row_with_no_valid_frames_is_refused(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, 0))])
    with pytest.raises(ValueError, match="no valid frames"):
        M.set_analysis_window(path, "k0", 0, 10, timestamp=TS)


def test_reset_restores_frame_range(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (5, 90))])
    M.set_analysis_window(path, "k0", 20, 40, timestamp=TS)

    rows = Project.open(path).reset_analysis_window("k0", timestamp=TS)

    assert _window(path, "k0") == (5, 90)
    assert _s(rows[0]["event"]) == "reset_analysis_window"
    assert tuple(rows[0]["old_window"]) == (20, 40)
    assert tuple(rows[0]["new_window"]) == (5, 90)


def test_duplicate_key_rows_clamp_to_their_own_frame_range(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, 50)), ("k0", KEY_A, (20, 90))])
    rows = M.set_analysis_window(path, "k0", 10, 70, timestamp=TS)
    with h5py.File(path, "r") as f:
        windows = [tuple(int(v) for v in w) for w in f["molecules"][TABLE]["analysis_window"]]
    assert windows == [(10, 50), (20, 70)]
    assert [_s(m) for m in rows["molecule_id"]] == ["mol-0", "mol-1"]


# --- manual window versus the photobleach auto-default -------------------------


def _bleaching_traces(n: int, bleach_at: int) -> np.ndarray:
    rng = np.random.default_rng(358)
    donor = rng.normal(0.0, 30.0, size=(n, N_FRAMES))
    acceptor = rng.normal(0.0, 30.0, size=(n, N_FRAMES))
    donor[:, :bleach_at] += 1200.0
    acceptor[:, :bleach_at] += 900.0
    return np.stack([donor, acceptor])


def test_manual_window_wins_over_photobleach_and_reset_rearms_it(tmp_path: Path) -> None:
    bleach_at = 70
    path = _seed(
        tmp_path,
        [("manual", KEY_A, (0, N_FRAMES)), ("auto", KEY_A, (0, N_FRAMES))],
        traces=_bleaching_traces(2, bleach_at),
    )
    M.set_analysis_window(path, "manual", 5, 30, timestamp=TS)

    compute_photobleach(path)
    auto_window = _window(path, "auto")
    assert auto_window[0] == 0 and abs(auto_window[1] - bleach_at) <= 2  # auto-default set
    assert _window(path, "manual") == (5, 30)  # the manual override wins

    M.reset_analysis_window(path, "manual", timestamp=TS)
    assert _window(path, "manual") == (0, N_FRAMES)
    compute_photobleach(path)
    assert _window(path, "manual") == auto_window  # back under the auto-default


# --- provenance + no-ops ---------------------------------------------------------


def test_unchanged_edits_write_nothing(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    assert M.clear_category(path, "k0", timestamp=TS).shape == (0,)
    assert M.reset_analysis_window(path, "k0", timestamp=TS).shape == (0,)
    M.set_category(path, "k0", "static", timestamp=TS)
    assert M.set_category(path, "k0", "static", timestamp=TS).shape == (0,)
    M.set_analysis_window(path, "k0", 3, 9, timestamp=TS)
    assert M.set_analysis_window(path, "k0", 3, 9, timestamp=TS).shape == (0,)
    events = [_s(e) for e in M.read_molecule_audit(path)["event"]]
    assert events == ["set_category", "set_analysis_window"]


def test_audit_is_append_only_in_event_order(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static", "dynamic"])
    M.set_category(path, "k0", "static", timestamp=TS)
    first = M.read_molecule_audit(path).copy()
    M.set_category(path, "k0", "dynamic", timestamp=TS)
    M.set_analysis_window(path, "k0", 1, 2, timestamp=TS)
    audit = M.read_molecule_audit(path)
    assert audit.shape == (3,)
    for name in ("event", "molecule_id", "old_category", "new_category", "timestamp"):
        assert _s(audit[0][name]) == _s(first[0][name])  # earlier rows are never rewritten
    assert {_s(e) for e in audit["event"]} <= M.MOLECULE_AUDIT_EVENTS
    assert tuple(audit[0]["new_window"]) == (-1, -1)  # a category row carries no window
    assert _s(audit[2]["new_category"]) == ""  # a window row carries no category
    assert all(_s(v) for v in audit["app_version"])


def test_the_audit_row_is_written_before_the_field(tmp_path: Path, monkeypatch) -> None:
    """A crash between the two writes leaves an orphan audit row, never an unaudited edit."""
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    real_append = M._append_audit

    def append_then_crash(f, rows):
        real_append(f, rows)
        raise RuntimeError("crash after the audit write")

    monkeypatch.setattr(M, "_append_audit", append_then_crash)
    with pytest.raises(RuntimeError):
        M.set_analysis_window(path, "k0", 10, 20, timestamp=TS)
    assert M.read_molecule_audit(path).shape == (1,)
    assert _window(path, "k0") == (0, N_FRAMES)


def test_unknown_molecule_key_raises(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    for call in (
        lambda: M.set_category(path, "nope", "x", timestamp=TS),
        lambda: M.clear_category(path, "nope", timestamp=TS),
        lambda: M.set_analysis_window(path, "nope", 0, 5, timestamp=TS),
        lambda: M.reset_analysis_window(path, "nope", timestamp=TS),
    ):
        with pytest.raises(KeyError):
            call()


def test_a_fresh_project_has_no_molecule_audit(tmp_path: Path) -> None:
    path = create_project(tmp_path / "fresh.tether")
    with h5py.File(path, "r") as f:
        assert "molecule_audit" not in f["settings"]
    assert M.read_molecule_audit(path).shape == (0,)


# --- the single-writer lock -----------------------------------------------------


def test_every_setter_refuses_a_foreign_lock_reads_allowed(tmp_path: Path) -> None:
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    lock.acquire(path, identity=HOST_B)
    proj = Project(path, identity=HOST_A)

    with pytest.raises(LockedError):
        proj.set_category("k0", "static", timestamp=TS)
    with pytest.raises(LockedError):
        proj.clear_category("k0", timestamp=TS)
    with pytest.raises(LockedError):
        proj.set_analysis_window("k0", 1, 5, timestamp=TS)
    with pytest.raises(LockedError):
        proj.reset_analysis_window("k0", timestamp=TS)
    assert proj.read_molecule_audit().shape == (0,)
    assert _category(path, "k0") == ""
    assert _window(path, "k0") == (0, N_FRAMES)


def test_setters_keep_the_owners_retained_lock(tmp_path: Path) -> None:
    """A session that holds the lock can edit, and still holds the same lock after."""
    path = _seed(tmp_path, [("k0", KEY_A, (0, N_FRAMES))])
    C.set_category_list(path, KEY_A.condition_id(), ["static"])
    proj = Project(path, identity=HOST_A)
    held = proj.acquire_lock()

    proj.set_category("k0", "static", timestamp=TS)
    proj.set_analysis_window("k0", 2, 8, timestamp=TS)
    proj.reset_analysis_window("k0", timestamp=TS)
    proj.clear_category("k0", timestamp=TS)

    owner = proj.lock_owner()
    assert owner is not None and owner.nonce == held.nonce
    assert proj.release_lock()
