# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The service commands, their registry, their text form and their routes (#892, ADR-0100).

The parity rule of PRD §7.11 says each action of the application has one Qt-free service
command and one command-line route. These tests hold the parts of that rule that can be
checked here: each registered command has a route, each request survives its text form,
the package loads no Qt module, and the reference page lists what the registry lists.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import math
import random
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any, ClassVar

import pytest

import tether.cli as cli
from tether import services
from tether.services import base, registry

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "src" / "tether" / "services"
CLI_PAGE = ROOT / "docs" / "cli.md"

# The top-level names of the Qt bindings and of the libraries built on them.
_QT_MODULES = (
    "PySide6",
    "PySide2",
    "PyQt6",
    "PyQt5",
    "shiboken6",
    "shiboken2",
    "qtpy",
    "pyqtgraph",
    "napari",
    "superqt",
    "magicgui",
    "vispy",
)

# One command line for each registered command, with only the required arguments. A new
# command needs an entry here, so that its route is shown to build its request.
_SAMPLE_ARGV: dict[str, list[str]] = {
    "extract": ["extract", "movie.tif", "-o", "out.tether"],
    "batch": ["batch", "a.tif", "b.tif", "-d", "out"],
}
_SAMPLE_REQUIRED: dict[str, dict[str, Any]] = {
    "extract": {"movie": "movie.tif", "output": "out.tether"},
    "batch": {"movies": ("a.tif", "b.tif"), "out_dir": "out"},
}


# --- The registry -------------------------------------------------------------------


def test_the_registry_names_each_command_once_with_its_types() -> None:
    names = [entry.name for entry in services.COMMANDS]
    assert names == ["extract", "batch"]
    assert len(set(names)) == len(names)
    for entry in services.COMMANDS:
        assert issubclass(entry.request_type, base.Request)
        assert entry.name == entry.request_type.COMMAND
        assert isinstance(entry.request_type.VERSION, int) and entry.request_type.VERSION >= 1
        assert dataclasses.is_dataclass(entry.result_type)
        assert typing.get_type_hints(entry.run)["return"] is entry.result_type
        assert services.command(entry.name) is entry


def test_a_command_whose_request_names_another_command_is_refused() -> None:
    with pytest.raises(ValueError, match="COMMAND is 'extract'"):
        registry.ServiceCommand(
            name="other",
            request_type=services.ExtractRequest,
            result_type=services.ExtractResult,
            run=lambda request: request,
            summary="",
        )


def test_run_refuses_a_request_of_the_wrong_type() -> None:
    @dataclasses.dataclass(frozen=True, kw_only=True)
    class Imposter(base.Request):
        COMMAND: ClassVar[str] = "extract"

    with pytest.raises(TypeError, match="expected ExtractRequest, got Imposter"):
        services.run(Imposter())
    with pytest.raises(KeyError):
        services.command("no-such-command")


# --- The command-line routes ---------------------------------------------------------


def test_every_registered_command_has_a_command_line_route() -> None:
    assert cli.unrouted_commands() == []
    subparsers = next(
        a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    names = [entry.name for entry in services.COMMANDS]
    assert list(subparsers.choices)[: len(names)] == names
    # `tether info` (#655) only reads a project, and `tether metadata` (#911) shows or edits
    # stored movie metadata through `Project`: plain subcommands, not service commands.
    assert list(subparsers.choices)[len(names) :] == ["info", "metadata"]


def test_a_registered_command_with_no_route_fails(monkeypatch) -> None:
    fake = registry.ServiceCommand(
        name="frobnicate",
        request_type=type("FrobnicateRequest", (base.Request,), {"COMMAND": "frobnicate"}),
        result_type=services.ExtractResult,
        run=lambda request: request,
        summary="a command nobody routed",
    )
    monkeypatch.setattr(cli, "COMMANDS", (*services.COMMANDS, fake))
    assert cli.unrouted_commands() == ["frobnicate"]
    with pytest.raises(KeyError, match="frobnicate"):
        cli.build_parser()


@pytest.mark.parametrize("entry", services.COMMANDS, ids=lambda e: e.name)
def test_each_route_builds_its_registered_request_with_the_request_defaults(entry) -> None:
    assert entry.name in _SAMPLE_ARGV, f"add a sample command line for {entry.name!r}"
    args = cli.build_parser().parse_args(_SAMPLE_ARGV[entry.name])
    request = cli._ROUTES[entry.name].to_request(args)
    assert type(request) is entry.request_type
    # The command line and the request agree on every default: an option left out on the
    # command line gives the request a recipe gets when it leaves the field out.
    assert request == entry.request_type(**_SAMPLE_REQUIRED[entry.name])


def test_the_negative_batch_flags_become_positive_fields() -> None:
    args = cli.build_parser().parse_args([*_SAMPLE_ARGV["batch"], "--no-idealize", "--no-defer"])
    request = cli._batch_request(args)
    assert request.idealize is False
    assert request.defer_if_unavailable is False


# --- The text form ------------------------------------------------------------------

_TRICKY_TEXT = ["", "movie.tif", "dir with space/m é.tif", 'q"uo\\te', "line\nbreak", "☃"]
_TRICKY_FLOATS = [0.0, -0.0, 0.5, 1e-300, 5e-324, 1.7976931348623157e308, -2.5, 3.0]


def _random_value(rng: random.Random, hint: Any) -> Any:
    optional, inner = base._allows_none(hint)
    if optional and rng.random() < 0.3:
        return None
    if typing.get_origin(inner) is tuple:
        item = typing.get_args(inner)[0]
        return tuple(_random_value(rng, item) for _ in range(rng.randint(0, 4)))
    if inner is bool:
        return rng.random() < 0.5
    if inner is int:
        return rng.choice([0, 1, -1, 21, 2**63, rng.randint(-(10**6), 10**6)])
    if inner is float:
        return rng.choice([*_TRICKY_FLOATS, rng.uniform(-1e6, 1e6), rng.random()])
    if inner is str:
        if rng.random() < 0.5:
            return rng.choice(_TRICKY_TEXT)
        return "".join(chr(rng.randint(32, 0x2FFF)) for _ in range(rng.randint(0, 12)))
    raise AssertionError(f"no generator for {hint!r}")


def _random_request(rng: random.Random, cls: type[base.Request]) -> base.Request:
    hints = typing.get_type_hints(cls)
    return cls(**{f.name: _random_value(rng, hints[f.name]) for f in dataclasses.fields(cls)})


@pytest.mark.parametrize("entry", services.COMMANDS, ids=lambda e: e.name)
def test_each_request_converts_to_text_and_back_unchanged(entry) -> None:
    # A property test with a fixed seed: 300 random requests of each registered type.
    rng = random.Random(892)
    for _ in range(300):
        request = _random_request(rng, entry.request_type)
        text = services.request_to_text(request)
        back = services.request_from_text(text)
        assert back == request, text
        assert type(back) is entry.request_type
        assert services.request_to_text(back) == text
        # -0.0 == 0.0, so compare the sign of each float as well.
        for f in dataclasses.fields(request):
            value = getattr(request, f.name)
            if isinstance(value, float):
                assert math.copysign(1, getattr(back, f.name)) == math.copysign(1, value)


def test_the_text_form_is_one_sorted_line_with_every_field() -> None:
    text = services.request_to_text(services.ExtractRequest(movie="m.tif", output="m.tether"))
    assert "\n" not in text and " " not in text
    data = json.loads(text)
    assert list(data) == ["command", "fields", "version"]
    assert data["command"] == "extract" and data["version"] == 1
    assert list(data["fields"]) == sorted(data["fields"])
    assert set(data["fields"]) == {f.name for f in dataclasses.fields(services.ExtractRequest)}
    assert data["fields"]["window"] == 21 and data["fields"]["tmap"] is None


def _text(command: str = "extract", version: Any = 1, **fields: Any) -> str:
    return json.dumps({"command": command, "version": version, "fields": fields})


def test_a_missing_field_with_a_default_takes_the_default() -> None:
    request = services.request_from_text(_text(movie="m.tif", output="o.tether", pair_tol=2))
    assert request == services.ExtractRequest(movie="m.tif", output="o.tether")
    assert isinstance(request.pair_tol, float)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "must be a JSON object"),
        (_text("frobnicate"), "unknown command 'frobnicate'"),
        (json.dumps({"version": 1, "fields": {}}), "unknown command None"),
        (_text(version=2, movie="m", output="o"), "version 2 is not supported"),
        (_text(version=True, movie="m", output="o"), "version True is not supported"),
        (_text(movie="m"), "missing field 'output'"),
        (_text(movie="m", output="o", colour="red"), r"unknown field\(s\).*colour"),
        (_text(movie="m", output=None), "'output' may not be null"),
        (_text(movie="m", output="o", window="21"), "'window' must be int, not str"),
        (_text(movie="m", output="o", window=True), "'window' must be int, not bool"),
        (_text(movie="m", output="o", window=21.0), "'window' must be int, not float"),
        (_text(movie="m", output="o", pair_tol=False), "'pair_tol' must be float"),
        (_text(movie="m", output="o", overwrite=1), "'overwrite' must be bool"),
        (_text("batch", movies="a.tif", out_dir="o"), "'movies' must be a list of strings"),
        (_text("batch", movies=["a", 1], out_dir="o"), "'movies' must be a list of strings"),
        (
            json.dumps({"command": "extract", "version": 1, "fields": {}, "x": 1}),
            r"unknown request key\(s\)",
        ),
        (json.dumps({"command": "extract", "version": 1}), "needs a 'fields' object"),
    ],
)
def test_a_bad_request_text_is_refused_with_a_reason(text: str, message: str) -> None:
    with pytest.raises(services.RequestError, match=message):
        services.request_from_text(text)


def test_a_value_with_no_text_form_is_refused() -> None:
    with pytest.raises(services.RequestError, match="finite"):
        services.request_to_text(
            services.ExtractRequest(movie="m", output="o", pair_tol=float("nan"))
        )
    with pytest.raises(services.RequestError, match="finite"):
        services.request_from_text(_text(movie="m", output="o", pair_tol=1e999))
    # Greptile finding on #1058: an integer too large for a float is a bad value too.
    with pytest.raises(services.RequestError, match="finite"):
        services.request_from_text(_text(movie="m", output="o", pair_tol=10**309))
    with pytest.raises(TypeError, match="not a request"):
        services.request_to_text("extract")  # type: ignore[arg-type]


def test_an_unsupported_field_type_is_a_programming_error() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        base._check("x", {"a": 1}, dict)
    with pytest.raises(TypeError, match="unsupported"):
        base._check("x", (1.0,), tuple[float, ...])
    with pytest.raises(TypeError, match="unsupported"):
        base._allows_none(int | str | None)


# --- The commands' own refusals -------------------------------------------------------


def test_extract_refuses_a_detection_mode_with_a_tdat_as_a_failed_command(tmp_path) -> None:
    request = services.ExtractRequest(
        movie=str(tmp_path / "m.tif"),
        output=str(tmp_path / "o.tether"),
        detection_mode="intensity",
        tdat=str(tmp_path / "d.tdat"),
    )
    with pytest.raises(services.CommandFailed, match="cannot be combined with --tdat"):
        services.run(request)
    assert not (tmp_path / "o.tether").exists()


def test_extract_refuses_an_empty_detection_mode_as_a_failed_command(tmp_path) -> None:
    # Only None means "no choice": a request from text may hold "", which must not
    # silently become the default detector.
    request = services.ExtractRequest(
        movie=str(tmp_path / "m.tif"), output=str(tmp_path / "m.tether"), detection_mode=""
    )
    with pytest.raises(services.CommandFailed, match="detection_mode must be one of"):
        services.run(request)


def test_batch_refuses_an_unknown_policy_before_it_writes(tmp_path) -> None:
    # Greptile finding on #1058: a recipe has no argparse `choices` to catch a typo.
    out = tmp_path / "out"
    request = services.BatchRequest(movies=("m.tif",), out_dir=str(out), policy="typo")
    with pytest.raises(services.RequestRefused, match="policy must be one of"):
        services.run(request)
    assert not out.exists()


def test_batch_refuses_an_empty_movie_list_before_it_writes(tmp_path) -> None:
    out = tmp_path / "out"
    with pytest.raises(services.RequestRefused, match="at least one movie"):
        services.run(services.BatchRequest(movies=(), out_dir=str(out)))
    assert not out.exists()


def test_batch_reports_an_output_directory_it_cannot_make_as_a_failed_command(tmp_path) -> None:
    # CodeRabbit finding on #1058: an OSError must not escape as a traceback.
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    request = services.BatchRequest(movies=("m.tif",), out_dir=str(blocker / "out"))
    with pytest.raises(services.CommandFailed, match="cannot create output directory"):
        services.run(request)


def test_batch_reports_a_log_it_cannot_write_as_a_failed_command(tmp_path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    request = services.BatchRequest(
        movies=("m.tif",), out_dir=str(tmp_path / "out"), log=str(blocker / "log.jsonl")
    )
    with pytest.raises(services.CommandFailed, match="cannot write the batch output or log"):
        services.run(request)


def test_tether_batch_exits_1_with_one_line_when_it_cannot_make_its_output(
    tmp_path, capsys
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    code = cli.main(["batch", "m.tif", "--out-dir", str(blocker / "out")])
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("tether batch: cannot create output directory")
    assert "Traceback" not in err


# --- No Qt ---------------------------------------------------------------------------


def _imports(path: Path) -> list[str]:
    """Every module that *path* imports, at any depth (function-level imports too)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            if node.module == "tether":  # `from tether import gui` names a subpackage
                found.extend(f"tether.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level:
            raise AssertionError(f"{path.name}: use absolute imports in tether.services")
    return found


def _is_qt(module: str) -> bool:
    top = module.split(".")[0]
    return top in _QT_MODULES or module == "tether.gui" or module.startswith("tether.gui.")


def test_the_service_sources_import_no_qt_and_no_gui_module() -> None:
    sources = sorted(SERVICES_DIR.glob("*.py"))
    assert sources, SERVICES_DIR
    bad = {p.name: m for p in sources for m in _imports(p) if _is_qt(m)}
    assert bad == {}


@pytest.mark.parametrize(
    "source",
    [
        "def run():\n    from tether import gui\n",
        "def run():\n    from tether.gui import app\n",
        "def run():\n    import tether.gui.app\n",
        "def run():\n    from PySide6 import QtCore\n",
    ],
)
def test_the_import_scan_finds_a_gui_or_qt_import_inside_a_function(tmp_path, source) -> None:
    path = tmp_path / "fake_command.py"
    path.write_text(source, encoding="utf-8")
    assert any(_is_qt(m) for m in _imports(path))


def test_importing_the_services_and_what_they_run_loads_no_qt_module() -> None:
    # In a fresh interpreter: the package, each of its modules, and each tether module a
    # command imports when it runs. Nothing else in the process may have loaded Qt first.
    core = sorted(
        {m for p in SERVICES_DIR.glob("*.py") for m in _imports(p) if m.startswith("tether.")}
    )
    code = (
        "import importlib, pkgutil, sys\n"
        "import tether.services as s\n"
        "for info in pkgutil.walk_packages(s.__path__, 'tether.services.'):\n"
        "    importlib.import_module(info.name)\n"
        f"for name in {core!r}:\n"
        "    importlib.import_module(name)\n"
        f"qt = {_QT_MODULES!r}\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] in qt]\n"
        "print(sorted(loaded + [m for m in sys.modules if m.startswith('tether.gui')]))\n"
    )
    assert "tether.project.extract" in core and "tether.project.batch" in core
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "[]", out.stdout


def test_importing_the_services_loads_no_third_party_module() -> None:
    # `tether --version` builds the parser from the registry, so the registry must stay
    # as light as argparse: the core is imported only when a command runs.
    code = (
        "import sys\n"
        "import tether.cli, tether.services\n"
        "heavy = ('numpy', 'h5py', 'scipy', 'skimage', 'tifffile', 'pandas', 'numba')\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "[]", out.stdout


# --- The reference page ----------------------------------------------------------------

_BEGIN = "<!-- service-commands:begin (made from tether.services.registry; do not edit) -->"
_END = "<!-- service-commands:end -->"


def test_the_reference_page_lists_the_commands_of_the_registry() -> None:
    text = CLI_PAGE.read_text(encoding="utf-8")
    assert text.count(_BEGIN) == 1 and text.count(_END) == 1
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
    expected = registry.reference_table()
    assert block == expected, (
        "docs/cli.md: replace the service-command table with this one:\n" + expected
    )


def test_the_example_request_on_the_page_is_the_text_form_of_its_command_line() -> None:
    text = CLI_PAGE.read_text(encoding="utf-8").split(_END, 1)[1]
    example = text.split("```json\n", 1)[1].split("```", 1)[0]
    args = cli.build_parser().parse_args(["extract", "movie.tif", "-o", "movie.tether"])
    request = cli._extract_request(args)
    assert json.loads(example) == json.loads(services.request_to_text(request))
