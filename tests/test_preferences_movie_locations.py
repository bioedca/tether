# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a movie was found on this computer, in the preferences file (#638, ADR-0122)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tether.gui import preferences

SHA = "ab" * 32


def test_a_location_round_trips_by_checksum(tmp_path) -> None:
    prefs = tmp_path / "preferences.json"
    movie = (tmp_path / "m.tif").absolute()

    preferences.record_movie_location(SHA.upper(), movie, 123, 4.5, prefs)

    stored = preferences.movie_location(SHA, prefs)
    assert stored == preferences.StoredMovieLocation(movie, 123, 4.5)
    assert preferences.movie_location("cd" * 32, prefs) is None


def test_other_preferences_survive_a_location_write(tmp_path) -> None:
    prefs = tmp_path / "preferences.json"
    preferences.set_curator_name("Ada", prefs)

    preferences.record_movie_location(SHA, tmp_path.absolute() / "m.tif", 1, 1.0, prefs)

    assert preferences.curator_name(prefs) == "Ada"


@pytest.mark.parametrize(
    ("sha256", "path"), [("not-a-checksum", "/abs/m.tif"), (SHA, "relative/m.tif")]
)
def test_a_bad_key_or_relative_path_is_refused(tmp_path, sha256, path) -> None:
    with pytest.raises(ValueError):
        preferences.record_movie_location(sha256, path, 1, 1.0, tmp_path / "p.json")


def test_a_malformed_entry_reads_as_unknown(tmp_path) -> None:
    prefs = tmp_path / "preferences.json"
    prefs.write_text(
        json.dumps({"schema": 1, "movie_locations": {SHA: {"path": "relative", "file_size": 1}}})
    )

    assert preferences.movie_location(SHA, prefs) is None


def test_only_the_most_recent_locations_are_kept(tmp_path, monkeypatch) -> None:
    prefs = tmp_path / "preferences.json"
    monkeypatch.setattr(preferences, "MOVIE_LOCATIONS_LIMIT", 2)
    keys = [f"{i:064x}" for i in range(3)]
    for key in keys:
        preferences.record_movie_location(key, Path(tmp_path.absolute(), key), 1, 1.0, prefs)

    assert preferences.movie_location(keys[0], prefs) is None
    assert preferences.movie_location(keys[2], prefs) is not None
