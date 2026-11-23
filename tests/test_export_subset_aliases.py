# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The subset export's copy for sharing: ``curator_alias`` (#620, PRD §7.9).

Headless (no Qt). With ``curator_alias`` the subset replaces each distinct user
name in the project's structured records with a stable alias. The provenance
records that aliasing was used and how many names were replaced.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
from dataclasses import replace
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
from tether.project import correction_states as CS  # noqa: E402
from tether.project import export as E  # noqa: E402
from tether.project.export import (  # noqa: E402
    JSON_USER_KEYS,
    PATH_ATTRS,
    export_subset_tether,
)
from tether.project.labels import default_labeler  # noqa: E402

#: Distinct, unmistakable names, so a byte search cannot match by chance.
ALICE = "alice-zq7-curator"
BOB = "bob-xk4-curator"
FIXTURE_LOGIN = "lab-user-yj8"

#: A compound field whose name says it can record a person.
_PERSON_FIELD = re.compile(
    r"label(l)?er|approv|confirm|review|curator|user|operator|author|_by$|^actor$"
)

#: An attribute whose name says it stores a file or folder path. One that matches must be
#: in PATH_ATTRS, or the alias option would let a home folder (a user name) through.
_PATH_ATTR = re.compile(r"path|dir|folder|home")

#: A folder name that a byte search cannot match by chance.
_HOME = "dana-pq9-home"
CAROL = "carol-vb2-curator"
DAVE = "dave-mw3-curator"


@pytest.fixture(autouse=True)
def _login(monkeypatch, request):
    """Use a stable login unless a raw-byte case selects another one."""
    login = getattr(request, "param", FIXTURE_LOGIN)
    if login is not None:
        monkeypatch.setattr(getpass, "getuser", lambda: login)


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
    """A source with labels by two curators plus the default labeler, and one audit row by Bob."""
    project, keys = build_store_with_model(tmp_path, _state_matrix(), MEANS)
    project.accept(keys[0], labeler=ALICE)
    project.reject(keys[1], labeler=BOB)
    project.accept(keys[1], labeler=ALICE)
    project.accept(keys[2])  # resolves through default_labeler()
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

    assert _label_labelers(project.path) == [ALICE, BOB, ALICE, FIXTURE_LOGIN]
    assert _label_labelers(out) == ["curator-1", "curator-2", "curator-1", "curator-3"]
    # The audit log uses the same map: Bob is curator-2 there too.
    assert _audit_labelers(out) == ["curator-2"]


def test_no_stored_value_equals_a_source_name(tmp_path):
    project, _ = _source(tmp_path)
    out = tmp_path / "shared.tether"

    export_subset_tether(project, out, include_rejected=True, curator_alias="curator")

    names = {ALICE, BOB, FIXTURE_LOGIN}
    assert not names & _every_stored_string(out)


@pytest.mark.parametrize(
    "_login", [None, "curator"], ids=["os-login", "curator-login"], indirect=True
)
def test_raw_bytes_of_an_aliased_copy_hold_no_source_name(tmp_path):
    default_login = default_labeler()
    prefix = "nym" if default_login in "curator-" else "curator"
    project, _ = _source(tmp_path)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    result = export_subset_tether(project, shared, include_rejected=True, curator_alias=prefix)

    assert _label_labelers(plain)[-1] == default_login
    plain_bytes = plain.read_bytes()
    shared_bytes = shared.read_bytes()
    # The fixed JSON key itself contains "curator".
    provenance_bytes = result.provenance_path.read_bytes().replace(b'"curator_alias":', b"")
    for name in (ALICE, BOB, default_login):
        encoded = name.encode()
        assert encoded in plain_bytes  # the search can see a name when it is there
        assert encoded not in shared_bytes
        assert encoded not in provenance_bytes


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
    """Guard: discover every compound field that can record a person.

    Scans the schema's table dtypes, the audit-log dtype, and every compound dataset of
    a populated store, including lazily written logs. A later approver or reviewer
    field fails here until the alias option covers it.
    """
    project, _ = _source(tmp_path)
    _record_takeovers(project.path)  # written lazily, so the scan must see one
    _record_jobs(project)  # likewise
    _record_correction_transitions(project)
    dtypes = [getattr(schema, n) for n in dir(schema) if n.endswith("_DTYPE") and n.isupper()] + [
        C._audit_dtype()
    ]

    def collect(_name, obj) -> None:
        if isinstance(obj, h5py.Dataset) and obj.dtype.names:
            dtypes.append(obj.dtype)

    with h5py.File(project.path, "r") as f:
        f.visititems(collect)
    assert any("actor" in (dtype.names or ()) for dtype in dtypes)
    for dtype in dtypes:
        person_fields = {field for field in (dtype.names or ()) if _PERSON_FIELD.search(field)}
        assert person_fields <= set(E._name_fields(dtype))


def test_every_json_key_that_names_a_person_is_aliased(tmp_path):
    """Guard: a JSON object stored in a compound field must list its person key.

    Scans every string field of every compound dataset of a populated store (with a run
    job, whose owner is written lazily) for a JSON object with a person-like key, so a
    later JSON record of a person fails here until JSON_USER_KEYS covers it.
    """
    project, _ = _source(tmp_path)
    _record_takeovers(project.path)
    _record_jobs(project)
    found: set[tuple[str, str]] = set()

    def visit(_name, obj) -> None:
        if not (isinstance(obj, h5py.Dataset) and obj.dtype.names):
            return
        for field in obj.dtype.names:
            for value in obj[field]:
                text = to_str(value) if isinstance(value, bytes | str) else ""
                try:
                    parsed = json.loads(text) if text.startswith("{") else None
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    found.update((field, k) for k in parsed if _PERSON_FIELD.search(k))

    with h5py.File(project.path, "r") as f:
        f.visititems(visit)
    assert ("owner", "user") in found  # the scan sees the job owner, so it is looking
    assert found <= set(JSON_USER_KEYS.items())


def _record_model(project_path, artifact_path: str) -> str:
    """Write a ``/models/<condition_id>`` reference as ``record_model_reference`` does."""
    from tether.project import model_artifact as M

    cid = to_str(read_molecules(project_path)["condition_id"][0])
    reference = M.ModelReference(
        condition_id=cid,
        artifact_path=artifact_path,
        artifact_sha256="0" * 64,
        n_retrains=0,
        model_updated_utc="2026-01-02T03:04:05+00:00",
        recorded_utc="2026-01-02T03:04:05+00:00",
        app_version="0.0.0",
        reference_version=M.MODEL_REFERENCE_VERSION,
    )
    with h5py.File(project_path, "r+") as f:
        group = f.require_group("models").require_group(cid)
        for key, value in M._reference_attrs(reference).items():
            group.attrs[key] = value
    return cid


@pytest.mark.parametrize(
    "artifact_path",
    [f"/home/{_HOME}/models/cond.tethermodel", f"C:\\Users\\{_HOME}\\models\\cond.tethermodel"],
    ids=["posix", "windows"],
)
def test_a_model_path_keeps_only_its_file_name(tmp_path, artifact_path):
    """A model file under a home folder does not carry the user name into the copy."""
    from tether.project.model_artifact import read_model_references

    project, _ = _source(tmp_path)
    cid = _record_model(project.path, artifact_path)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    export_subset_tether(project, shared, include_rejected=True, curator_alias="curator")

    assert _HOME.encode() in plain.read_bytes()  # the search can see the folder
    assert _HOME.encode() not in shared.read_bytes()
    assert read_model_references(shared)[cid].artifact_path == "cond.tethermodel"
    assert read_model_references(plain)[cid].artifact_path == artifact_path
    assert read_model_references(project.path)[cid].artifact_path == artifact_path


def test_every_attribute_that_stores_a_path_is_shortened(tmp_path):
    """Guard: an attribute that stores a path must be in PATH_ATTRS.

    Scans every attribute of a populated store with a model reference, so a later
    attribute that records a path fails here until the alias option covers it.
    """
    project, _ = _source(tmp_path)
    _record_model(project.path, f"/home/{_HOME}/cond.tethermodel")
    names: set[str] = set()

    def collect(_name, obj) -> None:
        names.update(obj.attrs.keys())

    with h5py.File(project.path, "r") as f:
        names.update(f.attrs.keys())
        f.visititems(collect)
    path_attrs = {name for name in names if _PATH_ATTR.search(name)}
    assert "artifact_path" in path_attrs  # the scan sees the model path
    assert path_attrs <= PATH_ATTRS


def _record_takeovers(project_path) -> None:
    """A take-over of Carol's lock by Bob, then a recovery of a corrupt lock by Bob."""
    from tether.project.lock import LockInfo
    from tether.project.lock_takeovers import record_takeover

    def info(user: str) -> LockInfo:
        return LockInfo(
            host="lab-pc", user=user, pid=7, timestamp="2026-01-02T03:04:05+00:00", nonce="n"
        )

    record_takeover(project_path, "take_over", info(BOB), info(CAROL))
    record_takeover(project_path, "recover", info(BOB), None)


def _record_jobs(project) -> None:
    """One job run by this session, then one whose owner is Dave."""
    from tether.project import jobs
    from tether.services import ExtractRequest

    request = ExtractRequest(movie="m.tif", output="m.tether")
    project.acquire_lock()
    try:
        ran = jobs.submit_job(project, request, ("a",))
        jobs.run_job(project, ran.job_id, lambda *_: None)
        other = jobs.submit_job(project, request, ("a",))
    finally:
        project.release_lock()
    owner = {"host": "lab-pc", "nonce": "n", "pid": 7, "user": DAVE}
    jobs._write_job(project.path, other.job_id, owner=json.dumps(owner, sort_keys=True))


def _record_correction_transitions(project) -> None:
    """Record two person actors and a policy whose text matches a labeler."""
    project.acquire_lock()
    try:
        CS.send_event(project, CS.CorrectionEvent.FINISH_INITIAL_CURATION, CS.Actor.person(CAROL))
        CS.send_event(
            project,
            CS.CorrectionEvent.CONFIRM_BLEEDTHROUGH,
            CS.Actor.policy(BOB),
            alpha=0.07,
            source="donor-only sample",
        )
        CS.send_event(
            project,
            CS.CorrectionEvent.APPROVE,
            CS.Actor.person(ALICE),
            gamma=0.9,
            factor_version="fv1",
        )
    finally:
        project.release_lock()


def test_correction_transition_person_names_are_aliased(tmp_path):
    """Person actors share the label aliases and policy text keeps its value."""
    project, _ = _source(tmp_path)
    _record_correction_transitions(project)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    result = export_subset_tether(project, shared, include_rejected=True, curator_alias="curator")

    initial, policy, approval = CS.read_correction_transitions(project.path)
    assert CS.read_correction_transitions(plain) == [initial, policy, approval]
    assert CS.read_correction_transitions(shared) == [
        replace(initial, actor=CS.Actor.person("curator-4")),
        policy,
        replace(approval, actor=CS.Actor.person("curator-1")),
    ]
    assert CAROL.encode() in plain.read_bytes()
    assert CAROL.encode() not in shared.read_bytes()
    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["n_aliased_names"] == 4


def _job_owners(path) -> list[dict]:
    with h5py.File(path, "r") as f:
        return [json.loads(to_str(v)) for v in f["settings"]["jobs"]["owner"]]


def test_job_owner_names_are_aliased(tmp_path):
    """A job record keeps its owner object; only the user name in it is aliased."""
    project, _ = _source(tmp_path)
    _record_jobs(project)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    result = export_subset_tether(project, shared, include_rejected=True, curator_alias="curator")

    source = _job_owners(project.path)
    owners = _job_owners(shared)
    # The first job's owner shares the alias of the default labeler.
    assert [o["user"] for o in owners] == ["curator-3", "curator-4"]
    for got, was in zip(owners, source, strict=True):
        assert {k: v for k, v in got.items() if k != "user"} == {
            k: v for k, v in was.items() if k != "user"
        }
    assert [o["user"] for o in source] == [FIXTURE_LOGIN, DAVE]
    assert DAVE.encode() in plain.read_bytes()  # the search can see the name
    assert FIXTURE_LOGIN.encode() not in shared.read_bytes()
    assert DAVE.encode() not in shared.read_bytes()
    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["n_aliased_names"] == 4


def _takeovers(path) -> list[tuple[str, str]]:
    from tether.project.lock_takeovers import read_takeovers

    rows = read_takeovers(path)
    return [(to_str(r["holder_user"]), to_str(r["ousted_user"])) for r in rows]


def test_lock_takeover_names_are_aliased(tmp_path):
    """The lock takeover log names the users; one person keeps one alias across tables."""
    project, _ = _source(tmp_path)
    _record_takeovers(project.path)
    plain = tmp_path / "plain.tether"
    shared = tmp_path / "shared.tether"

    export_subset_tether(project, plain, include_rejected=True)
    result = export_subset_tether(project, shared, include_rejected=True, curator_alias="curator")

    # Labels give alice 1, bob 2, the default labeler 3; then the audit log and takeovers.
    assert _takeovers(shared) == [("curator-2", "curator-4"), ("curator-2", "")]
    assert _takeovers(project.path) == [(BOB, CAROL), (BOB, "")]
    assert CAROL.encode() in plain.read_bytes()  # the search can see the name
    for name in (ALICE.encode(), BOB.encode(), CAROL.encode()):
        assert name not in shared.read_bytes()
    params = json.loads(result.provenance_path.read_text(encoding="utf-8"))["parameters"]
    assert params["n_aliased_names"] == 4
