# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The conflict-copy notice that the application and the command line both show (#560)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

from tether.io.schema import create_project  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.project.lock import LockIdentity  # noqa: E402
from tether.services.notices import conflict_copy_warning  # noqa: E402

HOST_A = LockIdentity(host="HOST-A", user="alice", pid=111)


def test_the_warning_names_every_copy_and_says_nothing_is_changed(tmp_path: Path) -> None:
    # Both documented naming forms are named, and Tether leaves them alone (§5.4).
    path = create_project(tmp_path / "exp.tether")
    (tmp_path / "exp-DESKTOP-AB12.tether").write_bytes(b"copy")
    (tmp_path / "exp (1).tether").write_bytes(b"copy")

    warning = conflict_copy_warning(path)

    assert warning is not None
    assert warning.startswith("2 possible OneDrive/SharePoint conflict copies beside exp.tether:")
    assert "exp-DESKTOP-AB12.tether" in warning
    assert "exp (1).tether" in warning
    assert "does not merge or delete them" in warning


def test_the_warning_uses_the_singular_for_one_copy(tmp_path: Path) -> None:
    path = create_project(tmp_path / "exp.tether")
    (tmp_path / "exp-LAPTOP-9.tether").write_bytes(b"copy")

    assert conflict_copy_warning(path) == (
        "1 possible OneDrive/SharePoint conflict copy beside exp.tether: "
        "exp-LAPTOP-9.tether. Tether does not merge or delete it; compare and "
        "reconcile by hand"
    )


def test_there_is_no_warning_without_copies(tmp_path: Path) -> None:
    path = create_project(tmp_path / "exp.tether")
    lock.acquire(path, identity=HOST_A)  # its own .lock is not a conflict copy
    (tmp_path / "other.tether").write_bytes(b"unrelated")

    assert conflict_copy_warning(path) is None


def test_the_warning_never_raises_when_the_folder_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Advisory only: a listing failure must not fail the open that asked.
    def _boom(_project_path: object) -> list[Path]:
        raise PermissionError("listing refused")

    monkeypatch.setattr(lock, "conflict_copies", _boom)

    assert conflict_copy_warning(tmp_path / "exp.tether") is None
