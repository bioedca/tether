# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the measured per-trace vbFRET fixtures of #797 and their expected tables.

Two CC-BY-4.0 records by Nicholas Marzano hold measured two-colour smFRET traces with the
per-trace vbFRET idealization the authors analysed. Each trace is one ``.dat`` file with five
columns: frame, donor, acceptor, FRET (= A / (D + A)), idealized FRET -- vbFRET's "Idealized
Traces" export.

* PPR-RNA, Zenodo 10.5281/zenodo.11323228, ``Figure_2.zip``: the whole condition folder
  ``Figure_2/Validation_data/RNAanticognate`` (114 traces).
* Hsp70/Hsp90, Zenodo 10.5281/zenodo.17118152, ``Fig1_FigS1_FigS2.7z``: the whole condition
  folder ``Fig1_FigS1_FigS2/single-molecule/KJG/KJG_231027`` (68 traces). The folder #797 names
  first, ``KJE/KJE_231212``, is 925 kB even as ``.tar.xz``, over the 512 KiB limit of a
  plain-git fixture, so the issue's "or smaller folders" applies.

``fixtures`` copies the member bytes **verbatim** into one ``.tar.xz`` for each set (PRD §2.2,
"What the tests can use", rule 2: a test starts from the original bytes). Every member keeps its
file name; the tar header is normalized (mtime 0, owner 0, mode 0644) so only the bytes carry
information. ``tests/_marzano_dat.py`` reads them back.

``tables`` runs ``nmarzano/smFRET_module`` v1.0.3 (Zenodo 10.5281/zenodo.16791519, MIT), outside
the product, on the committed fixtures and writes its dwell-time and transition tables as the
expected files of ``tests/test_marzano_vbfret.py``. It runs the module's own functions in the
order of its ``combine_technical_repeats`` and ``master_tdp_cleanup_func``:

1. ``Utilities.Data_analysis.remove_outliers(df, "hist")``: drop each frame whose raw FRET is not
   inside (-0.5, 1.5).
2. ``processing_scripts.TDP_processing.calculate_dwell_time``: the runs of constant idealized
   FRET of each molecule (a molecule with one run is dropped).
3. ``TDP_processing.generate_transitions``: one row for each pair of adjacent runs.
4. ``TDP_processing.remove_outliers_tdp``: drop a transition whose levels leave [-0.5, 1.5].

One step is ours: ``Data_analysis.file_reader`` takes the molecule number from
``filename.split('\\\\')[1]``, a Windows path, so on any other system it fails. This script lists
the members and builds the same data frame itself -- ``pd.read_table(sep=r"\\s+",
header=None)`` and the molecule number from the file-name prefix -- and the module's own functions
do everything after that.

Usage (dev only, never in CI)::

    uv run --no-project --with py7zr python scripts/make_marzano_fixture.py fixtures \\
        --source <dir with Figure_2.zip and Fig1_FigS1_FigS2.7z>
    uv run --no-project --with pandas python scripts/make_marzano_fixture.py tables \\
        --module <unpacked smFRET_module-v1.0.3 directory>
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import lzma
import platform
import sys
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
TABLES = FIXTURES / "reference" / "marzano"

#: set id -> (archive, folder inside it, committed fixture, archive SHA-256, treatment name)
SETS = {
    "ppr": (
        "Figure_2.zip",
        "Figure_2/Validation_data/RNAanticognate/",
        FIXTURES / "marzano_ppr_rnaanticognate.tar.xz",
        "f6f8beffac601d933d6e897ce808c1fcb45f27dc226ab6f11de6118bb50fcd10",
        "RNAanticognate",
    ),
    "chaperone": (
        "Fig1_FigS1_FigS2.7z",
        "Fig1_FigS1_FigS2/single-molecule/KJG/KJG_231027/",
        FIXTURES / "marzano_chaperone_kjg_231027.tar.xz",
        "92f94bbd6373edab7875b4dc21e6e2cfa00efaf39a8a45e5bbc74573abd2aac7",
        "KJG_231027",
    ),
}

#: The SHA-256 of ``nmarzano-smFRET_module-v1.0.3.zip`` from Zenodo 16791519.
MODULE_ZIP_SHA256 = "54f17fe4e0bd7bccaca172856cbe58fc4107034e378ea0b99cc01cb4ed2ab8a6"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _members(archive: Path, folder: str) -> dict[str, bytes]:
    """The ``.dat`` members directly inside ``folder``, by file name, with their bytes."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.startswith(folder) and n.endswith(".dat")]
            return {n[len(folder) :]: zf.read(n) for n in names if "/" not in n[len(folder) :]}
    import tempfile

    import py7zr  # dev-only dependency of this script

    with py7zr.SevenZipFile(archive) as zf, tempfile.TemporaryDirectory() as tmp:
        names = [n for n in zf.getnames() if n.startswith(folder) and n.endswith(".dat")]
        names = [n for n in names if "/" not in n[len(folder) :]]
        zf.extract(path=tmp, targets=names)
        return {n[len(folder) :]: (Path(tmp) / n).read_bytes() for n in names}


def _tar_xz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(
        fileobj=buf, mode="w:xz", format=tarfile.USTAR_FORMAT, preset=9 | lzma.PRESET_EXTREME
    ) as tf:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mode = 0o644
            info.mtime = 0
            tf.addfile(info, io.BytesIO(members[name]))
    return buf.getvalue()


def make_fixtures(source: Path) -> None:
    for set_id, (archive_name, folder, out, sha, _) in SETS.items():
        archive = source / archive_name
        actual = _sha256(archive.read_bytes())
        if actual != sha:
            sys.exit(f"{archive}: SHA-256 {actual}, expected {sha}")
        members = _members(archive, folder)
        out.write_bytes(_tar_xz(members))
        print(
            f"{set_id}: {len(members)} traces -> {out.relative_to(REPO)} ({out.stat().st_size} B)"
        )


def _load(module_dir: Path, relative: str):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, module_dir / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_tables(module_dir: Path) -> None:
    import pandas as pd

    util = _load(module_dir, "src/Utilities/Data_analysis.py")
    ps = _load(module_dir, "src/processing_scripts/TDP_processing.py")
    TABLES.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "record_version": 1,
        "tool": {
            "name": "nmarzano/smFRET_module",
            "version": "1.0.3",
            "source": "doi:10.5281/zenodo.16791519",
            "archive_sha256": MODULE_ZIP_SHA256,
            "functions": [
                "Utilities.Data_analysis.remove_outliers(df, 'hist')",
                "processing_scripts.TDP_processing.calculate_dwell_time",
                "processing_scripts.TDP_processing.generate_transitions",
                "processing_scripts.TDP_processing.remove_outliers_tdp",
            ],
        },
        "command": "python scripts/make_marzano_fixture.py tables --module <smFRET_module-v1.0.3>",
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "os": platform.platform(),
        "inputs": {},
        "outputs": {},
    }
    for set_id, (_, _, fixture, _, treatment) in SETS.items():
        frames = []
        with tarfile.open(fixture, "r:xz") as tf:
            for info in sorted(tf.getmembers(), key=lambda m: m.name):
                data = pd.read_table(tf.extractfile(info), sep=r"\s+", header=None)
                data["molecule number"] = info.name.split("_")[0]
                frames.append(data)
        imported = pd.concat(frames)
        cleaned = util.remove_outliers(imported, "hist")
        cleaned["treatment_name"] = treatment
        cleaned["repeat"] = 1
        cleaned["unique_id"] = (
            cleaned["molecule number"].astype(str)
            + "_"
            + cleaned["treatment_name"]
            + "_"
            + cleaned["repeat"].astype(str)
        )
        cleaned.columns = [
            "frames",
            "donor",
            "acceptor",
            "FRET",
            "idealized FRET",
            "molecule_number",
            "treatment_name",
            "repeat",
            "unique_id",
        ]
        dwells = ps.calculate_dwell_time(cleaned.filter(items=["idealized FRET", "unique_id"]))
        transitions = ps.remove_outliers_tdp(ps.generate_transitions(dwells))
        for kind, table in (("dwells", dwells), ("transitions", transitions)):
            path = TABLES / f"{set_id}_{kind}.csv"
            path.write_bytes(table.to_csv(index=False, lineterminator="\n").encode())
            record["outputs"][path.name] = _sha256(path.read_bytes())
            print(f"{set_id}: {len(table)} {kind} -> {path.relative_to(REPO)}")
        record["inputs"][fixture.name] = _sha256(fixture.read_bytes())
    (TABLES / "smfret_module_run.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    fx = sub.add_parser("fixtures", help="copy the two condition folders into .tar.xz fixtures")
    fx.add_argument("--source", type=Path, required=True)
    tb = sub.add_parser("tables", help="run smFRET_module v1.0.3 on the fixtures")
    tb.add_argument("--module", type=Path, required=True)
    args = parser.parse_args()
    if args.cmd == "fixtures":
        make_fixtures(args.source)
    else:
        make_tables(args.module)


if __name__ == "__main__":
    main()
