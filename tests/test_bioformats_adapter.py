# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The base side of the Bio-Formats route, in the required matrix (#907, ADR-0143).

The base environment must not change and must not import Java; a ``.vsi`` goes to the
cellSens adapter, which says how to install the stack where it is absent; and the pipe to the
runner maps each refusal to its exception and survives a runner that dies. The runner here is
the Java-free stand-in ``tests/_fake_bioformats_runner.py``; the live runner is the
``bioformats`` tier (``tests/test_runner_bioformats.py``).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")
yaml = pytest.importorskip("yaml")

from tether.io import bioformats  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    InvalidPixelError,
    LossyCodecError,
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.bioformats import (  # noqa: E402
    BIOFORMATS_ADAPTER,
    BioformatsRunnerError,
    BioformatsUnavailableError,
    runner_info,
)

REPO = Path(__file__).resolve().parents[1]
FAKE_RUNNER = Path(__file__).resolve().with_name("_fake_bioformats_runner.py")

#: Java bindings and the Java-backed readers that may never enter the base environment.
JAVA_MODULES = ("jpype", "_jpype", "jnius", "scyjava", "javabridge", "bioformats", "py4j", "jgo")
JAVA_PACKAGES = {
    "openjdk",
    "jpype1",
    "bftools",
    "bioformats_jar",
    "bioformats2raw",
    "scyjava",
    "pyjnius",
    "python-javabridge",
    "python-bioformats",
    "bioio-bioformats",
}


def _vsi(tmp_path: Path, frames: np.ndarray | None = None, **meta: object) -> Path:
    path = tmp_path / "movie.vsi"
    tifffile.imwrite(path, np.zeros((4, 4), dtype="u1"))
    if frames is not None:
        np.save(path.with_name(path.name + ".frames.npy"), frames)
    if meta:
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))
    return path


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bioformats.BIOFORMATS_PYTHON_ENV, sys.executable)
    monkeypatch.setattr(bioformats, "RUNNER_SCRIPT", FAKE_RUNNER)


@pytest.fixture
def no_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bioformats.BIOFORMATS_PYTHON_ENV, raising=False)


# --- the base environment does not change --------------------------------------------


def _lock_names(path: Path) -> set[str]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {package["name"] for package in document["package"]}


def test_the_base_lock_and_environment_hold_no_java() -> None:
    assert not _lock_names(REPO / "conda-lock.yml") & JAVA_PACKAGES
    spec = yaml.safe_load((REPO / "environment.yml").read_text(encoding="utf-8"))
    names = {
        re.split(r"[\s=<>!]", d, maxsplit=1)[0] for d in spec["dependencies"] if isinstance(d, str)
    }
    assert not names & JAVA_PACKAGES, names & JAVA_PACKAGES
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8").lower()
    for name in ("jpype", "scyjava", "pyjnius", "javabridge", "bioio-bioformats"):
        assert f'"{name}' not in pyproject, name


def test_the_fourth_lock_holds_the_stack_on_every_platform() -> None:
    document = yaml.safe_load((REPO / "bioformats/conda-lock.yml").read_text(encoding="utf-8"))
    platforms = set(document["metadata"]["platforms"])
    assert platforms == {"linux-64", "osx-64", "osx-arm64", "win-64"}
    for name in ("python", "openjdk", "jpype1", "bftools"):
        found = {p["platform"] for p in document["package"] if p["name"] == name}
        assert found == platforms, (name, found)
    bftools = {p["version"] for p in document["package"] if p["name"] == "bftools"}
    assert all(v.startswith("8.0.") for v in bftools), bftools


def test_the_base_environment_imports_no_java(tmp_path: Path) -> None:
    """Choosing and trying to open a ``.vsi`` loads no Java binding into the base process."""
    path = _vsi(tmp_path)
    code = (
        "import sys\n"
        "import tether, tether.io, tether.io.adapters as a, tether.io.bioformats, tether.io.movie\n"
        f"p = {str(path)!r}\n"
        "assert a.select_adapter(p).name == 'cellSens VSI'\n"
        "try:\n    a.open_movie_source(p)\nexcept a.UnsupportedContainerError:\n    pass\n"
        f"java = [m for m in sys.modules if m.split('.')[0] in {JAVA_MODULES!r}]\n"
        "assert not java, java\n"
    )
    env = {k: v for k, v in os.environ.items() if k != bioformats.BIOFORMATS_PYTHON_ENV}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_the_runner_script_imports_java_only_inside_main() -> None:
    """At module level the runner needs the standard library only (and never ``tether``)."""
    tree = ast.parse(bioformats.RUNNER_SCRIPT.read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert top <= set(sys.stdlib_module_names) | {"__future__"}, top
    assert "tether" not in top


# --- the adapter ---------------------------------------------------------------------


def test_a_vsi_goes_to_the_cellsens_adapter(tmp_path: Path) -> None:
    assert select_adapter(_vsi(tmp_path)) is BIOFORMATS_ADAPTER


def test_a_vsi_name_on_other_content_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "movie.vsi"
    path.write_bytes(b"not a tiff header")
    with pytest.raises(UnsupportedContainerError, match="content is not a cellSens VSI"):
        select_adapter(path)


def test_without_the_stack_the_message_says_how_to_install_it(tmp_path, no_stack) -> None:
    with pytest.raises(BioformatsUnavailableError) as err:
        open_movie_source(_vsi(tmp_path))
    message = str(err.value)
    assert "micromamba create" in message
    assert "bioformats/conda-lock.yml" in message
    assert bioformats.BIOFORMATS_PYTHON_ENV in message
    assert isinstance(err.value, UnsupportedContainerError)


def test_a_python_that_is_not_there_says_how_to_install_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(bioformats.BIOFORMATS_PYTHON_ENV, str(tmp_path / "no" / "python"))
    with pytest.raises(BioformatsUnavailableError, match="not a file"):
        open_movie_source(_vsi(tmp_path))


def test_runner_info_without_the_stack(no_stack) -> None:
    with pytest.raises(BioformatsUnavailableError, match="micromamba create"):
        runner_info()


# --- the pipe, against the stand-in runner -------------------------------------------


def test_runner_info_through_the_pipe(fake_runner) -> None:
    assert runner_info()["bioformats"] == "fake"


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("lossy", LossyCodecError),
        ("invalid_pixel", InvalidPixelError),
        ("unsupported", UnsupportedContainerError),
        ("not_found", FileNotFoundError),
        ("unavailable", BioformatsUnavailableError),
        ("error", BioformatsRunnerError),
    ],
)
def test_each_refusal_is_its_exception(tmp_path, fake_runner, kind, error) -> None:
    path = _vsi(tmp_path, refuse=[kind, f"refused as {kind}"])
    with pytest.raises(error, match=f"refused as {kind}"):
        open_movie_source(path)


def test_a_runner_that_dies_is_a_runner_error(tmp_path, fake_runner) -> None:
    path = _vsi(tmp_path, crash=True)
    with pytest.raises(BioformatsRunnerError, match="exit status 3") as err:
        open_movie_source(path)
    assert "noise on stderr" in str(err.value), "the message carries the runner's last output"


def test_a_python_that_does_not_start_is_unavailable(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "python"
    fake.write_text("not a program")
    monkeypatch.setenv(bioformats.BIOFORMATS_PYTHON_ENV, str(fake))
    with pytest.raises(BioformatsUnavailableError, match="did not start"):
        open_movie_source(_vsi(tmp_path))


def test_the_source_keeps_the_file_metadata_and_stops_the_runner(tmp_path, fake_runner) -> None:
    frames = np.arange(3 * 4 * 5, dtype="<u2").reshape(3, 4, 5)
    path = _vsi(
        tmp_path, frames, metadata={"frame_time": 0.03, "pixel_size": 0.16, "channels": None}
    )
    source = open_movie_source(path)
    process = source._runner._proc
    assert source.series == 1 and source.format == "CellSens VSI"
    assert source.metadata.frame_time.origin == "file"
    assert source.metadata.pixel_size.value == pytest.approx(0.16)
    assert source.metadata.channels.origin == "unknown"
    np.testing.assert_array_equal(source.read_frames(1, 3), frames[1:3])
    assert "open" in repr(source)
    source.close()
    assert process.poll() == 0, "close stops the runner process"
    assert "closed" in repr(source)


def test_the_runner_helpers_find_the_jar_and_the_java_runtime(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("bf_runner_probe", bioformats.RUNNER_SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.delenv("TETHER_BIOFORMATS_JAR", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    assert runner.find_jar(tmp_path) is None
    assert runner.find_java_home(tmp_path) is None
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "bioformats_package.jar").write_bytes(b"PK")
    (tmp_path / "lib" / "jvm").mkdir(parents=True)
    (tmp_path / "lib" / "jvm" / "release").write_text('JAVA_VERSION="21"\n')
    assert runner.find_jar(tmp_path) == tmp_path / "bin" / "bioformats_package.jar"
    assert runner.find_java_home(tmp_path) == tmp_path / "lib" / "jvm"
    monkeypatch.setenv("TETHER_BIOFORMATS_JAR", str(tmp_path / "x.jar"))
    assert runner.find_jar(tmp_path) == tmp_path / "x.jar"
