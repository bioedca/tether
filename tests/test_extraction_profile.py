# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The extraction profile and its command-line routes (#349, ADR-0129).

The field checks enumerate ``dataclasses.fields(ExtractOptions)``, so a new field
fails here until it is in the profile, in both requests and in PRD §11.2. The end-to-end
tests run ``tether extract`` and ``tether batch`` on the synthetic movie of
``test_extract_cli`` and read ``/settings/extraction`` back.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("numpy")

from tether import cli  # noqa: E402
from tether.imaging.layout import ChannelLayout  # noqa: E402
from tether.project.extract import ExtractOptions  # noqa: E402
from tether.services import (  # noqa: E402
    BatchRequest,
    ExtractionFields,
    ExtractionProfile,
    ExtractRequest,
    RequestError,
    profile_from_text,
    profile_to_text,
    read_profile,
    request_from_text,
    write_profile,
)
from tether.services.profile import LAYOUT_FIELDS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CLI_PAGE = REPO / "docs" / "cli.md"
PRD = REPO / "docs" / "PRD.md"

_OPTION_FIELDS = [f.name for f in dataclasses.fields(ExtractOptions)]

#: The ExtractOptions fields a profile holds under other names; every other field is held
#: under its own name.
_CARRIED_AS: dict[str, tuple[str, ...]] = {"channel_layout": LAYOUT_FIELDS}

#: The two halves of the 64 x 96 synthetic movie of test_extract_cli, as rectangles.
_DONOR_RECT = (1, 1, 64, 48)
_ACCEPTOR_RECT = (1, 49, 64, 96)

#: A value for every ExtractOptions field, all valid together and all away from the default
#: except ``donor_side``, which a channel layout requires at ``left`` (it has its own test).
_NON_DEFAULT: dict[str, Any] = {
    "donor_side": "left",
    "channel_layout": ChannelLayout(
        kind="rectangles", donor_rect=_DONOR_RECT, acceptor_rect=_ACCEPTOR_RECT
    ),
    "detection_mode": "bandpass",
    "detection_threshold": 0.9,
    "detection_block": 25,
    "min_separation": 4.5,
    "prealign": "similarity",
    "prealign_upsample": 20,
    "prealign_low_sigma": 1.5,
    "prealign_high_sigma": 15.0,
    "pair_tol": 1.5,
    "rms_gate": 0.75,
    "window": 19,
    "coloc_distance": 2.5,
    "colocalization": "donor-anchored",
    "disk_radius": 2.5,
    "ring_inner": 5.0,
    "ring_outer": 7.0,
    "bg_window": 5,
}

#: ``_NON_DEFAULT`` as the fields of a profile.
_NON_DEFAULT_PROFILE: dict[str, Any] = {
    **{k: v for k, v in _NON_DEFAULT.items() if k != "channel_layout"},
    "channel_layout": "rectangles",
    "channel_layout_donor": None,
    "channel_layout_donor_rect": _DONOR_RECT,
    "channel_layout_acceptor_rect": _ACCEPTOR_RECT,
}

#: Every field name a profile holds.
_PROFILE_FIELDS = sorted(name for f in _OPTION_FIELDS for name in _CARRIED_AS.get(f, (f,)))

#: The PRD §11.2 row that registers each ExtractOptions field (by a phrase of its label).
_PRD_ROWS: dict[str, str] = {
    "donor_side": "Donor half (`donor_side`)",
    "channel_layout": "Channel layout (split geometry)",
    "detection_mode": "Particle detection mode",
    "detection_threshold": "Detection threshold (intensity mode)",
    "detection_block": "Detection block size",
    "min_separation": "Minimum spot separation (`min_separation`)",
    "prealign": "Registration prealign",
    "prealign_upsample": "Registration prealign",
    "prealign_low_sigma": "Registration prealign",
    "prealign_high_sigma": "Registration prealign",
    "pair_tol": "NN pairing tolerance",
    "rms_gate": "Registration RMS-residual gate",
    "window": "Aperture (PSF disk / BG ring)",
    "coloc_distance": "Colocalization distance",
    "colocalization": "Colocalization policy (`colocalization`)",
    "disk_radius": "Aperture (PSF disk / BG ring)",
    "ring_inner": "Aperture (PSF disk / BG ring)",
    "ring_outer": "Aperture (PSF disk / BG ring)",
    "bg_window": "Per-frame background",
}


# --- The fields ----------------------------------------------------------------------


def test_the_value_table_names_every_extract_option() -> None:
    # An ExtractOptions field added without a value here fails, and with it every test below.
    assert sorted(_NON_DEFAULT) == sorted(_OPTION_FIELDS)
    ExtractOptions(**_NON_DEFAULT)  # valid together


@pytest.mark.parametrize("cls", [ExtractionFields, ExtractionProfile, ExtractRequest, BatchRequest])
def test_each_profile_carrier_has_every_extract_option_with_its_default(cls) -> None:
    carried = {f.name: f for f in dataclasses.fields(cls)}
    library = {f.name: f.default for f in dataclasses.fields(ExtractOptions)}
    for option in _OPTION_FIELDS:
        names = _CARRIED_AS.get(option, (option,))
        missing = [name for name in names if name not in carried]
        assert not missing, f"{cls.__name__} lacks {missing} for ExtractOptions.{option}"
        for name in names:
            # detection_mode None is "no choice"; the layout fields are None for no layout.
            expected = library[name] if (name == option and name != "detection_mode") else None
            assert carried[name].default == expected, (cls.__name__, name)
    assert library["channel_layout"] is None


def test_a_profile_holds_the_extract_options_and_nothing_else() -> None:
    assert sorted(f.name for f in dataclasses.fields(ExtractionProfile)) == _PROFILE_FIELDS


def test_every_extract_option_is_registered_in_prd_11_2() -> None:
    assert sorted(_PRD_ROWS) == sorted(_OPTION_FIELDS), "map each new field to its §11.2 row"
    text = PRD.read_text(encoding="utf-8")
    section = text.split("### 11.2 Default parameter values", 1)[1].split("### 11.3", 1)[0]
    labels = [line.split("|")[1].strip() for line in section.splitlines() if line.startswith("| ")]
    missing = {f: row for f, row in _PRD_ROWS.items() if not any(row in lab for lab in labels)}
    assert not missing, f"PRD §11.2 has no row for {missing}"


# --- The text form -------------------------------------------------------------------


def test_a_profile_round_trips_every_field(tmp_path: Path) -> None:
    profile = ExtractionProfile(**_NON_DEFAULT_PROFILE)
    assert profile_from_text(profile_to_text(profile)) == profile
    path = tmp_path / "p.json"
    write_profile(profile, path)
    back = read_profile(path)
    assert back == profile
    assert back.extract_options() == ExtractOptions(**_NON_DEFAULT)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["command"] == "extraction-profile"
    assert data["version"] == 1
    assert sorted(data["fields"]) == _PROFILE_FIELDS  # every field is written
    assert data["fields"]["channel_layout_donor_rect"] == list(_DONOR_RECT)


def test_a_non_default_donor_side_round_trips() -> None:
    profile = profile_from_text(profile_to_text(ExtractionProfile(donor_side="right")))
    assert profile.extract_options() == ExtractOptions(donor_side="right")


@pytest.mark.parametrize(
    "options",
    [
        ExtractOptions(),
        ExtractOptions(**_NON_DEFAULT),
        ExtractOptions(donor_side="right"),
        ExtractOptions(channel_layout=ChannelLayout(kind="top-bottom", donor="bottom")),
        ExtractOptions(channel_layout=ChannelLayout()),
    ],
    ids=["defaults", "non-default", "donor-right", "top-bottom", "side-by-side"],
)
def test_a_profile_made_from_options_gives_the_same_options(options: ExtractOptions) -> None:
    profile = ExtractionProfile.from_options(options)
    assert profile_from_text(profile_to_text(profile)).extract_options() == options


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"channel_layout_donor": "top"}, "need a channel_layout kind"),
        ({"channel_layout": "diagonal"}, "kind must be one of"),
        ({"channel_layout": "top-bottom", "channel_layout_donor": "left"}, "donor for a"),
        ({"channel_layout": "rectangles", "channel_layout_donor_rect": [1, 1, 2, 2]}, "both"),
        (
            {
                "channel_layout": "rectangles",
                "channel_layout_donor_rect": [1, 1, 2],
                "channel_layout_acceptor_rect": [1, 3, 2, 4],
            },
            "4 values",
        ),
        ({"channel_layout_donor_rect": [1, True, 2, 2]}, "list of integers"),
        ({"channel_layout": "top-bottom", "donor_side": "right"}, "donor_side applies only"),
    ],
)
def test_a_bad_channel_layout_is_refused(tmp_path: Path, fields: dict, message: str) -> None:
    path = tmp_path / "bad.json"
    text = {"command": "extraction-profile", "fields": fields, "version": 1}
    path.write_text(json.dumps(text), encoding="utf-8")
    with pytest.raises(RequestError, match=message):
        read_profile(path)


def test_a_field_left_out_keeps_its_default(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(
        '{"command":"extraction-profile","fields":{"disk_radius":2.5},"version":1}',
        encoding="utf-8",
    )
    assert read_profile(path) == ExtractionProfile(disk_radius=2.5)
    assert read_profile(path).extract_options() == ExtractOptions(disk_radius=2.5)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ('{"command":"extract","fields":{},"version":1}', "extraction-profile"),
        ('{"command":"extraction-profile","fields":{"frobs":1},"version":1}', "frobs"),
        ('{"command":"extraction-profile","fields":{"window":21.0},"version":1}', "window"),
        ('{"command":"extraction-profile","fields":{"window":20},"version":1}', "odd"),
        ('{"command":"extraction-profile","fields":{"ring_outer":11.0},"version":1}', "fit"),
        ('{"command":"extraction-profile","fields":{},"version":2}', "version"),
    ],
)
def test_a_bad_profile_is_refused_with_its_path(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RequestError, match=message) as info:
        read_profile(path)
    assert str(path) in str(info.value)


def test_a_missing_profile_file_is_refused_with_its_path(tmp_path: Path) -> None:
    with pytest.raises(RequestError, match="cannot read extraction profile"):
        read_profile(tmp_path / "absent.json")


def test_the_registry_does_not_read_a_profile_as_a_request() -> None:
    with pytest.raises(RequestError, match="unknown command"):
        request_from_text(profile_to_text(ExtractionProfile()))


# --- The command line ------------------------------------------------------------------


def _profile_file(tmp_path: Path, **fields: Any) -> Path:
    path = tmp_path / "profile.json"
    write_profile(ExtractionProfile(**fields), path)
    return path


def _parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def test_extract_profile_sets_every_field_of_the_request(tmp_path: Path) -> None:
    path = _profile_file(tmp_path, **_NON_DEFAULT_PROFILE)
    request = cli._extract_request(
        _parse("extract", "m.tif", "-o", "o.tether", "--profile", str(path))
    )
    assert request.extraction_values() == _NON_DEFAULT_PROFILE
    assert request.extract_options() == ExtractOptions(**_NON_DEFAULT)


def test_batch_profile_sets_every_field_of_the_request(tmp_path: Path) -> None:
    path = _profile_file(tmp_path, **_NON_DEFAULT_PROFILE)
    request = cli._batch_request(_parse("batch", "a.tif", "-d", "out", "--profile", str(path)))
    assert request.extraction_values() == _NON_DEFAULT_PROFILE


@pytest.mark.parametrize("flag", ["--window", "--donor-side", "--detection-mode", "--rms-gate"])
def test_extract_refuses_a_profile_beside_an_option_flag_even_at_its_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    default = {
        "--window": "21",
        "--donor-side": "left",
        "--detection-mode": "wavelet",
        "--rms-gate": "0.5",
    }[flag]
    path = _profile_file(tmp_path)
    argv = ["extract", "m.tif", "-o", str(tmp_path / "o.tether"), "--profile", str(path)]
    assert cli.main([*argv, flag, default]) == 1
    err = capsys.readouterr().err
    assert f"--profile cannot be combined with {flag}" in err
    assert not (tmp_path / "o.tether").exists()


def test_extract_reports_a_bad_profile_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "p.json"
    path.write_text('{"command":"extraction-profile","fields":{"window":20},"version":1}')
    assert cli.main(["extract", "m.tif", "-o", "o.tether", "--profile", str(path)]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert err[0].startswith(f"tether extract: extraction profile {path}")


def test_batch_refuses_a_bad_profile_before_it_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    rc = cli.main(["batch", "a.tif", "-d", str(out), "--profile", str(tmp_path / "absent.json")])
    assert rc == 2
    assert "cannot read extraction profile" in capsys.readouterr().err
    assert not out.exists()


# --- The --tdat rule -------------------------------------------------------------------


def test_the_tdat_rule_is_in_the_help_of_both_commands() -> None:
    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, cli.argparse._SubParsersAction))  # noqa: SLF001
    for name in ("extract", "batch"):
        text = " ".join(subparsers.choices[name].format_help().split())
        assert " ".join(cli._TDAT_RULE.split()) in text, name


def test_the_tdat_rule_is_on_the_reference_page() -> None:
    text = " ".join(CLI_PAGE.read_text(encoding="utf-8").split())
    assert "A `.tdat` sets `detection_mode` and `detection_threshold`, and no other option." in text
    assert "It is refused beside a choice of either field" in text


@pytest.mark.parametrize(
    "fields",
    [{"detection_mode": "wavelet"}, {"detection_threshold": 0.5}],
    ids=["mode", "threshold"],
)
@pytest.mark.parametrize(("command", "code"), [("extract", 1), ("batch", 2)])
def test_a_profile_that_chooses_detection_is_refused_beside_tdat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fields: dict, command: str, code: int
) -> None:
    path = _profile_file(tmp_path, **fields)
    tdat = tmp_path / "run.tdat"
    tdat.write_bytes(b"never read: the refusal comes first")
    out = tmp_path / "out"
    head = (
        ["extract", "m.tif", "-o", str(out)]
        if command == "extract"
        else ["batch", "a.tif", "-d", str(out)]
    )
    assert cli.main([*head, "--profile", str(path), "--tdat", str(tdat)]) == code
    assert "cannot be combined with --tdat" in capsys.readouterr().err
    assert not out.exists()


def test_the_batch_service_refuses_a_detection_choice_beside_tdat(tmp_path: Path) -> None:
    from tether.services import RequestRefused, run

    out = tmp_path / "out"
    request = BatchRequest(
        movies=("a.tif",), out_dir=str(out), tdat="run.tdat", detection_mode="bandpass"
    )
    with pytest.raises(RequestRefused, match="cannot be combined with --tdat"):
        run(request)
    assert not out.exists()


def test_the_batch_service_refuses_invalid_options_before_it_writes(tmp_path: Path) -> None:
    from tether.services import RequestRefused, run

    out = tmp_path / "out"
    with pytest.raises(RequestRefused, match="odd"):
        run(BatchRequest(movies=("a.tif",), out_dir=str(out), window=20))
    assert not out.exists()


# --- End to end on the synthetic movie -------------------------------------------------


def _movie_helpers():
    for module in ("scipy", "skimage", "tifffile", "h5py"):
        pytest.importorskip(module)
    import test_extract_cli  # noqa: PLC0415

    return test_extract_cli


#: Non-default fields the three-spot synthetic movie still extracts cleanly with.
_E2E = {"window": 19, "disk_radius": 2.5, "ring_inner": 5.0, "ring_outer": 7.0, "bg_window": 5}


def test_tether_extract_records_the_profile_values(tmp_path: Path) -> None:
    helpers = _movie_helpers()
    movie = helpers._make_movie(tmp_path)
    out = tmp_path / "p.tether"
    path = _profile_file(tmp_path, **_E2E)
    assert cli.main(["extract", str(movie), "-o", str(out), "--profile", str(path)]) == 0
    recorded = helpers._extraction_profile(out)
    assert {k: recorded[k] for k in _E2E} == _E2E


def test_tether_batch_records_a_non_default_profile_field(tmp_path: Path) -> None:
    helpers = _movie_helpers()
    movie = helpers._make_movie(tmp_path, name="movie_010.tif")
    out = tmp_path / "out"
    path = _profile_file(tmp_path, **_E2E)
    rc = cli.main(["batch", str(movie), "-d", str(out), "--no-idealize", "--profile", str(path)])
    assert rc == 0
    recorded = helpers._extraction_profile(out / "movie_010.tether")
    assert {k: recorded[k] for k in _E2E} == _E2E


def test_tdat_sets_the_detection_fields_and_leaves_the_profile_s_others(tmp_path: Path) -> None:
    helpers = _movie_helpers()
    movie = helpers._make_movie(tmp_path)
    tdat = helpers._write_tdat(tmp_path / "data.tdat", mode_code=3.0)  # bandpass
    out = tmp_path / "t.tether"
    path = _profile_file(tmp_path, **_E2E)
    argv = ["extract", str(movie), "-o", str(out), "--profile", str(path), "--tdat", str(tdat)]
    assert cli.main(argv) == 0
    recorded = helpers._extraction_profile(out)
    assert recorded["detection_mode"] == "bandpass"
    assert recorded["tdat_source"] == "data.tdat"
    assert {k: recorded[k] for k in _E2E} == _E2E
    untouched = set(_OPTION_FIELDS) - set(_E2E) - {"detection_mode", "detection_threshold"}
    defaults = dataclasses.asdict(ExtractOptions())
    assert {k: recorded[k] for k in untouched} == {k: defaults[k] for k in untouched}


def test_a_profile_example_on_the_reference_page_reads(tmp_path: Path) -> None:
    section = CLI_PAGE.read_text(encoding="utf-8").split("### Extraction profiles", 1)[1]
    example = re.search(r"```json\n(.*?)```", section, re.S)
    assert example, "the Extraction profiles section has no JSON example"
    path = tmp_path / "example.json"
    path.write_text("".join(example.group(1).split("\n")), encoding="utf-8")
    assert read_profile(path) == ExtractionProfile(disk_radius=2.5, ring_inner=5.0, window=19)


def test_the_layout_example_on_the_reference_page_reads(tmp_path: Path) -> None:
    section = CLI_PAGE.read_text(encoding="utf-8").split("### Extraction profiles", 1)[1]
    examples = re.findall(r"```json\n(.*?)```", section, re.S)
    assert len(examples) >= 2, "the Extraction profiles section has no layout example"
    path = tmp_path / "layout.json"
    path.write_text("".join(line.strip() for line in examples[1].splitlines()), encoding="utf-8")
    options = read_profile(path).extract_options()
    assert options.channel_layout == ChannelLayout(kind="top-bottom", donor="bottom")


def test_tether_extract_splits_by_the_profile_s_channel_layout(tmp_path: Path) -> None:
    helpers = _movie_helpers()
    movie = helpers._make_movie(tmp_path)
    out = tmp_path / "rect.tether"
    path = _profile_file(
        tmp_path,
        channel_layout="rectangles",
        channel_layout_donor_rect=_DONOR_RECT,
        channel_layout_acceptor_rect=_ACCEPTOR_RECT,
    )
    assert cli.main(["extract", str(movie), "-o", str(out), "--profile", str(path)]) == 0
    recorded = helpers._extraction_profile(out)["channel_layout"]
    assert recorded["kind"] == "rectangles"
    assert recorded["donor_rect"] == list(_DONOR_RECT)
    assert recorded["acceptor_rect"] == list(_ACCEPTOR_RECT)
