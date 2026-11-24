# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The source registry: originals, derived files and the recipe between them (#912, ADR-0140).

Headless (no Qt). Each test reads the project file back, so it checks what is stored.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import build_store_with_channels  # noqa: E402
from tether.io.schema import (  # noqa: E402
    SCHEMA_VERSION,
    SOURCES_DTYPE,
    assert_is_compatible_project,
    read_schema_version,
)
from tether.project import jobs  # noqa: E402
from tether.project import sources as S  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.export import export_subset_tether  # noqa: E402
from tether.project.lock import LockedError, LockIdentity  # noqa: E402
from tether.services.sources import run_checksum_job, submit_checksum_job  # noqa: E402

#: Bigger than two 1 MiB blocks, so a checksum reports progress more than once.
_MOVIE_BYTES = bytes(range(256)) * (10 * 1024 + 7)


def _project(tmp_path: Path, *, version: int = SCHEMA_VERSION, movie_sha: str = ""):
    """A project with one movie file ``movie.tif`` beside it, recorded in ``/movies``."""
    movie = tmp_path / "movie.tif"
    movie.write_bytes(_MOVIE_BYTES)
    donor = np.ones((3, 20))
    project, _ = build_store_with_channels(tmp_path, donor, donor, uri="movie.tif")
    with h5py.File(project.path, "r+") as f:
        table = f["movies"]["table"][:]
        table["sha256"][0] = movie_sha
        table["file_size"][0] = len(_MOVIE_BYTES)
        f["movies"]["table"][:] = table
        if version < 3:
            del f["sources"]
            f.attrs["schema_version"] = version
    return project, movie


def test_the_registry_is_a_version_3_table_with_every_field() -> None:
    assert SCHEMA_VERSION == 3
    assert SOURCES_DTYPE.names == (
        "source_id",
        "relative_path",
        "original_name",
        "file_size",
        "sha256",
        "adapter",
        "adapter_version",
        "role",
        "acquisition",
        "movie_id",
        "derived_from",
        "recipe",
        "created_utc",
    )


def test_an_existing_project_reads_one_record_per_movie_and_writes_nothing(tmp_path) -> None:
    project, _ = _project(tmp_path, version=2)
    before = project.path.read_bytes()
    assert assert_is_compatible_project(project.path) == 2
    (record,) = S.read_sources(project.path)
    assert (record.source_id, record.role, record.movie_id) == ("movie:mov-1", "movie", "mov-1")
    assert (record.relative_path, record.original_name) == ("movie.tif", "movie.tif")
    assert record.file_size == len(_MOVIE_BYTES)
    assert not record.checksum_calculated and not record.stored
    assert record.resolve(project.path) == tmp_path / "movie.tif"
    assert project.path.read_bytes() == before


def test_an_original_and_a_derived_file_link_through_the_recipe(tmp_path) -> None:
    project, _ = _project(tmp_path)
    beads = tmp_path / "raw" / "beads.tif"
    beads.parent.mkdir()
    beads.write_bytes(b"bead video")
    image = tmp_path / "beads-max.tif"
    image.write_bytes(b"max projection")
    with project.write_lock():
        original = S.register_source(
            project,
            beads,
            role="bead_video",
            adapter="tiff",
            adapter_version="1",
            acquisition={"frame_time_s": 0.1},
        )
        derived = S.register_source(
            project,
            image,
            role="map_image",
            derived_from=original.source_id,
            recipe='{"projection": "max"}',
        )
    by_id = {r.source_id: r for r in S.read_sources(project.path)}
    stored = by_id[original.source_id]
    assert stored.relative_path == "raw/beads.tif"
    assert (stored.adapter, stored.adapter_version) == ("tiff", "1")
    assert stored.acquisition == {"frame_time_s": 0.1}
    assert stored.file_size == len(b"bead video") and not stored.checksum_calculated
    link = by_id[derived.source_id]
    assert (link.derived_from, link.recipe) == (original.source_id, '{"projection": "max"}')
    assert by_id["movie:mov-1"].stored is False  # the movie is still made from /movies


def test_a_bad_registration_is_refused(tmp_path) -> None:
    project, movie = _project(tmp_path)
    other = tmp_path / "other.tif"
    other.write_bytes(b"x")
    with project.write_lock():
        with pytest.raises(ValueError, match="role"):
            S.register_source(project, other, role="photo")
        with pytest.raises(ValueError, match="both"):
            S.register_source(project, other, role="map_image", recipe="r")
        with pytest.raises(KeyError):
            S.register_source(project, other, role="map_image", derived_from="nope", recipe="r")
        with pytest.raises(ValueError, match="movie_id"):
            S.register_source(project, other, role="movie")
        S.register_source(project, other, role="map_image")
        with pytest.raises(ValueError, match="already registered"):
            S.register_source(project, other, role="map_image")


def test_a_file_derived_from_a_movie_stores_the_movie_record(tmp_path) -> None:
    project, _ = _project(tmp_path)
    crop = tmp_path / "crop.tif"
    crop.write_bytes(b"crop")
    with project.write_lock():
        S.register_source(
            project, crop, role="movie", movie_id="mov-2", derived_from="movie:mov-1", recipe="c"
        )
    records = {r.source_id: r for r in S.read_sources(project.path)}
    assert records["movie:mov-1"].stored


def test_the_first_registry_write_backs_up_an_older_store(tmp_path) -> None:
    project, _ = _project(tmp_path, version=2)
    original = project.path.read_bytes()
    with project.write_lock():
        S.ensure_registry(project)
    backup = tmp_path / "chan.v2-backup.tether"
    assert backup.read_bytes() == original
    assert read_schema_version(project.path) == 3
    with h5py.File(project.path, "r") as f:
        assert f["sources"]["table"].shape == (0,)


def test_the_checksum_is_a_job_with_progress_on_a_worker_thread(tmp_path) -> None:
    project, _ = _project(tmp_path, version=2)
    project.acquire_lock()
    try:
        job = submit_checksum_job(project)
        assert job.parts == ("movie:mov-1",)
        assert str(tmp_path) not in job.request  # the stored job names no path
        parts: list[tuple[int, int]] = []
        blocks: list[tuple[str, int, int]] = []
        threads: set[int] = set()

        def on_block(source_id: str, read: int, size: int) -> None:
            threads.add(threading.get_ident())
            blocks.append((source_id, read, size))

        out: list[jobs.JobRecord] = []
        worker = threading.Thread(
            target=lambda: out.append(
                run_checksum_job(
                    project, job.job_id, progress=lambda *a: parts.append(a), file_progress=on_block
                )
            )
        )
        worker.start()
        worker.join()
    finally:
        project.release_lock()
    assert out[0].state is jobs.JobState.DONE
    assert threads == {worker.ident}
    assert parts == [(1, 1)]
    assert len(blocks) == 3 and blocks[-1] == ("movie:mov-1", len(_MOVIE_BYTES), len(_MOVIE_BYTES))
    (record,) = S.read_sources(project.path)
    assert record.stored and record.sha256 == hashlib.sha256(_MOVIE_BYTES).hexdigest()


def test_a_cancelled_checksum_writes_nothing_and_continues_later(tmp_path) -> None:
    project, _ = _project(tmp_path)
    project.acquire_lock()
    try:
        job = submit_checksum_job(project, ["movie:mov-1"])
        token = jobs.CancelToken()
        first = run_checksum_job(
            project, job.job_id, cancel=token, file_progress=lambda *_: token.cancel()
        )
        assert first.state is jobs.JobState.CANCELLED
        assert not S.read_sources(project.path)[0].checksum_calculated
        again = run_checksum_job(project, job.job_id)
    finally:
        project.release_lock()
    assert again.state is jobs.JobState.DONE
    assert S.read_sources(project.path)[0].sha256 == hashlib.sha256(_MOVIE_BYTES).hexdigest()


def test_a_moved_source_links_again_only_when_the_checksum_matches(tmp_path) -> None:
    project, movie = _project(tmp_path, movie_sha=hashlib.sha256(_MOVIE_BYTES).hexdigest())
    moved = tmp_path / "moved" / "renamed.tif"
    moved.parent.mkdir()
    movie.rename(moved)
    impostor = tmp_path / "impostor.tif"
    impostor.write_bytes(_MOVIE_BYTES[:-1] + b"!")
    with project.write_lock():
        before = project.path.read_bytes()
        with pytest.raises(S.RelinkRefused, match="checksum is different"):
            S.relink_source(project, "movie:mov-1", impostor)
        assert project.path.read_bytes() == before
        record = S.relink_source(project, "movie:mov-1", moved)
    assert record.relative_path == "moved/renamed.tif"
    (stored,) = S.read_sources(project.path)
    assert stored.stored and stored.relative_path == "moved/renamed.tif"
    assert stored.sha256 == hashlib.sha256(_MOVIE_BYTES).hexdigest()


def test_a_source_with_no_known_checksum_cannot_be_linked_again(tmp_path) -> None:
    project, movie = _project(tmp_path)  # /movies holds no checksum either
    with project.write_lock(), pytest.raises(S.RelinkRefused, match="no checksum"):
        S.relink_source(project, "movie:mov-1", movie)
    assert not S.same_file("", "")
    assert S.same_file("AB", "ab")


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("raw/m.tif", "raw/m.tif"),
        ("../../home/dana/m.tif", "m.tif"),
        ("/home/dana/m.tif", "m.tif"),
        ("C:/Users/dana/m.tif", "m.tif"),
        ("C:\\Users\\dana\\m.tif", "m.tif"),
    ],
)
def test_a_copy_for_sharing_keeps_only_the_name_of_a_path_outside_the_folder(path, kept) -> None:
    assert S.shareable_path(path) == kept


def test_the_copy_for_sharing_holds_no_absolute_private_path(tmp_path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    project, _ = _project(folder)
    private = tmp_path / "dana-pq9-home" / "beads.tif"
    private.parent.mkdir()
    private.write_bytes(b"beads")
    with project.write_lock():
        S.register_source(project, private, role="bead_video")
    out = tmp_path / "shared.tether"
    export_subset_tether(project, out, curator_alias="curator")
    shared = S.read_sources(out)
    assert {r.relative_path for r in shared} == {"movie.tif", "beads.tif"}
    assert b"dana-pq9-home" not in out.read_bytes()
    plain = tmp_path / "plain.tether"
    export_subset_tether(project, plain)
    assert {r.relative_path for r in S.read_sources(plain)} == {
        "project/movie.tif",
        "dana-pq9-home/beads.tif",
    }


def test_a_subset_in_another_folder_still_names_the_same_files(tmp_path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    project, movie = _project(folder)
    out = tmp_path / "exports" / "subset.tether"
    out.parent.mkdir()
    export_subset_tether(project, out)
    (record,) = S.read_sources(out)
    assert record.relative_path == "../data/movie.tif"
    assert record.resolve(out).resolve() == movie.resolve()


def test_a_movie_that_changed_since_extraction_keeps_its_checksum(tmp_path) -> None:
    original = hashlib.sha256(_MOVIE_BYTES).hexdigest()
    project, movie = _project(tmp_path, movie_sha=original)
    movie.write_bytes(_MOVIE_BYTES + b"changed")
    project.acquire_lock()
    try:
        job = submit_checksum_job(project)
        out = run_checksum_job(project, job.job_id)
        assert out.state is jobs.JobState.FAILED and "ChecksumMismatch" in out.error
        assert not S.read_sources(project.path)[0].checksum_calculated
        with pytest.raises(S.ChecksumMismatch):
            S.set_checksum(project, "movie:mov-1", "0" * 64)
        S.set_checksum(project, "movie:mov-1", original.upper())
        with pytest.raises(S.ChecksumMismatch):  # a stored checksum is never replaced
            S.set_checksum(project, "movie:mov-1", "0" * 64)
    finally:
        project.release_lock()
    assert S.read_sources(project.path)[0].sha256 == original


def test_a_cancel_during_the_last_block_stores_nothing(tmp_path) -> None:
    project, movie = _project(tmp_path)
    movie.write_bytes(b"one small block")
    project.acquire_lock()
    try:
        job = submit_checksum_job(project)
        token = jobs.CancelToken()
        out = run_checksum_job(
            project, job.job_id, cancel=token, file_progress=lambda *_: token.cancel()
        )
    finally:
        project.release_lock()
    assert out.state is jobs.JobState.CANCELLED
    assert not S.read_sources(project.path)[0].checksum_calculated


def test_a_lock_lost_during_a_checksum_stops_its_write(tmp_path) -> None:
    project, _ = _project(tmp_path)
    thief = Project(project.path, identity=LockIdentity("other-host", "someone", 4242))
    project.acquire_lock()
    job = submit_checksum_job(project)

    stolen: list[bool] = []

    def steal_and_leave(*_: object) -> None:
        if not stolen:
            stolen.append(True)
            thief.steal_lock()
            thief.release_lock()  # no lock file is left, which a plain writer check accepts

    with pytest.raises(LockedError):
        run_checksum_job(project, job.job_id, file_progress=steal_and_leave)
    assert not S.read_sources(project.path)[0].checksum_calculated


def test_a_lock_lost_during_a_relink_stops_its_write(tmp_path) -> None:
    project, movie = _project(tmp_path, movie_sha=hashlib.sha256(_MOVIE_BYTES).hexdigest())
    moved = tmp_path / "moved.tif"
    movie.rename(moved)
    thief = Project(project.path, identity=LockIdentity("other-host", "someone", 4242))
    project.acquire_lock()
    stolen: list[bool] = []

    def steal_and_leave(*_: object) -> None:
        if not stolen:
            stolen.append(True)
            thief.steal_lock()
            thief.release_lock()  # no lock file is left, which a plain writer check accepts

    before = project.path.read_bytes()
    with pytest.raises(LockedError):
        S.relink_source(project, "movie:mov-1", moved, progress=steal_and_leave)
    assert project.path.read_bytes() == before
    assert not S.read_sources(project.path)[0].stored


def test_a_legacy_movie_path_from_the_current_folder_is_read_as_the_movie_panel_reads_it(
    tmp_path, monkeypatch
) -> None:
    project, movie = _project(tmp_path)
    # Extraction ran from ``tmp_path`` with ``data/movie.tif`` while the project is in ``out/``.
    (tmp_path / "data").mkdir()
    legacy = tmp_path / "data" / "movie.tif"
    movie.rename(legacy)
    out = tmp_path / "out"
    out.mkdir()
    moved = out / project.path.name
    project.path.rename(moved)
    with h5py.File(moved, "r+") as f:
        table = f["movies"]["table"][:]
        table["uri"][0] = "data/movie.tif"
        table["mtime"][0] = legacy.stat().st_mtime
        f["movies"]["table"][:] = table
    monkeypatch.chdir(tmp_path)
    (record,) = S.read_sources(moved)
    assert record.relative_path == "../data/movie.tif"
    assert record.resolve(moved).resolve() == legacy.resolve()
    # A file whose signature does not match is not taken: the path stays in the project folder.
    with h5py.File(moved, "r+") as f:
        table = f["movies"]["table"][:]
        table["mtime"][0] = 1.0
        f["movies"]["table"][:] = table
    assert S.read_sources(moved)[0].relative_path == "data/movie.tif"


def test_a_subset_keeps_the_extraction_checksum_of_a_movie_not_yet_registered(
    tmp_path,
) -> None:
    sha = hashlib.sha256(_MOVIE_BYTES).hexdigest()
    project, movie = _project(tmp_path, movie_sha=sha)
    out = tmp_path / "subset" / "subset.tether"
    out.parent.mkdir()
    export_subset_tether(project, out)
    (record,) = S.read_sources(out)
    assert record.stored and record.sha256 == sha
    # The subset can link the moved movie again, which needs that checksum.
    moved = tmp_path / "moved.tif"
    movie.rename(moved)
    subset = Project(out)
    with subset.write_lock():
        assert S.relink_source(subset, record.source_id, moved).relative_path == "../moved.tif"


def test_a_stored_movie_record_with_no_checksum_takes_the_extraction_checksum_into_a_subset(
    tmp_path,
) -> None:
    sha = hashlib.sha256(_MOVIE_BYTES).hexdigest()
    project, _ = _project(tmp_path, movie_sha=sha)
    derived = tmp_path / "movie-crop.tif"
    derived.write_bytes(b"crop")
    with project.write_lock():  # stores the movie's record, with no checksum of its own
        S.register_source(
            project, derived, role="map_image", derived_from="movie:mov-1", recipe="crop"
        )
    original = next(r for r in S.read_sources(project.path) if r.source_id == "movie:mov-1")
    assert original.stored and not original.sha256
    out = tmp_path / "subset" / "subset.tether"
    out.parent.mkdir()
    export_subset_tether(project, out)
    copied = next(r for r in S.read_sources(out) if r.source_id == "movie:mov-1")
    assert copied.sha256 == sha


def test_a_map_image_that_names_a_movie_keeps_its_own_checksum_in_a_subset(tmp_path) -> None:
    sha = hashlib.sha256(_MOVIE_BYTES).hexdigest()
    project, _ = _project(tmp_path, movie_sha=sha)
    image = tmp_path / "map.png"
    image.write_bytes(b"map")
    with project.write_lock():
        record = S.register_source(project, image, role="map_image", movie_id="mov-1")
    out = tmp_path / "subset" / "subset.tether"
    out.parent.mkdir()
    export_subset_tether(project, out)
    copied = next(r for r in S.read_sources(out) if r.source_id == record.source_id)
    assert copied.sha256 == ""  # not the movie's checksum
