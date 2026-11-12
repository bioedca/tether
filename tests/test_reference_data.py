# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reference-data manifest and its fetch helper (#696, ADR-0073). No test here uses the network.

The first half holds ``tests/reference_data/manifest.toml`` to its rules: the fields, the licence,
the checksum, the tier limits, and a manifest entry plus REUSE, NOTICE and PROVENANCE rows for every
committed third-party file. The second half drives ``fetch.path_for`` with local files and a fake
opener: a wrong checksum is refused with both checksums named, a download happens only when it is
allowed, and a failed download leaves nothing in the cache.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from reference_data import fetch

REPO = Path(__file__).resolve().parents[1]
PROJECT_LICENSE = "GPL-3.0-or-later"
PROJECT_HOLDER = "The Tether Authors"
_LICENCE_TAG = "SPDX-" + "License-Identifier:"  # split, so REUSE does not read it as a tag
MANIFEST = fetch.load_manifest()


def _committed(tier: str | None = None) -> list[fetch.Dataset]:
    tiers = (tier,) if tier else ("git", "lfs")
    return [d for d in MANIFEST.values() if d.tier in tiers]


# --- The manifest ------------------------------------------------------------------------------


def test_every_entry_keeps_the_manifest_rules() -> None:
    assert MANIFEST, "the manifest must not be empty"
    assert fetch.check_manifest(MANIFEST) == []


def test_committed_files_have_the_size_and_checksum_of_the_manifest() -> None:
    for d in _committed():
        path = REPO / d.file
        if fetch.lfs_oid(path) is not None:
            continue  # an unmaterialized pointer: check_manifest compared its oid and size
        assert hashlib.sha256(path.read_bytes()).hexdigest() == d.sha256, d.id


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to read .gitattributes")
def test_git_tier_files_are_never_line_ending_converted() -> None:
    for d in _committed("git"):
        out = subprocess.run(
            ["git", "check-attr", "text", "--", d.file],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert out.strip().endswith("text: unset"), f"{d.file} needs `-text` in .gitattributes"


def _with(d: fetch.Dataset, **changes: object) -> dict[str, fetch.Dataset]:
    entry = dataclasses.replace(MANIFEST[d.id], **changes) if d.id in MANIFEST else d
    return {**MANIFEST, d.id: entry}


_GIT = MANIFEST["kinsoft-trace-sample"]
_LFS = MANIFEST["kinsoft-sim-packed"]
_DOWNLOAD = MANIFEST["kinsoft-sim-fig2"]


@pytest.mark.parametrize(
    ("entry", "changes", "message"),
    [
        (_GIT, {"id": "Bad_ID"}, "an id is"),
        (_GIT, {"source": "zenodo 5701310"}, "source must be"),
        (_GIT, {"record_url": "http://zenodo.org"}, "record_url must be"),
        (_GIT, {"license": "CC-BY-3.0"}, "REUSE does not know it"),
        (_GIT, {"sha256": "ABC"}, "64 lower-case hex"),
        (_GIT, {"citation": " "}, "citation is empty"),
        (_GIT, {"tier": "usb"}, "tier must be one of"),
        (_GIT, {"file": "../outside.txt"}, "relative path"),
        (_GIT, {"file": "..\\outside.txt"}, "relative path"),
        (_GIT, {"file": "C:outside.txt"}, "relative path"),
        (_GIT, {"size": 1980}, "the manifest says 1980 B"),
        (_GIT, {"size": 2_000_000}, "a git file is at most"),
        (_GIT, {"file": "tests/fixtures/missing.txt"}, "is not committed"),
        (_LFS, {"tier": "git"}, "under an LFS path"),
        (_GIT, {"tier": "lfs"}, "an lfs file lives under"),
        (_LFS, {"sha256": "0" * 64}, "the LFS pointer names"),
        (_DOWNLOAD, {"download_url": None}, "needs an https download_url"),
        (_DOWNLOAD, {"size": 200_000_001}, "use tier workstation"),
        (_GIT, {"download_url": "https://example.org/x"}, "only a download entry"),
        (_DOWNLOAD, {"tier": "workstation"}, "names its directory variable"),
        (_GIT, {"env": "TETHER_X"}, "only a workstation entry"),
        (_GIT, {"extract_of": ("kinsoft-trace-sample",)}, "is not a download or workstation"),
        (_GIT, {"extract_member": None}, "either its extract_script or"),
        (_GIT, {"extract_script": "scripts/make_kinsoft_fixture.py"}, "either its extract_script"),
        (_LFS, {"extract_script": "scripts/none.py"}, "does not exist"),
        (_GIT, {"extract_of": ()}, "need extract_of"),
        (_DOWNLOAD, {"extract_of": ("kinsoft-sim-fig3",), "extract_script": "x"}, "is committed"),
    ],
)
def test_each_rule_refuses_a_broken_entry(entry, changes, message) -> None:
    if entry.tier == "lfs" and changes.get("sha256") and fetch.lfs_oid(REPO / entry.file) is None:
        pytest.skip("the LFS object is materialized, so there is no pointer oid to compare")
    broken = dataclasses.replace(entry, **changes)
    datasets = {k: v for k, v in MANIFEST.items() if k != entry.id}
    datasets[broken.id] = broken
    problems = fetch.check_manifest(datasets)
    assert any(message in p for p in problems), problems


def test_the_lfs_total_is_limited(tmp_path: Path) -> None:
    (tmp_path / "LICENSES").mkdir()
    big = tmp_path / "tests" / "fixtures" / "large" / "big.bin"
    big.parent.mkdir(parents=True)
    big.write_bytes(fetch.LFS_POINTER + b"\noid sha256:" + b"0" * 64 + b"\nsize 50000001\n")
    assert fetch.check_manifest({}, repo=tmp_path) == [
        "the LFS tier holds 50000001 B, more than 50000000 B"
    ]


def test_the_loader_refuses_unknown_and_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.toml"
    path.write_text('[[dataset]]\nid = "x"\ncolour = "red"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"x: missing fields .*unknown fields \['colour'\]"):
        fetch.load_manifest(path)


def _third_party_fixtures() -> dict[str, str]:
    """Each committed test file that REUSE gives to a holder other than the project, by licence."""
    reuse = tomllib.loads((REPO / "REUSE.toml").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for note in reuse.get("annotations", []):
        holders = note.get("SPDX-FileCopyrightText", [])
        holders = [holders] if isinstance(holders, str) else holders
        if all(PROJECT_HOLDER in h for h in holders):
            continue
        patterns = note["path"] if isinstance(note["path"], list) else [note["path"]]
        for pattern in patterns:
            if not pattern.startswith(("tests/", "benchmarks/")):
                continue
            for path in REPO.glob(pattern):
                if path.is_file():
                    found[path.relative_to(REPO).as_posix()] = note["SPDX-License-Identifier"]
    for sidecar in [*REPO.glob("tests/**/*.license"), *REPO.glob("benchmarks/**/*.license")]:
        text = sidecar.read_text(encoding="utf-8")
        if PROJECT_HOLDER not in text:
            licence = text.split(_LICENCE_TAG)[-1].strip().split()[0]
            found[sidecar.relative_to(REPO).with_suffix("").as_posix()] = licence
    return found


def test_every_committed_third_party_fixture_has_a_manifest_entry() -> None:
    third_party = _third_party_fixtures()
    assert third_party, "REUSE.toml names the kinSoft fixtures, so this set is never empty"
    by_file = {d.file: d for d in _committed()}
    missing = sorted(set(third_party) - set(by_file))
    assert not missing, f"third-party fixtures with no entry in the manifest: {missing}"
    for file, licence in third_party.items():
        assert by_file[file].license == licence, f"{file}: REUSE says {licence}"


def test_every_committed_third_party_entry_has_reuse_notice_and_provenance_rows() -> None:
    third_party = _third_party_fixtures()
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    provenance = (REPO / "tests" / "fixtures" / "PROVENANCE.md").read_text(encoding="utf-8")
    for d in _committed():
        if d.license == PROJECT_LICENSE:
            continue
        assert d.file in third_party, f"{d.id}: no REUSE.toml annotation for {d.file}"
        assert d.file in notice, f"{d.id}: NOTICE does not name {d.file}"
        assert d.file in provenance, f"{d.id}: tests/fixtures/PROVENANCE.md does not name {d.file}"


def test_the_cache_key_follows_only_the_download_checksums() -> None:
    key = fetch.cache_key(MANIFEST)
    cited = _with(_DOWNLOAD, citation="another text")
    assert fetch.cache_key(cited) == key
    assert fetch.cache_key(_with(_GIT, sha256="1" * 64)) == key
    assert fetch.cache_key(_with(_DOWNLOAD, sha256="2" * 64)) != key


# --- fetch.path_for, offline --------------------------------------------------------------------

_PAYLOAD = b"reference bytes\n"


def _entry(tier: str, **changes: object) -> fetch.Dataset:
    base: dict[str, object] = {
        "id": "demo",
        "source": "doi:10.5281/zenodo.1",
        "record_url": "https://zenodo.org/records/1",
        "license": "CC-BY-4.0",
        "file": "demo.bin",
        "size": len(_PAYLOAD),
        "sha256": hashlib.sha256(_PAYLOAD).hexdigest(),
        "tier": tier,
        "citation": "Demo.",
    }
    if tier == "download":
        base["download_url"] = "https://zenodo.org/records/1/files/demo.bin"
    if tier == "workstation":
        base["env"] = "TETHER_DEMO_DIR"
    base.update(changes)
    return fetch.Dataset(**base)


def _no_network(*_args, **_kwargs):
    raise AssertionError("path_for opened a URL")


class _Opener:
    def __init__(self, data: bytes) -> None:
        self.data, self.calls = data, 0

    def __call__(self, url: str, timeout: float) -> io.BytesIO:
        self.calls += 1
        assert url.startswith("https://") and timeout > 0
        return io.BytesIO(self.data)


@pytest.fixture(autouse=True)
def _downloads_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(fetch.DOWNLOAD_ENV, raising=False)
    monkeypatch.delenv("TETHER_DEMO_DIR", raising=False)


def test_a_corrupted_cached_file_is_refused_with_both_checksums(tmp_path: Path) -> None:
    d = _entry("download")
    cached = tmp_path / "demo" / "demo.bin"
    cached.parent.mkdir()
    cached.write_bytes(b"corrupted\n")
    actual = hashlib.sha256(b"corrupted\n").hexdigest()
    with pytest.raises(fetch.ChecksumMismatch) as err:
        fetch.path_for("demo", manifest={"demo": d}, cache_dir=tmp_path, opener=_no_network)
    assert "demo:" in str(err.value) and actual in str(err.value) and d.sha256 in str(err.value)


def test_a_corrupted_committed_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "demo.bin").write_bytes(b"edited\n")
    with pytest.raises(fetch.ChecksumMismatch, match="demo: SHA-256 of"):
        fetch.path_for("demo", manifest={"demo": _entry("git")}, repo=tmp_path)


def test_an_lfs_pointer_skips(tmp_path: Path) -> None:
    (tmp_path / "demo.bin").write_bytes(fetch.LFS_POINTER + b"\noid sha256:" + b"a" * 64 + b"\n")
    with pytest.raises(pytest.skip.Exception, match="git lfs pull"):
        fetch.path_for("demo", manifest={"demo": _entry("lfs")}, repo=tmp_path)


def test_with_downloads_off_a_missing_set_skips_and_opens_nothing(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception, match=fetch.DOWNLOAD_ENV):
        fetch.path_for(
            "demo", manifest={"demo": _entry("download")}, cache_dir=tmp_path, opener=_no_network
        )
    assert not any(tmp_path.iterdir())


def test_a_download_is_verified_then_cached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(fetch.DOWNLOAD_ENV, "1")
    opener = _Opener(_PAYLOAD)
    manifest = {"demo": _entry("download")}
    first = fetch.path_for("demo", manifest=manifest, cache_dir=tmp_path, opener=opener)
    again = fetch.path_for("demo", manifest=manifest, cache_dir=tmp_path, opener=opener)
    assert first == again == tmp_path / "demo" / "demo.bin"
    assert first.read_bytes() == _PAYLOAD and opener.calls == 1


def test_a_second_writer_on_the_same_cache_cannot_change_the_returned_file(
    tmp_path: Path, monkeypatch
) -> None:
    """Each download writes its own temporary file (#795 review).

    Another process that misses the same cached file and writes ``demo.bin.part`` while this
    download runs must neither change the bytes that ``path_for`` returns nor lose its own file.
    """
    monkeypatch.setenv(fetch.DOWNLOAD_ENV, "1")
    other = tmp_path / "demo" / "demo.bin.part"
    garbage = b"x" * (len(_PAYLOAD) + 10)

    class _RacingStream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            if not chunk:  # the other writer truncates and fills the shared name
                other.write_bytes(garbage)
            return chunk

    def opener(url: str, timeout: float) -> io.BytesIO:
        return _RacingStream(_PAYLOAD)

    path = fetch.path_for(
        "demo", manifest={"demo": _entry("download")}, cache_dir=tmp_path, opener=opener
    )
    assert path.read_bytes() == _PAYLOAD
    assert other.read_bytes() == garbage


@pytest.mark.parametrize("served", [b"other bytes\n", _PAYLOAD + b"extra"])
def test_a_bad_download_leaves_nothing_in_the_cache(tmp_path: Path, monkeypatch, served) -> None:
    monkeypatch.setenv(fetch.DOWNLOAD_ENV, "1")
    with pytest.raises(fetch.ChecksumMismatch, match="demo: "):
        fetch.path_for(
            "demo",
            manifest={"demo": _entry("download")},
            cache_dir=tmp_path,
            opener=_Opener(served),
        )
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_a_workstation_set_skips_without_its_variable_and_is_verified_with_it(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = {"demo": _entry("workstation")}
    with pytest.raises(pytest.skip.Exception, match="set TETHER_DEMO_DIR"):
        fetch.path_for("demo", manifest=manifest)
    monkeypatch.setenv("TETHER_DEMO_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="TETHER_DEMO_DIR is set"):
        fetch.path_for("demo", manifest=manifest)
    (tmp_path / "demo.bin").write_bytes(_PAYLOAD)
    assert fetch.path_for("demo", manifest=manifest) == tmp_path / "demo.bin"


def test_an_unknown_id_is_an_error() -> None:
    with pytest.raises(KeyError, match="not in the reference-data manifest"):
        fetch.path_for("no-such-set")


def test_the_real_git_entry_resolves_to_its_committed_file() -> None:
    assert (
        fetch.path_for("kinsoft-trace-sample") == REPO / "tests/fixtures/kinsoft_trace_sample.txt"
    )
