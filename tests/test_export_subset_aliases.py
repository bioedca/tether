# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The subset export's copy for sharing: ``curator_alias`` (#620, PRD §7.9).

Headless (no Qt). A ``.tether`` records the curator's user name in two places: the
``labeler`` of every ``/labels`` row and of every ``/settings/condition_audit`` row.
With ``curator_alias`` the subset replaces each distinct name with a stable alias,
records that it did so and how many names there were, and keeps no source name in
its bytes. Without it the output is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import MEANS, build_store_with_model, to_str  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.io import schema  # noqa: E402
from tether.io.filename import ConditionKey  # noqa: E402
from tether.project import conditions as C  # noqa: E402
from tether.project import export as E  # noqa: E402
from tether.project.export import USER_NAME_FIELDS, export_subset_tether  # noqa: E402
from tether.project.labels import default_labeler  # noqa: E402

#: Distinct, unmistakable names, so a byte search cannot match by chance.
ALICE = "alice-zq7-curator"
BOB = "bob-xk4-curator"

#: A compound field whose name says it records a person. A field that matches must be
#: in USER_NAME_FIELDS, or the alias option would let that person's name through.
_PERSON_FIELD = re.compile(r"label(l)?er|approv|confirm|review|curator|user|operator|author|_by$")


def _state_matrix() -> np.ndarray:
    return np.array(
        [
            [0, 0, 0, 0, 1, 1, 1, 1],
            [1, 1, 1, 1, 0, 0, 0, 0],
            [0, 1, 0, 1, 0, 1, 0, 1],
        ],
        dtype="int64",
    )


def _source(tmp_path):
    """A source with labels by two curators plus the OS login, and one audit row by Bob."""
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler=ALICE)
    project.reject(keys[1], labeler=BOB)
    project.accept(keys[1], labeler=ALICE)
    project.accept(keys[2])  # default_labeler(): the OS login of the test run
    project.sync_conditions()
    from_id = to_str(read_molecules(project.path)["condition_id"][0])
    C.rekey_condition(
        project.path, from_id, ConditionKey(construct_variant="wt", dye="cy3cy5"), labeler=BOB
    )
    return project, keys


def _audit_labelers(path) -> list[str]:
    with h5py.File(path, "r") as f:
        return [to_str(v) for v in f["settings"]["condition_audit"]["labeler"]]


def _label_labelers(path) -> list[str]:
    with h5py.File(path, "r") as f:
        return [to_str(v) for v in f["labels"][schema.TABLE]["labeler"]]


def _every_stored_string(path) -> set[str]:
    """Every string value in the file: compound string fields, string datasets, attrs."""
    values: set[str] = set()

    def add_attrs(obj) -> None:
        for val in obj.attrs.values():
            if isinstance(val, (bytes, str)):
                values.add(to_str(val))

    def visit(_name, obj) -> None:
        add_attrs(obj)
        if not isinstance(obj, h5py.Dataset):
            return
        data = obj[()]
        if obj.dtype.names:
            for field in obj.dtype.names:
                if obj.dtype[field].kind in "OSU":
                    values.update(to_str(v) for v in np.ravel(data[field]))
        elif obj.dtype.kind in "OSU":
            values.update(to_str(v) for v in np.ravel(data))

    with h5py.File(path, "r") as f:
        add_attrs(f)
        f.visititems(visit)
    return values


def test_names_are_replaced_by_stable_aliases_in_first_met_order(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, include_rejected=True, curator_alias="curator")

    os_user = default_labeler()
    assert _label_labelers(project.path) == [ALICE, BOB, ALICE, os_user]
    assert _label_labelers(out) == ["curator-1", "curator-2", "curator-1", "curator-3"]
    # The audit log uses the same map: Bob is curator-2 there too.
    assert _audit_labelers(out) == ["curator-2"]


def test_no_stored_value_equals_a_source_name(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, include_rejected=True, curator_alias="curator")

    names = {ALICE, BOB, default_labeler()}
    assert not names & _every_stored_string(out)


def test_raw_bytes_of_an_aliased_copy_hold_no_source_name(tmp_path):
    project, _ = _source(tmp_path)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    export_subset_tether(project, shared, include_rejected=True, curator_alias="curator")

    os_user = default_labeler().encode()
    for name in (ALICE.encode(), BOB.encode(), os_user):
        assert name in plain.read_bytes()  # the search can see a name when it is there
        assert name not in shared.read_bytes()
        sidecar = shared.with_name(shared.name + ".provenance.json")
        assert name not in sidecar.read_bytes()


def test_provenance_records_the_count_and_no_name(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    result = export_subset_tether(project, out, include_rejected=True, curator_alias="curator")

    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["names_aliased"] is True
    assert params["n_aliased_names"] == 3
    assert params["curator_alias"] == "curator"
    with h5py.File(out, "r") as f:
        assert int(f.attrs["tether_subset_names_aliased"]) == 1
        assert int(f.attrs["tether_subset_n_aliased_names"]) == 3


def test_count_covers_only_names_in_the_copy(tmp_path):
    """A name whose labels stay behind (a rejected molecule) is not counted or aliased."""
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler=ALICE)
    project.reject(keys[1], labeler=BOB)
    out = tmp_path / "shared.tether"

    result = export_subset_tether(project, out, curator_alias="curator")

    assert _label_labelers(out) == ["curator-1"]
    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["n_aliased_names"] == 1


def test_an_alias_never_equals_a_source_name(tmp_path):
    """Re-sharing a copy whose names already look like aliases cannot collide."""
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler=ALICE)
    project.accept(keys[1], labeler="curator-1")
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, curator_alias="curator")

    assert _label_labelers(out) == ["curator-2", "curator-3"]


def test_no_alias_contains_a_source_name(tmp_path):
    """Codex P1 on #1067: ``curator-1`` in the source must not reappear in ``curator-10``."""
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler="curator-1")
    project.accept(keys[1], labeler=ALICE)
    project.accept(keys[2], labeler=BOB)
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, curator_alias="curator")

    aliases = _label_labelers(out)
    assert len(set(aliases)) == 3
    assert not any("curator-1" in a for a in aliases)
    assert b"curator-1" not in out.read_bytes()


def test_aliases_skip_every_number_that_holds_a_source_name():
    """Twelve names, one of them ``curator-1``: the aliases skip 1 and 10 to 19."""
    names = ["curator-1"] + [f"person-{i}" for i in range(11)]

    aliases = E._CuratorAliases("curator", names)

    written = [aliases._map[n] for n in names]
    assert len(set(written)) == len(names)
    assert not any(src in a for src in names for a in written)


def test_a_prefix_that_holds_a_source_name_is_refused(tmp_path):
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler=ALICE)
    out = tmp_path / "shared.tether"

    with pytest.raises(ValueError, match="curator_alias"):
        export_subset_tether(project, out, curator_alias=f"team-{ALICE}")
    assert not out.exists()


def test_an_empty_labeler_stays_empty(tmp_path):
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler="")
    project.accept(keys[1], labeler=ALICE)
    out = tmp_path / "shared.tether"

    result = export_subset_tether(project, out, curator_alias="curator")

    assert _label_labelers(out) == ["", "curator-1"]
    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["n_aliased_names"] == 1


def test_the_source_project_is_not_changed(tmp_path):
    project, _ = _source(tmp_path)
    before = hashlib.sha256(project.path.read_bytes()).hexdigest()

    export_subset_tether(project, tmp_path / "shared.tether", curator_alias="curator")

    assert hashlib.sha256(project.path.read_bytes()).hexdigest() == before


def test_the_rest_of_the_audit_log_is_kept(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, curator_alias="curator")

    with h5py.File(project.path, "r") as f:
        src = f["settings"]["condition_audit"][()]
        src_ds = f["settings"]["condition_audit"]
        src_maxshape = src_ds.maxshape
    with h5py.File(out, "r") as f:
        dst_ds = f["settings"]["condition_audit"]
        dst = dst_ds[()]
        assert dst_ds.maxshape == src_maxshape  # still appendable
        assert "extraction" in f["settings"]  # the rest of /settings travels
    for field in src.dtype.names:
        if field != "labeler":
            assert [to_str(v) for v in dst[field]] == [to_str(v) for v in src[field]]


def test_option_off_writes_the_same_bytes(tmp_path, monkeypatch):
    """Default and an explicit ``None`` give one file; names stay as recorded."""
    project, _ = _source(tmp_path)

    class _FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 - same signature as datetime.now
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    monkeypatch.setattr(E, "datetime", _FixedClock)
    a = tmp_path / "a.tether"
    b = tmp_path / "b.tether"

    export_subset_tether(project, a, include_rejected=True)
    export_subset_tether(project, b, include_rejected=True, curator_alias=None)

    assert a.read_bytes() == b.read_bytes()
    assert _label_labelers(a) == _label_labelers(project.path)
    assert _audit_labelers(a) == _audit_labelers(project.path)
    with h5py.File(a, "r") as f:
        assert "tether_subset_names_aliased" not in f.attrs
    params = json.loads(a.with_name(a.name + ".provenance.json").read_text(encoding="utf-8"))
    assert "names_aliased" not in params["parameters"]


@pytest.mark.parametrize("bad", ["", "   "])
def test_a_blank_alias_is_refused(tmp_path, bad):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    with pytest.raises(ValueError, match="curator_alias"):
        export_subset_tether(project, out, curator_alias=bad)
    assert not out.exists()


def test_every_field_that_names_a_person_is_aliased(tmp_path):
    """Guard: a compound field that records a person must be in USER_NAME_FIELDS.

    Scans the schema's table dtypes, the audit-log dtype, and every compound dataset of
    a populated store, so a later approver or reviewer field fails here until the alias
    option covers it (the #890 plan change: no approver name in the copy).
    """
    project, _ = _source(tmp_path)
    dtypes = [getattr(schema, n) for n in dir(schema) if n.endswith("_DTYPE") and n.isupper()] + [
        C._audit_dtype()
    ]

    def collect(_name, obj) -> None:
        if isinstance(obj, h5py.Dataset) and obj.dtype.names:
            dtypes.append(obj.dtype)

    with h5py.File(project.path, "r") as f:
        f.visititems(collect)
    person_fields = {
        field for dt in dtypes for field in (dt.names or ()) if _PERSON_FIELD.search(field)
    }
    assert person_fields  # the scan sees labeler, so it is looking in the right place
    assert person_fields <= USER_NAME_FIELDS
