---
description: "The HDF5 layout of a .tether project file, so that you can read it with h5py, another language or a script that must work across releases."
---

# The `.tether` project file format

**Who this page is for.** Anyone who has a `.tether` file and wants to read it with plain `h5py`
or in another language. It is also for a script that must keep working across Tether releases. It
assumes that you can run a Python script and that you know what an HDF5 group and a dataset are.

This page is the reference for the **internal** layout of a project file only. It calls that HDF5
layout the **store**, and it uses "project file" for the `.tether` file itself. What Tether
*writes out* (the SMD-HDF5 hand-off, CSV and Parquet tables, plot exports) is a separate surface.
[Exports (files, columns, units)](exports.md) describes it.

A `.tether` file is an ordinary HDF5 file. There is no proprietary container and no separate index
file. The only compression is stock gzip on the bulk arrays.

The format has **two layers**, and they carry different guarantees:

- The **frozen skeleton** is the 14 group paths (the root and the 13 top-level groups), the six
  compound `table` dtypes and the three root attributes. One place declares it:
  `src/tether/io/schema.py`. A machine-checked copy of it is committed as
  `schema/schema_frozen.json`. This site serves that file, unchanged, as
  [`tether-schema.json`](tether-schema.json), so each version of these pages links to the schema
  of the same version. `scripts/dump_schema.py` generates that manifest. It runs
  `tether.io.schema.build_manifest` against a throwaway `create_project` store, so it cannot drift
  from the code. Where this page and that manifest disagree about the skeleton, **the manifest is
  right and this page is a bug**.
- A pipeline stage writes everything else *later*: `/traces/*`, `/patches/*`, `/settings/*`,
  `/idealization/{model}`, `/features/table`, `/conditions/categories`, `/movies/layouts`,
  `/calibration/*` and `/models/*`. This is additive data for each record, and the module that
  writes it declares it. None of it is in the manifest, so the manifest cannot arbitrate it. The
  writer named in each section below is the authority. Note the `/*`: the **container groups**
  that hold this data are part of the frozen skeleton, and only their contents are additive.

Every claim below names the module, constant or writer behind it.

## How to read this page

Each table of stored fields has the same six columns, in this order:

| Column | What it holds |
| --- | --- |
| **Field** | The name of the field, attribute, member or dataset. |
| **Type** | The stored type, in the notation of [Type notation used below](#type-notation-used-below). |
| **Shape** | `scalar`, or the shape of the value, such as `(2,)`. |
| **Unit** | The unit of a number: frames, seconds, pixels, bytes, count, trace intensity, pixel value or dimensionless. A dash means that the value is text, a code or an identifier. |
| **Empty value** | The value that the field holds when it has no data, such as `""`, `NaN` or `-1`. **absent** means that the field or the dataset can be missing. A dash means that no empty value is documented. |
| **Meaning** | What the value is, in at most 25 words. A longer note is in the numbered notes below the table. |

**Trace intensity** is the unit of `/traces`. Tether records no physical unit or gain for it (see
[`/traces`](#traces-the-integrated-intensity-arrays)).

Some tables list paths, states, codes or writers, not fields. Their header names their columns.

A warning before a table gives a fact that can change a number that you publish, or that can make
a reader fail. The [Background](#background) section at the end gives the reasons behind some of
the layout.

## Opening a store

```python
import h5py

with h5py.File("experiment.tether", "r") as f:
    assert f.attrs["format"] == "tether-project"
    print(int(f.attrs["schema_version"]))          # 3
    mols = f["/molecules/table"][:]                # a structured (compound) array
    print(mols["molecule_key"][0].decode("utf-8")) # vlen UTF-8 reads back as bytes
```

Tether's own validation entry point is `tether.io.schema.assert_is_compatible_project(path)`. It
checks these conditions, in this order:

1. The file is readable HDF5.
2. The root `format` attribute is `tether-project`.
3. A `schema_version` stamp exists.
4. The frozen top-level skeleton is present. So Tether rejects a foreign *or truncated* HDF5 file
   and does not silently accept it.
5. That version is not newer than the running app.

It returns the on-disk `schema_version`. The order matters when you diagnose a rejection. A file
with no stamp fails on the stamp before Tether inspects the skeleton.

String fields are HDF5 **variable-length UTF-8** (`h5py.string_dtype(encoding="utf-8")`, the
[`_str()`](https://github.com/bioedca/tether/blob/main/src/tether/io/schema.py) helper in
`src/tether/io/schema.py`). h5py returns them as `bytes`, so decode them yourself.

## What is frozen, and what that means for your reader

The whole group skeleton was declared in advance and stamped with a version when the schema was
frozen. After that it is **additive-only**
([ADR-0005](../adr/0005-m0-schema-freeze.md)). The freeze covers these items:

- The 14 group paths listed on this page.
- The six compound `table` datasets, including **field order**. A compound dtype's on-disk layout
  is positional, so a new field can only be *appended*.
  [`_diff_compound`](https://github.com/bioedca/tether/blob/main/src/tether/io/schema.py) in
  `src/tether/io/schema.py` requires the golden field sequence to stay an exact prefix of the
  current one.
- The dtype and the sub-array shape of each field.
- The `maxshape` of each table. All six are `(None,)`: 1-D and resizable.
- The presence and the type of the three root attributes, and the *values* of `format` and
  `schema_version`.

Machine checks enforce these rules, not review. The `schema-guard` CI job
(`.github/workflows/schema-guard.yml`) runs `python scripts/dump_schema.py --check`. That check
fails a removal, a rename, a reorder or a retype. It passes an added group, dataset, attribute or
*trailing* table field.

This page does not restate the release **policy** behind those rules.
[What each release may change](../stability.md#what-each-release-may-change) sets out which
release types can touch what.
[The `.tether` on-disk format](../stability.md#2-the-tether-on-disk-format) lists which parts of
this page are a promise and not only a description.

If you write your own reader:

> **Tolerate what you do not recognize.** Read compound tables **by field name**, never by
> positional index or by a hard-coded record size. Ignore fields, datasets, groups and attributes
> that you do not know, because a later Tether release can add them without a `schema_version`
> bump.

You can rely on the **frozen skeleton** above for as long as `schema_version` stays `3`. The group
paths, the field names, dtypes and order of the six tables, and the three root attributes stay
unchanged. `app_version` is the exception: it is present only in a file whose writer stamped it
(see the root group section below). That is exactly the set that `schema-guard` enforces against
the writer.

The additive payloads further down (`/traces/*`, `/patches/*`, `/settings/*`,
`/idealization/{model}`, `/features/table`, `/calibration/*`) are **documented convention, not
part of the freeze**. Their **container groups are part of it**. `/traces`, `/patches`,
`/settings`, `/idealization`, `/features`, `/calibration` and `/models` are all in the golden's
`groups` list. They are also in
[`_CONTAINER_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/io/schema.py)
(`src/tether/io/schema.py`). So `assert_is_compatible_project` rejects a file that is missing any
one of them as not a project. Each one is a **required** group that your reader can rely on, also
when it is empty.

Only what is *inside* them is not arbitrated. `build_manifest` inspects a freshly created, empty
store, so none of those child names is in the golden. `diff_manifest` never compares them. So a
rename or a retype of one passes `schema-guard` with `schema_version` unchanged. In practice they
have been additive. `/features/table` carries its own `feature_schema_version`, so its columns can
change without a change to the store's `schema_version`.

Pin your reader to the writer module named in each section, and treat a missing or unfamiliar
payload as normal.

`schema_version` is now **`3`** (`SCHEMA_VERSION`, `src/tether/io/schema.py`).
Version 2 added the `/generations` table ([ADR-0136](../adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)),
and version 3 the `/sources` table ([ADR-0140](../adr/0140-a-source-registry-records-each-source-file-and-what-was-derived-from-it.md)).
A store stamped `1` has neither table and a store stamped `2` has no `/sources` table.

Each is still a complete project, and it reads as one source record for each movie. A store
stamped `1` also reads as generation 1 of each of its movies. The first write that adds a
table also adds every other table of version 3. It keeps the file as it was beside it, as
`<stem>.v1-backup.tether` or `<stem>.v2-backup.tether`.

[Project file compatibility](../stability.md#project-file-compatibility) tells what a bump means
for a file that you already hold. It also tells what a non-additive change must carry before it
is allowed. The [deprecation policy](../stability.md#deprecation-policy) on the same page tells
how a covered name is retired.

Two properties are worth knowing before you build on the format:

- The store is a **superset of SMD**, the tMAVEN interchange model
  ([ADR-0002](../adr/0002-smd-superset-round-trip.md)). Every SMD concept has a place here. The
  store also holds the coordinates, patches, corrections and provenance that SMD has no slot for.
  So round-trip fidelity is a property of the data model, not of a converter.
- The `<file>.lock` single-writer marker is a separate **lock file**. It is not part of the HDF5
  schema (the module docstring of `src/tether/io/schema.py`), and `create_project` never writes
  it.

## A freshly created project is nearly empty

`tether.io.schema.create_project(path)` writes the whole skeleton and nothing else: 13 top-level
groups, six **zero-row** compound tables and three root attributes. Everything else on this page
is *data* that a specific pipeline stage writes later. Do not treat the *size* of the file as a
format property. The same skeleton measures 12,950 bytes under HDF5 2.0.0 and 13,164 bytes under
1.14.x. Only the structure is fixed.

The `/traces` and `/patches` **groups** always exist. They are part of the validated skeleton
([`_CONTAINER_GROUPS`](https://github.com/bioedca/tether/blob/main/src/tether/io/schema.py)), and
Tether rejects a file that is missing one as not a project. Do not assume that anything is
*inside* them. No `/traces/*` or `/patches/*` dataset, no `/features/table` and no
`/idealization/{model}` is sure to be there.

| Path | In a fresh store | First written by |
|---|---|---|
| `/movies/table`, `/molecules/table`, `/conditions/table`, `/labels/table`, `/generations/table`, `/sources/table` | yes, 0 rows | `create_project` (`src/tether/io/schema.py`) |
| `/calibration`, `/traces`, `/patches`, `/idealization`, `/settings`, `/features`, `/models` | yes, empty groups | `create_project` |
| `/calibration/<calibration_id>/…` | no | `write_calibration` (`src/tether/imaging/calibrate.py`) |
| `/movies` and `/molecules` rows, `/movies/layouts/<movie_id>`, `/traces/*`, `/patches/*`, `/settings/extraction`, `/generations` rows | no rows / absent | `write_extraction` (`src/tether/imaging/extract.py`) |
| `/generations/archive/<movie_id>/<generation>` | absent | `write_generation` (`src/tether/imaging/extract.py`), run by `reextract_movie` (`src/tether/project/extract.py`) |
| `/movies/metadata/<movie_id>` | absent | `extract_movie` (`src/tether/project/extract.py`). `tether.project.movie_metadata` changes it. |
| `/idealization/{model}` | no | `write_idealization_model` (`src/tether/project/idealize.py`) |
| `/labels` rows | 0 rows | `set_curation_label` (`src/tether/project/labels.py`) |
| `/labels/reject_log` | absent | `reject` / `unreject` (`src/tether/project/labels.py`) |
| `/settings/leakage`, `/settings/gamma`, `/settings/correction`, `/settings/batch` | no | `tether.project.leakage` / `.gamma` / `.correct` / `.batch` |
| `/conditions` rows, `/conditions/categories/<condition_id>`, `/settings/condition_audit` | 0 rows / absent | `tether.project.conditions` |
| `/settings/molecule_audit` | absent | `set_category` / `clear_category` / `set_analysis_window` / `reset_analysis_window` (`src/tether/project/molecule_edits.py`) |
| `/settings/lock_takeovers` | absent | `record_takeover` (`src/tether/project/lock_takeovers.py`), which the application calls after a Recover or a Take over |
| `/features/table` | no | `compute_features` (`src/tether/project/features.py`). Each writer of an analysis window can delete it (note 1). |
| `/settings/analysis_only` | no | `import_analysis_only_project` (`src/tether/project/analysis_import.py`) |
| `/settings/jobs`, `/settings/recorded_inputs`, `/settings/stale_marks` | absent | `submit_job` / `record_inputs` / `set_stale_marks` (`src/tether/project/jobs.py`) |
| `/sources` rows | 0 rows | `register_source` / `relink_source` (`src/tether/project/sources.py`) and the checksum job (`src/tether/services/sources.py`) |
| `/settings/correction_transitions` | absent | `send_event` (`src/tether/project/correction_states.py`) |
| `/settings/gamma_contributors`, `/settings/gamma_contributor_decisions` | absent | `compute_gamma` (`src/tether/project/gamma.py`) / `set_gamma_contributor` (`src/tether/project/gamma_contributors.py`) |
| `/settings/approvals` | absent | `record_approval` (`src/tether/project/approvals.py`), which `tether approve` and a recipe policy call |
| `/models/<condition_id>` | no | `record_model_reference` (`src/tether/project/model_artifact.py`) |

**Notes**

1. Each writer of an analysis window deletes `/features/table` when it changes a row that the
   table holds. The [`/features`](#features-the-ml-feature-cache) section names these writers.

`write_extraction` is not the only writer of `/molecules` rows and `/traces`. Four shipped
provenances give a populated store, and they differ in what they fill in. The rest of this page
describes the native-extraction case unless it says otherwise:

| Store provenance | Writer | What differs |
|---|---|---|
| Native extraction | `write_extraction` (`src/tether/imaging/extract.py`) | The fullest record: `/movies` rows, coordinates, all six `/traces` layers and `/patches`. The layers and `/patches` exist only for a movie with at least one molecule (note 1). |
| Deep-LASI reconstruction | `reconstruct_project` (`src/tether/project/reconstruct.py`) | The same `/movies`, `/molecules`, `/traces` and `/patches` layout, because it also goes through `write_extraction`. The trace values are copied from the legacy `.mat` file, and no calibration is written (note 2). |
| Analysis-only import | `import_analysis_only_project` (`src/tether/project/analysis_import.py`) | No `/movies` rows, `movie_id = ""`, and `donor_xy`/`acceptor_xy` `NaN`. `/patches` is empty, and only the two background-subtracted (`corrected`) `/traces` layers exist. |
| Subset export | `export_subset_tether` (`src/tether/project/export.py`) | A new store whose `/molecules` rows are copied verbatim, so `movie_id` survives as a **dangling** key. It has no `/movies` rows and only the `corrected` `/traces` layers, unless `include_raw=True`. |

**Notes**

1. [`/traces`](#traces-the-integrated-intensity-arrays) describes the case of a movie that gives
   no molecules.
2. The trace values are the legacy `.mat` series, copied verbatim. They are **not** an integration
   that this build performed. `reconstruct_project` never calls `write_calibration`, so
   `/calibration` stays empty whatever the caller passes. The GUI wizard is the only shipped
   caller, and no `tether` subcommand reconstructs. The wizard also leaves the calibration key and
   the channel geometry unset
   ([`_movie_metadata`](https://github.com/bioedca/tether/blob/main/src/tether/gui/deeplasi_executor.py),
   `src/tether/gui/deeplasi_executor.py`). So in practice `/movies.calibration_id` is `""` and the
   crop, rotation and flip fields are all zero.

`create_project` opens the file with `track_order=True` and creates every skeleton group with it.
The later `/settings/*`, `/calibration/<id>` and `/conditions/categories` writers do the same, so
those links are stored in creation order.

The promise is **not** for the whole store.
`write_idealization_model` (`src/tether/project/idealize.py`) creates the
`/idealization/<name>` group of each model, and the `priors` subgroup inside it, without
`track_order`. The copy in
[`_copy_idealization`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py)
(`src/tether/project/export.py`) does the same. Their members fall back to the default HDF5 order.
In both cases `h5py.Group.visit` iterates **alphabetically**.

## Type notation used below

The Type column uses the canonical names of `schema/schema_frozen.json`. The manifest drops byte
order, so that it is the same on every platform
([`_canonical_scalar`](https://github.com/bioedca/tether/blob/main/src/tether/io/schema.py)):

- `i1`, `i4` and `i8` are signed integers of 1, 4 and 8 bytes. `u1` is an unsigned integer of
  1 byte.
- `f4` is a 32-bit float, and `f8` is a 64-bit float.
- `bool` is a NumPy boolean.
- `str:utf-8` is a variable-length UTF-8 string.

`src/tether/io/schema.py` declares the little-endian spellings `<i4`, `<i8` and `<f8`, and `i1`,
which has no byte order. The rows of an attribute use the short names `int`, `float`, `str` and
`bool`, as the manifest does for the root attributes.

## `/` — the root group

Three attributes. `create_project` always writes `format` and `schema_version`. It stamps the
third **by default, but the third is optional** (see below):

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `format` | `str` | scalar | — | — | `tether-project` (`FORMAT_TAG`). Frozen: the name, the type **and the value**. |
| `schema_version` | `int` | scalar | — | — | `3` (`SCHEMA_VERSION`). Frozen: the name, the type **and the value**, and the value never goes down. |
| `app_version` | `str` | scalar | — | absent | The writing Tether version, such as `0.8.1.dev16+g…`. Frozen: the name and the type only. |

`app_version` is provenance, not structure. What is frozen is the **writer's** declaration, not
every file. `create_project(path, stamp_app_version=False)` leaves the attribute out, and
`assert_is_compatible_project` never looks for it. So a store without it is valid. Read it as
`f.attrs.get("app_version")`, and never reject a file because it is absent.
[The `.tether` on-disk format](../stability.md#2-the-tether-on-disk-format) section of the
stability page gives the policy, and why the manifest leaves out the *value*.

Subset exports stamp four **more** root attributes on the destination file:
`tether_subset_of`, `tether_subset_created_utc`, `tether_subset_include_raw` and
`tether_subset_n_molecules` (`src/tether/project/export.py`). This is the additive rule in
practice: expect root attributes that you do not know.

## `/movies` — one row per source movie

Group holding `/movies/table`, a resizable 1-D compound dataset (`MOVIES_DTYPE`, 20 fields).
`write_extraction` appends one row for each movie. `extract_movie` writes the first row of a new
project, and `add_movie_to_project` (`src/tether/project/extract.py`) appends each later movie. A
Deep-LASI acquisition goes in through `reconstruct_project` and `add_reconstruction_to_project`
(`src/tether/project/reconstruct.py`).

Re-appending an existing `movie_id` is refused. `add_movie_to_project` also refuses a movie
whose `sha256` is already in a row, and `write_extraction` refuses any molecule whose
`molecule_key` is already in `/molecules`
([ADR-0117](../adr/0117-a-movie-is-appended-to-a-project-once-under-its-writer-lock.md)).
For a new trace generation, `write_generation` updates the movie's row in place
with the new inputs and keeps its `frame_time`. The previous row stays in
[the generation archive](#generations-the-trace-generations-of-each-movie).

`file_size`, `mtime` and `offline_flag` are the **metadata-only fast signature**. This check for
relocation and staleness reads no bytes of the movie, so it never hydrates a cloud placeholder.

> **Do not take the frame interval of a Deep-LASI reconstruction from `frame_time`.** The
> reconstruction builds this row from the raw TIFF, and the shipped `.tdat`/`.mat` decode reads
> no `FrameTime`. So a reconstructed store does **not** keep a Deep-LASI timebase. With the shipped
> writers, a `frame_time` of `0.0` means that the TIFF had no usable ImageJ `finterval` tag (note
> 1).

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `movie_id` | `str:utf-8` | scalar | — | — | The movie's identity in this store. The CLI writes `mov-<uuid4>` (`src/tether/project/extract.py`). |
| `uri` | `str:utf-8` | scalar | — | `""` (unknown) | The path of the source movie, as text. |
| `sha256` | `str:utf-8` | scalar | — | — | The 64-character hex SHA-256 of the whole movie file ([`_hash_movie`](https://github.com/bioedca/tether/blob/main/src/tether/project/extract.py)). It seeds every `molecule_key`. |
| `file_size` | `i8` | scalar | bytes | — | `Path.stat().st_size`. |
| `mtime` | `f8` | scalar | seconds since the POSIX epoch | — | `Path.stat().st_mtime`. |
| `offline_flag` | `i1` | scalar | — | `0` | A cloud-placeholder or offline marker. It is reserved, and every shipped writer leaves it `0`. |
| `n_frames` | `i4` | scalar | frames | — | The frame count of the movie, validated `>= 1`. |
| `height` | `i4` | scalar | pixels | — | The full-frame height. |
| `width` | `i4` | scalar | pixels | — | The full-frame width: the whole frame, before the two-channel split. |
| `pixel_dtype` | `str:utf-8` | scalar | — | — | The on-disk pixel dtype string, such as `>u2` (`src/tether/io/movie.py`). |
| `byteorder` | `str:utf-8` | scalar | — | — | `>` (big-endian) or `<` (little-endian). |
| `frame_time` | `f8` | scalar | seconds per frame | `0.0` (unknown) | The frame interval, from the ImageJ `finterval` tag of the movie TIFF (note 1). |
| `head_tail_hash` | `str:utf-8` | scalar | — | `""` | A reserved partial-content digest for the fast signature. No shipped writer sets it. |
| `calibration_id` | `str:utf-8` | scalar | — | `""` (Deep-LASI reconstruction) | The join key into `/calibration`. |
| `donor_crop` | `i4` | `(4,)` | pixels | all zero (full frame, or not given) | `[y1, x1, y2, x2]`, the **1-based inclusive** pixel bounds of the donor channel (`src/tether/imaging/split.py`). |
| `acceptor_crop` | `i4` | `(4,)` | pixels | all zero | The same convention, for the acceptor channel. |
| `donor_rotation_deg` | `i4` | scalar | degrees | — | The channel rotation, one of `{0, 90, 180, 270}`. |
| `acceptor_rotation_deg` | `i4` | scalar | degrees | — | The same, for the acceptor channel. |
| `donor_flip` | `i1` | `(2,)` | — | — | The `[vertical, horizontal]` flip flags, `0` or `1`. |
| `acceptor_flip` | `i1` | `(2,)` | — | — | The same, for the acceptor channel. |

**Notes**

1. The writer stores the value that the caller's `MovieMetadata` holds, and the default is `0.0`.
   Both shipped writers take it from the ImageJ `finterval` tag of the movie TIFF, and only when
   that value is finite and positive
   ([`_read_frame_time`](https://github.com/bioedca/tether/blob/main/src/tether/io/movie.py),
   `src/tether/io/movie.py`). In every other case the value is `0.0`. The two writers are
   `tether.project.extract` and the Deep-LASI reconstruction's
   [`_movie_metadata`](https://github.com/bioedca/tether/blob/main/src/tether/gui/deeplasi_executor.py)
   (`src/tether/gui/deeplasi_executor.py`).

The group also holds an additive child, `/movies/layouts/<movie_id>`. It records the **channel
layout** that split the movie, and `write_layout_record` in `src/tether/imaging/layout.py` writes
it. Each record is an empty group with attributes only:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `version` | `int` | scalar | — | — | `1`. A reader refuses a newer version (`read_layout_record`). |
| `kind` | `str` | scalar | — | — | `side-by-side`, `top-bottom` or `rectangles`. |
| `donor` | `str` | scalar | — | `""` (for `rectangles`) | The donor half: `left`/`right` or `top`/`bottom`. |
| `acceptor` | `str` | scalar | — | `""` (for `rectangles`) | The acceptor half, in the same words. |
| `donor_rect` | `i4` | `(4,)` | pixels | absent (not `rectangles`) | `[y1, x1, y2, x2]`, 1-based inclusive, for `rectangles` only. |
| `acceptor_rect` | `i4` | `(4,)` | pixels | absent (not `rectangles`) | The same, for the acceptor channel. |

The record states the layout. The crops that it resolved to for this frame size are the
`donor_crop` and `acceptor_crop` fields above. The native `tether extract` path writes one record
for every movie, the default `side-by-side` / `left` included.

Some movies have no record. A
store written before layout records existed has none, and neither has a Deep-LASI
reconstruction. An extraction that took its split from an imported `.tmap` has none either. For
those movies, read the crop fields instead.

The group also holds a second additive child, `/movies/metadata/<movie_id>`. It is one group
with no datasets for each movie. Its attributes record three values, each with its **origin**:
`file` (read from the movie), `user` (given by a person) or `unknown`
(`src/tether/project/movie_metadata.py`). It does not change `frame_time` above, which keeps the
value from the file.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `frame_time` | `float` | scalar | seconds per frame | `0.0` (unknown) | The frame interval in use: the file's value, or the one that the user gave. |
| `frame_time_origin` | `str` | scalar | — | — | `file`, `user` or `unknown`. |
| `frame_time_verified` | `bool` | scalar | — | — | True only for a file value, or after the user confirmed a given value in a separate action (`confirm_frame_time`). Giving a value clears it. |
| `pixel_size_um` | `float` | scalar | micrometers per pixel | `0.0` (unknown) | The ImageJ resolution of the file, or the user's value (note 1). |
| `pixel_size_origin` | `str` | scalar | — | — | `file`, `user` or `unknown`. |
| `channel_roles_origin` | `str` | scalar | — | — | Where the donor and acceptor assignment of `donor_crop`/`acceptor_crop` came from: `file` or `user` (note 2). |

**Notes**

1. Tether uses the ImageJ resolution only when its unit is µm, the pixels are square and the
   resolution is not the `1/1` placeholder.
2. `file` is for an imported `.tmap` or a map file. `user` is for a native extraction
   (`--donor-side`). A re-extraction sets it again from the sources of that run.

`extract_movie` writes the group. Some movies have no group: those of a store written before the
group existed, of a Deep-LASI reconstruction, and of an analysis-only import. Such a row reads
(`read_movie_metadata`) as follows. A positive `frame_time` has origin `file` and is verified,
because that field has only ever been filled from the ImageJ `finterval`. A `frame_time` of `0.0`
is `unknown`. The pixel size and the channel roles are `unknown`.

A re-extraction (`reextract_movie`) writes only a new `channel_roles_origin`. It keeps the
other attributes of the movie, and for a row with no group it reads them by the rule above.

## `/molecules` — one row per molecule

Group holding `/molecules/table` (`MOLECULES_DTYPE`, 21 fields). This table is the spine of the
store. **Row `i` of `/molecules/table`, of every `/traces/*` array and of every `/patches/*` array
is the same molecule.** The join is positional, and
[`_validate_alignment`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)
in `src/tether/imaging/extract.py` asserts it.

Later stages change columns of existing rows, and they never change the structure:

- Photobleach detection rewrites `bleach_frames` and `analysis_window`.
- The leakage, gamma and correction stages write `alpha`, `gamma`, `correction_method` and
  `correction_confidence`.
- Curation writes `curation_label`.
- A condition re-key rewrites `condition_id`.
- The setters for one molecule in `src/tether/project/molecule_edits.py` write `category` and
  `analysis_window`.

> **Join data for each row on `molecule_id`, not on `molecule_key`.** `molecule_id` is unique.
> Two molecules can in principle quantize to the same `molecule_key` (see
> [the derivations](#two-sentinels-and-two-derivations-worth-reading-twice) below).

> **Read `correction_method`, not `alpha` or `gamma`, to learn why a factor is `NaN`.** A `NaN`
> factor means that no factor was applied, and there are several reasons for it (notes 4 and 5).

> **Compare `bleach_frames` with `frame_range`, not with `-1`.** After photobleach detection, a
> channel that does not bleach is recorded as the trace end, `frame_range[1]`. `-1` means that
> detection has not run.

> **Do not read a narrowed `analysis_window` as a sign of photobleach detection or curation.** An
> analysis-only import can write a narrowed window (note 7). To tell the cases apart, read
> `/settings/analysis_only` or the `round-trip-unavailable` tag, not the analysis window.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_id` | `str:utf-8` | scalar | — | — | The stable identity of the row, `mol-<uuid4 hex>`. It is **unique**, so it is the correct join key for data of each row. |
| `molecule_key` | `str:utf-8` | scalar | — | — | The cross-file content identity, a 64-character hex SHA-256. It is the same across runs and platforms (note 1). |
| `movie_id` | `str:utf-8` | scalar | — | `""` (analysis-only import) | The foreign key into `/movies/table`. It **dangles** in a subset export, which carries no `/movies` rows, so check it before you use it. |
| `donor_xy` | `f8` | `(2,)` | pixels | `NaN` (analysis-only import) | The sub-pixel `[x, y]` centroid of the donor (reference channel), in movie pixels of the donor sub-image. |
| `acceptor_xy` | `f8` | `(2,)` | pixels | `NaN` (analysis-only import) | The sub-pixel `[x, y]` acceptor read position: the donor position mapped through the registration map, in movie pixels. |
| `aperture_id` | `i4` | scalar | — | — | The aperture-geometry ID. It is always `0` now (the standard aperture window), and no registry of apertures exists yet. |
| `frame_range` | `i4` | `(2,)` | frames | — | The `[start, stop)` frame indices of the molecule's valid native extent inside the zero-padded trace arrays. |
| `analysis_window` | `i4` | `(2,)` | frames | — | The `[start, stop)` frame indices that are actually analyzed (note 2). |
| `bleach_frames` | `i4` | `(2,)` | frames | `-1` (detection has not run) | The `(donor, acceptor)` first-bleach frames, as absolute frame indices. `-1` is *not* "no bleach" (note 3). |
| `alpha` | `f8` | scalar | dimensionless | `NaN` (no factor applied) | The donor leakage factor α (note 4). |
| `gamma` | `f8` | scalar | dimensionless | `NaN` (no factor applied) | The detection-correction factor γ, with the same `NaN` convention as `alpha` (note 5). |
| `delta` | `f8` | scalar | dimensionless | — | The direct-excitation factor δ. It has no effect in two-color work, and it is written `0.0`. |
| `correction_method` | `str:utf-8` | scalar | — | `""` (before any correction pass) | A closed vocabulary from `src/tether/project/correct.py`: `corrected`, `manual`, `apparent-E (corrections unavailable)` or `apparent-E (user toggle)`. |
| `correction_confidence` | `f8` | scalar | — | `NaN` (before any pass) | A **provenance flag, not a confidence interval**: `1.0` when a real correction was applied, and `0.0` on the apparent-E fallback. |
| `curation_label` | `i4` | scalar | — | `0` (uncurated) | The signed codec `CurationLabel` (`src/tether/project/labels.py`): `0` uncurated, `1` accept, `-1` reject. It mirrors the most recent **human** label only. |
| `category` | `str:utf-8` | scalar | — | `""` (at extraction) | The editable category value of the condition, stored by **name** and independent of accept or reject (note 6). |
| `quality_class` | `f8` | scalar | dimensionless | `NaN` (none) | The read-only output of the ML ranker. No shipped ML path writes it yet. |
| `condition_id` | `str:utf-8` | scalar | — | — | The current foreign key into `/conditions/table`. A re-key rewrites it. |
| `condition_id_provisional` | `str:utf-8` | scalar | — | — | The condition ID derived from the filename, kept verbatim across any re-key. |
| `source_filename` | `str:utf-8` | scalar | — | — | The original acquisition filename that the molecule came from. |
| `tags` | `str:utf-8` | scalar | — | `""` (none) | A comma-joined tag string. The pipeline emits two tags (see below). |

**Notes**

1. The derivations are under
   [Two sentinels and two derivations worth reading twice](#two-sentinels-and-two-derivations-worth-reading-twice).
2. At native extraction the analysis window equals `frame_range`. An SMD import seeds it from the
   analysis window of the source (note 7).
    - Photobleach detection refines it automatically, but *only* while it still equals
      `frame_range`. So an analysis window that is already narrower wins.
    - A curator sets it with `set_analysis_window`. That function clamps the request into
      `frame_range` (bounded by `[0, n_frames]`) by the SMD-import rule
      ([`_resolve_windows`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py),
      `src/tether/project/analysis_import.py`). `reset_analysis_window` restores it.
    - There is no "manual" flag. An analysis window set to the whole `frame_range` is under the
      automatic default again.
    - A change to a row that `/features/table` holds deletes that table, because the old features
      came from the old frames.
3. `-1` is the sentinel that Tether writes before photobleach detection. The
   [sentinels](#two-sentinels-and-two-derivations-worth-reading-twice) below tell how to read it.
4. `NaN` means **no factor applied**, which is not the same as "the pass has not run". It has
   these causes:
    - `compute_leakage_alpha` *withholds* the factor when fewer than `min_qualifying_traces`
      molecules give a valid donor-only tail (`src/tether/project/leakage.py`). It then resets the
      processed α rows, and the γ rows that depend on them, to `NaN`.
    - An analysis-only import seeds every row at `NaN` (`src/tether/project/analysis_import.py`).

    `compute_corrected_fret` then stamps `apparent-E (corrections unavailable)` on every molecule
    that can be analyzed (one whose `frame_range` is not empty). It leaves the factor `NaN`. There
    are two exceptions. `apparent_e_only` stamps `apparent-E (user toggle)` instead. A manual
    `alpha_override` or `gamma_override` can complete a usable pair (both effective factors
    finite, and γ > 0). Then it stamps `manual` and **writes the override into this field**.

    So read `correction_method` and `/settings/leakage` (`withheld`) to tell the cases apart,
    never this field.
5. `compute_gamma` withholds the dataset γ below `min_qualifying_traces`
   (`src/tether/project/gamma.py`), and it resets the processed rows to `NaN`. A leakage
   recomputation that is withheld also resets those rows, because γ was derived from the earlier
   α. A γ pass that withholds stamps `/settings/gamma` with `withheld = True`. A leakage pass that
   withholds removes `/settings/gamma`, and there is no marker that it was invalidated. So read
   `correction_method` and `/settings/gamma`, not this field.
6. `set_category` accepts only a name in the condition's `/conditions/categories/<condition_id>`
   list. `clear_category` writes `""`.
7. In a natively extracted store the analysis window starts equal to `frame_range`, so a narrower
   one does mean photobleach detection or a curator edit. An **analysis-only import** breaks that
   inference.
   [`_normalize_source`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)
   reads the tMAVEN `pre_list` and `post_list` of the SMD.
   [`_build_molecule_rows`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)
   then writes them straight into `analysis_window` (`src/tether/project/analysis_import.py`).

    So a row that was *just imported* can already be narrower than `frame_range`, with
    `bleach_frames` still `-1` and `curation_label` still `0`. The automatic refinement acts only
    on an analysis window that still equals `frame_range`. So a later `compute_photobleach` leaves
    those imported windows alone.

    For example, take a real import of 2 molecules over 40 frames, with `pre_list = [3, 0]` and
    `post_list = [30, 40]`. The rows read `analysis_window` `[3, 30]` and `[0, 40]` against
    `frame_range` `[0, 40]`. `compute_photobleach` reported `n_windows_autoset=1`, so it refined
    only the second row.

### `/molecules/acceptor_detected` — the colocalization flag

A dataset beside the table holds the colocalization flag, one value for each row of
`/molecules/table`, in the same order
([ADR-0127](../adr/0127-colocalization-policy-partner-in-both-by-default-or-donor-anchored.md)):

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `acceptor_detected` | `i1` | `(n_molecules,)` | — | `-1` (not recorded) | `1` when an acceptor spot was detected within `coloc_distance` of the mapped position, and `0` when none was. |

A project extracted before the flag existed has no dataset. A project that later gained
rows can have a dataset shorter than the table. Both read as `-1` for the rows that the dataset
does not cover
([`read_acceptor_detected`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)).
A dataset longer than the table is refused on the next append. A Deep-LASI reconstruction
writes `-1`, because its molecules are not detected. A subset export carries the flag of
each molecule that it copies.

The rule of each movie is the `colocalization` key of its profile on
`/settings/extraction/movies` (below). It is `partner-in-both`, under which every kept
molecule has flag `1`, or `donor-anchored`. A profile without the key reads as
`donor-anchored`
([`read_colocalization_policies`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)).
Such a profile was written before the rule could be chosen, or by a Deep-LASI
reconstruction.

### Two sentinels and two derivations worth reading twice

**`bleach_frames`.** `-1`
([`_UNDETECTED_FRAME`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py),
`src/tether/imaging/extract.py`) is what extraction and an analysis-only import write *before
anyone has looked*. It means that the photobleach detector has not run for that row, not that it
ran and found nothing. After `compute_photobleach` (`src/tether/project/photobleach.py`), a
channel that does **not** bleach within the trace is recorded as `frame_range[1]`, the trace end.
So compare with `frame_range`, never with `-1`.

`-1` also survives on a row with no valid native frames, which the detector skips. On a real
40-frame store, one molecule that bleaches and two that do not read
`[[30, 20], [40, 40], [40, 40]]` against `frame_range` `[[0, 40], [0, 40], [0, 40]]`.

**`alpha` and `gamma` NaN.** A NaN factor means that no factor was applied. One cause is the
min-qualifying-traces gate, which withheld it. Another is an analysis-only import, which seeds
every row at NaN (`src/tether/project/analysis_import.py`). The NaN **stays in
`/molecules.alpha` / `.gamma`**. Without a manual override, `compute_corrected_fret` only stamps
`correction_method` / `correction_confidence`.

NaN factors next to `apparent-E (corrections unavailable)` are therefore an *expected*
signature, not corruption. An import carries `/settings/analysis_only`. An α withhold is
recorded in `/settings/leakage` (`withheld`) and a γ withhold in `/settings/gamma` (`withheld`).
A missing factor never becomes a NaN in E (ADR-0003).

E can still be NaN on a frame whose denominator is zero, because the ratio is not defined
there. For apparent E that is a frame with zero total intensity. For corrected E it is a frame
where `I_A,corr + γ·I_D,corr` is zero. So a frame with both channels at zero is NaN in both. But
a frame whose donor and acceptor only cancel can have a finite corrected E.

That per-frame NaN
is not the factor sentinel. The factor NaN is also distinct from the `-1` undetected sentinel
above and from `NO_STATE` ([glossary](../glossary.md#withheld-factor-nan-sentinel)).

**`tags`.** Shipped code emits two tags:

| Tag | Constant | When |
|---|---|---|
| `low-confidence-registration` | `LOW_CONFIDENCE_TAG` (`src/tether/imaging/calibrate.py`) | The registration fit for that molecule exceeded the confidence gate. |
| `round-trip-unavailable` | `ANALYSIS_ONLY_TAG` (`src/tether/project/analysis_import.py`) | On **every** molecule of an analysis-only import, so it is on every row of such a store. |

**`molecule_key`.** It is the cross-file join key that everything downstream depends on. It is a
pure content hash, with no salt and no run state
([ADR-0016](../adr/0016-extraction-trace-store-layout.md)). The **first extraction** derivation,
from `molecule_key()` in `src/tether/imaging/extract.py`, is:

```python
q = round_half_away(donor_xy / MOLECULE_KEY_QUANTUM_PX).astype(np.int64)  # 0.1 px
payload = f"{movie_sha256}|{int(q[0])}|{int(q[1])}"
key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The quantum of 0.1 pixel absorbs jitter in the float representation. A split or a subset keeps
the stored key. Two molecules can in principle quantize to the same coordinate. Join data for each
row on `molecule_id`.

An **analysis-only import** has no movie and no coordinates, and `molecule_key()` would raise on
its `NaN` `donor_xy`. So it uses a second derivation,
[`_analysis_only_molecule_key`](https://github.com/bioedca/tether/blob/main/src/tether/project/analysis_import.py)
(`src/tether/project/analysis_import.py`). It hashes the source identity, the molecule's row index
and the raw bytes of the donor and acceptor traces. That variant *is* unique by construction.

A later extraction keeps the keys of carried
molecules ([ADR-0136](../adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)).
An unmatched site uses quantized raw camera coordinates and appends `|camera` to the hash
payload. It can join a newly found site in another copy across crops and generation numbers.
If the project's history already holds the camera key, the new payload also ends in `|g<n>`.
First-to-later joins use an earlier key carried by the recorded match. Read the stored key.

## `/conditions` — the structured experimental conditions

Group holding `/conditions/table` (`CONDITIONS_DTYPE`, 14 fields). The condition **identity
key** has these parts: construct or variant, dye, ligand and concentration, buffer, temperature
and laser power. `date`, `replicate` and the source file can vary *within* one condition, and
they are not part of the key ([ADR-0033](../adr/0033-condition-identity-and-rekey.md)).
`condition_id` is derived from that key as `cond-<first 12 hex of its sha256>`
(`ConditionKey.condition_id()` in `src/tether/io/filename.py`).

The project file is not part of the identity either: two projects whose `/conditions` rows
carry the same confirmed key hold the same condition
([ADR-0068](../adr/0068-a-project-holds-one-experiment.md)). A key parsed from a filename is
provisional until it is confirmed. The parse gives no buffer, temperature or laser power, so two
provisional keys can agree for two different conditions. Two near-miss filename parses yield
different ids and stay separate by default. Collapsing them requires an explicit confirmed
re-key ([ADR-0033](../adr/0033-condition-identity-and-rekey.md)).

> **Record the unit of `laser_power` yourself.** The store keeps no unit for it, and there is no
> `laser_power_unit` field. The conditions editor only *shows* the value as mW
> ([`_key_summary`](https://github.com/bioedca/tether/blob/main/src/tether/gui/conditions.py),
> `src/tether/gui/conditions.py`).

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `condition_id` | `str:utf-8` | scalar | — | — | `cond-<12 hex>`, the content hash of the identity key. |
| `construct_variant` | `str:utf-8` | scalar | — | — | The construct or variant name. Part of the identity key. |
| `dye` | `str:utf-8` | scalar | — | — | The dye pair label. Part of the identity key. |
| `ligand` | `str:utf-8` | scalar | — | — | The ligand name. Part of the identity key. |
| `ligand_concentration` | `f8` | scalar | the unit in `ligand_concentration_unit` | `NaN` (absent) | The numeric concentration. A `NaN` round-trips back to the same `condition_id`. |
| `ligand_concentration_unit` | `str:utf-8` | scalar | — | — | The unit string for `ligand_concentration`, such as `nM`. Part of the identity key. |
| `buffer` | `str:utf-8` | scalar | — | `""` (note 1) | The buffer description. Part of the identity key. |
| `temperature_c` | `f8` | scalar | degrees Celsius | `NaN` (absent) | The temperature. Part of the identity key (note 1). |
| `laser_power` | `f8` | scalar | not stored (see the warning above) | `NaN` (absent) | The laser power, entered in the conditions editor. Part of the identity key (note 1). |
| `date` | `str:utf-8` | scalar | — | — | The acquisition date. Provenance within the condition, **not** part of the identity key. |
| `replicate` | `str:utf-8` | scalar | — | — | The replicate label. Provenance within the condition, not part of the identity key. |
| `leakage_alpha` | `f8` | scalar | dimensionless | `NaN` (not set) | The leakage factor α of the condition. |
| `leakage_alpha_source` | `str:utf-8` | scalar | — | — | The provenance of `leakage_alpha`: the donor-only sample that it came from. |
| `tags` | `str:utf-8` | scalar | — | — | A free-form tag string. |

**Notes**

1. The filename parser never fills `buffer`, `temperature_c` or `laser_power`. `parse_filename`
   (`src/tether/io/filename.py`) fills only construct or variant, dye, ligand, concentration and
   unit. Its only `ConditionKey(...)` construction sets those five and nothing else. So in a store
   whose conditions came from filename parsing alone, these three fields are `""` or `NaN`. They
   change only through the conditions editor, by a re-key that `/settings/condition_audit` logs.

The group also holds an additive child, `/conditions/categories/<condition_id>`. It is one
resizable 1-D vlen-UTF-8 dataset for each condition, with that condition's ordered, editable
category names
([`_write_category_names`](https://github.com/bioedca/tether/blob/main/src/tether/project/conditions.py)
in `src/tether/project/conditions.py`). It appears only after a category list has been set.
Emptying the list resizes the dataset to zero rows, and it does not delete the dataset.

Each of those datasets carries one attribute, `tmaven_class_lookup`. It is the condition's
tMAVEN class to category lookup, stored as a JSON object. Its keys are the classes `"1"`,
`"2"`, and so on. Its values are the category names in list order. Class `0` is not stored.
It always means no category (`""`).

The same writer rewrites the attribute with every list edit, so it never lags the names. The
tMAVEN hand-off and its return leg read it through `read_class_lookup`. The curation hotkeys
follow the same numbering. A list saved before the attribute existed reads as the lookup that its
order defines. A lookup that disagrees with the names beside it is refused
([ADR-0125](../adr/0125-the-tmaven-class-lookup-follows-the-category-list.md)).

A condition can span several projects ([ADR-0068](../adr/0068-a-project-holds-one-experiment.md)),
and each project keeps its own copy of this list. `copy_category_list` in
`src/tether/project/conditions.py` copies one project's list for a condition into another project
that holds the same condition. It keeps the order, so every category keeps its integer code. The
destination's previous list is replaced. An empty source list empties it.

Molecules keep their stored category names, so a name that is not in the new list can stay on a
molecule. `compare_category_lists` reports each category whose name or code differs between the
two projects. Both functions refuse a project that has no `/conditions` row for the condition, and
they name that file in the message.

## `/labels` — the append-only curation log

Group holding `/labels/table` (`LABELS_DTYPE`, 8 fields), with one row for each labeling *event*.
The table is **append-only** and carries full provenance
([ADR-0023](../adr/0023-curation-label-codec-and-labels-log.md)). Rows are never removed or
reordered, and every field except `weight` is immutable. The audit row is written **before** the
`/molecules.curation_label` mirror is updated. So a crash leaves at worst an orphan label row,
never a state change without an audit.

> **Do not cache `weight`.** Each retrain recomputes the whole column in place
> (`recompute_label_weights`, `src/tether/project/weighting.py`).

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_key` | `str:utf-8` | scalar | — | — | The join key into `/molecules.molecule_key`. It is the cross-file key, so labels survive the split-file merge-back. |
| `labeler` | `str:utf-8` | scalar | — | — | The curator's identity. The default is the OS login, or `unknown`. |
| `timestamp` | `str:utf-8` | scalar | — | — | An offset-aware ISO-8601 time, such as `2026-07-21T00:52:09.949494+00:00` (note 1). |
| `source_file` | `str:utf-8` | scalar | — | — | The source provenance that the caller gave, stored **verbatim**. It is not always a `.tether` name (note 2). |
| `source` | `str:utf-8` | scalar | — | — | A closed vocabulary (`src/tether/project/labels.py`): `human`, `deeplasi-provisional` or `cross-condition-seed`. The last two are provisional cold-start priors. |
| `weight` | `f8` | scalar | dimensionless | — | The effective training weight. Human labels are `1.0`, and provisional weights decay as human labels accrue. |
| `label_value` | `i4` | scalar | — | — | The same signed codec as `curation_label`: `0` uncurated, `1` accept, `-1` reject. |
| `condition_id` | `str:utf-8` | scalar | — | — | The condition that the label is scoped to. |

**Notes**

1. Tether's own writes are in UTC. The writer only requires an explicit offset
   (`set_curation_label`, `src/tether/project/labels.py`), so a merged-in row can carry another
   offset.
2. It only *defaults* to the basename of the `.tether` that is curated (`set_curation_label`,
   `src/tether/project/labels.py`). The Deep-LASI reconstruction seeds its `deeplasi-provisional`
   rows with the acquisition filename instead (`parsed.source_filename`,
   `src/tether/project/reconstruct.py`).

A **reject is a reversible tag that persists, never a deletion**. It carries across files on
`molecule_key`, and it is reversible in one call (`tether.project.labels.unreject`). The
**toggleable** curation filter excludes it (`include_rejected=False` by default).

`tether.project.labels.accept` / `reject` / `unreject` each append one provenance-stamped row to
`/labels/table`. `unreject` appends one only when the molecule really is rejected. On any other
state it is a documented no-op that writes nothing. The GUI shows that reversal as **Un-reject
selected** in the Browser dock, a visible control and not a hidden global shortcut.

Nothing appends a row for a category assignment. `category` lives only on `/molecules`.
No row is ever deleted (a reversal is logged as its own row), but those three wrappers are not
the log's only appenders. `tether.project.reconstruct` records provisional Deep-LASI accept
priors through `set_curation_label`. `tether.project.merge` appends a contributor's human rows
when two projects are merged, matched and de-duplicated. It rewrites the `condition_id` of each
appended row to the owner project's value.

### `/labels/reject_log` — the reason of each reject

It is a resizable 1-D compound dataset, and it is **append-only**. The first human reject creates
it (`reject`, `src/tether/project/labels.py`), so a fresh store does not have it
([ADR-0104](../adr/0104-a-reject-reason-log-under-labels.md)). Each human reject appends a
`reject` row, and each un-reject appends a `restore` row.

A rejected trace stays in the store: a reject sets `curation_label` to `-1` and deletes
nothing. The log is a record only. The curation filter, the ranker and every analysis
read `/molecules.curation_label` and not the reason. Provisional-source rejects write
no row.

`reject_record_of` returns the `reject` row that joins the molecule's latest human `/labels`
event, when that event is a reject. Otherwise it returns `None`. It also returns `None` for a
store written before this log existed, and when two `reject` rows share that join.

The rows travel with their `/labels` rows. A subset export copies the rows of the exported
molecules. The split-file merge-back (`merge_labels`, `src/tether/project/merge.py`) appends the
rows that join a `/labels` row that it pulls. It appends them under the owner's `condition_id`,
unless the owner already holds them. A second pull of the same file appends nothing.

A join that names two pulled rejects (or two restores) copies no reason, since it cannot say which
one the reason belongs to. An earlier pull can already have copied a reason for that join. Then
the pull copies the other reasons of the join too, so the owner also reads no reason for it.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_key` | `str:utf-8` | scalar | — | — | The join key into `/molecules.molecule_key`. |
| `event` | `str:utf-8` | scalar | — | — | `reject` or `restore`. |
| `reason` | `str:utf-8` | scalar | — | `""` (on a `restore` row) | A code from the list below. |
| `note` | `str:utf-8` | scalar | — | — | Optional free text: valid UTF-8, with no NUL character. Anything else is refused before the reject or restore is written. |
| `value` | `f8` | scalar | the unit of the score | `NaN` (not a `threshold` reject) | The score threshold of a `threshold` reject. |
| `labeler` | `str:utf-8` | scalar | — | — | Who rejected or restored. The same value as the matching `/labels` row. |
| `timestamp` | `str:utf-8` | scalar | — | — | When. The same value as the matching `/labels` row, so the two rows join on `molecule_key`, `labeler` and `timestamp`. |
| `condition_id` | `str:utf-8` | scalar | — | — | The condition of the matching `/labels` row. |

The reasons are a fixed list, defined once in `REJECT_REASONS`
(`src/tether/project/labels.py`). Any other value is refused before a write.

| Code | Meaning |
|---|---|
| `unspecified` | No reason was given. The default of a reject. |
| `no-anticorrelation` | Donor and acceptor do not anti-correlate. |
| `multiple-molecules` | More than one donor or acceptor in the spot. |
| `artifact` | A blink, spike, drift or other artifact makes the trace unusable. |
| `low-signal` | The signal is too low or too noisy to use. |
| `threshold` | Rejected by a score threshold. The row must carry the threshold in `value`. |
| `other` | Another reason. The row must carry a `note` that says which. |

## `/generations` — the trace generations of each movie

Group holding `/generations/table` (`GENERATIONS_DTYPE`, 9 fields), new in
`schema_version` 2 ([ADR-0136](../adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)).
The table is **append-only**, and each extraction of a movie appends one row. `write_extraction`
writes generation 1 of a new movie. `write_generation` writes a later one, and `reextract_movie`
(`src/tether/project/extract.py`) runs it.

The newest generation of a movie is its **active** generation. Its molecules are the movie's rows
of `/molecules`, `/traces` and `/patches`, so every reader of those arrays reads it. A movie with
no row (every movie of a store stamped `1`) reads as its generation 1, and reading writes nothing.
The readers are in `src/tether/io/generations.py`: `read_generations`, `active_generation` and
`read_generation`.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `movie_id` | `str:utf-8` | scalar | — | — | The join key into `/movies.movie_id`. |
| `generation` | `i4` | scalar | — | — | The number of the generation among those of the movie, from `1`. |
| `generation_id` | `str:utf-8` | scalar | — | — | `gen-<uuid4 hex>`, unique in the project. It identifies the movie's `trace_generation` instance in `/settings/recorded_inputs` (ADR-0103). |
| `created_utc` | `str:utf-8` | scalar | — | `""` (note 1) | An ISO-8601 UTC time, such as `2026-11-19T18:02:11Z`. |
| `inputs` | `str:utf-8` | scalar | — | — | A JSON object of the inputs of the extraction (note 2). |
| `n_molecules` | `i4` | scalar | count | — | The molecule count of the generation. |
| `carried_keys` | `str:utf-8` | scalar | — | `[]` (first generation) | A JSON list of the `molecule_key` of each molecule that kept its curation label, category and analysis window from the previous generation. |
| `match_px` | `f8` | scalar | pixels | `NaN` (first generation) | The donor distance that decided "did not move" for the carried molecules (`GENERATION_MATCH_PX`, `0.5`). |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

**Notes**

1. It is `""` for generation 1 of a store stamped `1`. Tether records that generation when the
   store first takes a new generation.
2. The keys of the object are these:
    - `source`: `sha256`, `uri`, `file_size` and `mtime`.
    - `geometry`: the crop, rotation and flip fields of `/movies`.
    - `layout`: the `/movies/layouts` record, or `null`.
    - `map`: `calibration_id`.
    - `colocalization`, and `options` (the movie's settings profile).

When a movie gains a generation, Tether first copies the generation that was active, unchanged,
to `/generations/archive/<movie_id>/<generation>`. The archive holds `movie` (its `/movies` row),
`molecules`, `acceptor_detected`, `traces/<layer>` (cut to the movie's frame count) and
`patches/<layer>`. It also holds `layout` (its `/movies/layouts` record), if the movie had one.

The `<movie_id>` part of that path is the movie ID with `%` written `%25` and `/` written `%2F`.
An ID of only `.` or `..` is written `%2E` or `%2E%2E`. So the IDs `run-a` and `run-a/1` get two
archives, and one is not nested in the other. Any other ID is written as it is
(`tether.io.generations.archive_name`). An archive that an older Tether wrote at the unescaped
path still reads.

Then the rows of the old active generation leave the row-aligned arrays, and the rows of the new
generation are appended **at the end**. So the rows of other movies move up, but they do not
change. An extraction writes an archive once, and it never changes or removes it.

A new generation keeps the movie's `condition_id` when every molecule of the previous
generation had one. When the previous generation has no molecule, the newest archived
generation of the movie that has molecules gives it. A new generation of a project whose
correction state is `ANALYSIS_READY` or `CORRECTED_REVIEW` also appends one row to
`/settings/correction_transitions`, to `CALIBRATION_STALE`, with the actor `policy`.

While an approval is in force (`CALIBRATION_APPROVED`, `CORRECTED_REVIEW`, `ANALYSIS_READY`), a
new generation also appends two marks to `/settings/stale_marks`. One is for the approved
`factor_version` (edge 7), and one is for the applied `correction_version` (edge 8). So *finish
review* refuses until the factors are approved again
([ADR-0136](../adr/0136-a-new-extraction-of-a-movie-is-a-new-trace-generation.md)).

`/generations/label_reviews` is an optional dataset, and it is append-only. The last review row
for a molecule applies:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_id` | `str:utf-8` | scalar | — | — | The current `molecule_id` of the molecule. |
| `label_event` | `i8` | scalar | — | `-1` (pending review) | The zero-based row index in `/labels/table` of a human accept, a human reject or a confirmation (note 1). |

**Notes**

1. `-1` marks a carried label that is pending review after a clear, or in an exported subset.

A carry-over in `carried_keys` starts pending. It stays out of supervised training until it is
confirmed on the current extraction.

A confirmation (`tether.project.generation_reconcile.confirm_carried`, the reconciliation
page) is one human row of `/labels/table` with the molecule's current label, who
confirmed and when. It does not change the label, and the earlier rows keep the original
curator and time. A confirmed reject that has a reason also gets a copy of its
`/labels/reject_log` row, with who confirmed and when, so the reason stays joined. The
reconciliation of a later generation passes over a confirmation that kept the label, so
the curator shown is still the person who set it.

## `/sources` — the source registry

Group holding `/sources/table` (`SOURCES_DTYPE`, 13 fields), new in `schema_version` 3
([ADR-0140](../adr/0140-a-source-registry-records-each-source-file-and-what-was-derived-from-it.md)).
It has one row for each source file of the project: a movie, the bead video that made a channel
map, or a map image. The registry records where each file is and what it is. It never copies a
raw movie into the project. The module is `src/tether/project/sources.py` (`read_sources`,
`register_source`, `relink_source`), and the checksum job is in `src/tether/services/sources.py`.

A movie with no row (every movie of a store stamped `1` or `2`) reads as one record made from its
`/movies` row. That record has the ID `movie:<movie_id>`, the stored `uri` as its path, and no
calculated checksum. A relative `uri` is read as the movie panel reads it. Reading writes nothing.
The first write that needs the record stores it.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `source_id` | `str:utf-8` | scalar | — | — | `src-<uuid4 hex>`, or `movie:<movie_id>` for a record first made from `/movies`. Unique in the project. |
| `relative_path` | `str:utf-8` | scalar | — | — | The file's path, read against the project file's folder, with `/` separators. A file on another drive keeps its absolute path. |
| `original_name` | `str:utf-8` | scalar | — | — | The file name at registration. |
| `file_size` | `i8` | scalar | bytes | — | The size of the file. |
| `sha256` | `str:utf-8` | scalar | — | `""` (not calculated yet) | The lower-case hex SHA-256 of the file. |
| `adapter` | `str:utf-8` | scalar | — | `""` | The adapter that reads the file. |
| `adapter_version` | `str:utf-8` | scalar | — | `""` | That adapter's version. |
| `role` | `str:utf-8` | scalar | — | — | `movie`, `bead_video` or `map_image`. |
| `acquisition` | `str:utf-8` | scalar | — | `{}` | A JSON object of acquisition values. |
| `movie_id` | `str:utf-8` | scalar | — | `""` (not a movie) | The join key into `/movies.movie_id`, for a movie. |
| `derived_from` | `str:utf-8` | scalar | — | `""` (an original) | The `source_id` of the original that a derived file was made from. |
| `recipe` | `str:utf-8` | scalar | — | `""` (an original) | The recipe that made the derived file from its original. |
| `created_utc` | `str:utf-8` | scalar | — | — | The ISO-8601 UTC time when the row was first stored. |

**The checksum** is calculated by a stored job (`/settings/jobs`, command `source-checksum`)
with one part for each source. It reads in 1 MiB blocks, with progress and cancel. A file
whose checksum differs from the one already known (the registry's or `/movies.sha256`) fails
its part, and nothing is written.

**Only a file can be a source.** `register_source` refuses a folder. A file outside the
project's folder is accepted.

**A source that moved** is linked again with `relink_source` only when the checksums match.
The new file must have the registry's checksum or, for a movie with none, its `/movies.sha256`.
The size stored is the size of the file that was read.

**A file replaced during the read is not stored.** The checksum records the device, inode
and modification time of the file it read, and the bytes read. Just before the write, the
checksum job and `relink_source` check that the path still names that file, with that size
and modification time. If another process replaced the file or wrote into it, the write is
refused (`SourceReplaced`) and nothing is stored. A write that keeps the size and lands
within the filesystem's time resolution is not seen. On a filesystem that gives no inode
number, only the size and modification time are compared, so a replacement with the same
size and modification time is not seen there either.

**A copy for sharing** (`export_subset_tether(..., curator_alias=...)`) copies the registry. It
keeps only the file name of a path that is absolute or that steps out of the folder (`..`). A
plain subset stores each path relative to its own folder, so it names the same file. A subset has
no `/movies` rows, so a movie's record takes its `/movies.sha256` with it.

## `/traces` — the integrated intensity arrays

Empty in a fresh store. It holds **up to six** datasets, named
`{donor,acceptor}_{raw,corrected,background}`
([ADR-0016](../adr/0016-extraction-trace-store-layout.md)). `write_extraction` writes all six
**only for a movie that gave at least one molecule**. A movie that colocalized zero molecules
still appends its `/movies` row, but it creates no `/traces` or `/patches` dataset at all
(`if molecules.n_molecules:`, `src/tether/imaging/extract.py`). So a valid, error-free store can
have a populated `/movies` and an entirely empty `/traces`. The other provenances write fewer
layers.

> **Do not assume that all six layers exist.** A movie with no molecules writes none. An
> analysis-only import writes only the two `corrected` layers, and a subset export usually writes
> only those two (notes 2 and 3).

> **Read the valid frames of row `i` from `/molecules.frame_range[i]`.** Do not infer them from
> where the zeros start, because the time axis is zero-padded (note 1).

> **Do not assume a physical unit or a gain for the intensities.** Nothing in the code states a
> calibration, and the meaning of the numbers depends on the provenance of the store (note 4).

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `donor_raw`, `acceptor_raw` | `f4` | `(n_molecules, max_T)` | trace intensity | absent (notes 2 and 3) | The uncorrected aperture sum, before background subtraction. |
| `donor_corrected`, `acceptor_corrected` | `f4` | `(n_molecules, max_T)` | trace intensity | absent (no molecules) | The background-subtracted disk intensity, exactly `raw − background`. |
| `donor_background`, `acceptor_background` | `f4` | `(n_molecules, max_T)` | trace intensity | absent (notes 2 and 3) | The subtracted background: the ring mean of a temporal moving average of the crop, **already scaled by the disk pixel count** `n_psf` (`tether.imaging.aperture`). |

Each dataset that is present has dtype **float32**
([`_TRACE_DTYPE = "<f4"`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py),
`src/tether/imaging/extract.py`). Each one is chunked and gzip-compressed, with
`maxshape=(None, None)`. Row order matches `/molecules/table` exactly. The time axis unit is
**frames**. The seconds for each frame are only in `/movies.frame_time`.

**Notes**

1. **In a natively appended store, the time axis is zero-padded to the longest movie in that
   store.** Appending a shorter movie leaves zeros past its end. Appending a longer one grows the
   axis and fills the tail of every existing row with zeros
   ([`_append_padded_2d`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)).
   A **subset export** instead inherits the width of the *source* store.
   [`_copy_trace_layers`](https://github.com/bioedca/tether/blob/main/src/tether/project/export.py)
   copies `ds[rows]` with the full second dimension of the source dataset
   (`src/tether/project/export.py`). So a subset's width need not match the longest movie behind
   its own molecules, and it has no `/movies` rows to check against.
2. An **analysis-only import** (from a legacy Deep-LASI text or SMD file) writes *only*
   `donor_corrected` and `acceptor_corrected`. The raw and background layers are really absent.
   See [Legacy Deep-LASI import](../io/legacy-import.md).
3. A **subset export** (`export_subset_tether`, `src/tether/project/export.py`) writes only
   `donor_corrected`/`acceptor_corrected`, unless `include_raw=True`, which is **not** the
   default. The raw and background layers travel or are dropped as a set (see
   [Background](#why-the-raw-and-background-layers-travel-together)). Detect it from the
   `tether_subset_include_raw` root attribute.
4. In a natively extracted store the values are aperture sums of raw pixel values
   (`tether.imaging.aperture.integrate_traces`). They are in whatever the source pixel dtype held
   (`/movies.pixel_dtype`, `/movies.byteorder`).
    - In a **Deep-LASI reconstruction** they are the pre-integrated `.mat` series, copied
      verbatim. The aperture integration "is skipped"
      ([`_traces_from_export`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py),
      `src/tether/project/reconstruct.py`).
    - In an **analysis-only import** they are the SMD or text series, copied verbatim.

    Only the first is a sum that Tether itself computed.

`tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS` is the single source of truth for which
pair an `intensity_quantity` selects: `corrected` and `raw` only. There is no `background`
quantity.

## `/patches` — the cached image crops

Empty until `write_extraction` runs **on a movie with at least one molecule**. A movie with zero
molecules creates neither dataset, as under `/traces` above.

> **Check the provenance of the store before you read an all-zero patch.** In a Deep-LASI
> reconstruction every patch is a zero placeholder, not an image (note 2). Do not read a zero
> patch as an out-of-frame crop in any store.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `donor` | `f4` | `(n_molecules, window, window)` | pixel value | absent (no molecules, or an analysis-only import) | The cached image crop of each molecule in the donor channel (note 1). |
| `acceptor` | `f4` | `(n_molecules, window, window)` | pixel value | absent (no molecules, or an analysis-only import) | The same, for the acceptor channel. |

**Notes**

1. In a **natively extracted** store each entry is the **temporal mean** image crop for that
   molecule and channel, in `(row, col)` image order. It is centered on the molecule's rounded
   coordinate.
    - [`_mean_patches`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py)
      (`src/tether/imaging/extract.py`) fills a molecule whose aperture window falls outside the
      frame with zeros. But no such molecule reaches disk.
    - `colocalize` keeps only molecules whose aperture window fits in **both** channels.
      `write_extraction` refuses the whole write if any integrated trace is invalid
      ([`_validate_alignment`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/extract.py):
      "refusing to write an all-zero trace").
    - The default aperture window is 21 pixels (`extract_molecules(..., window=21)`). The value
      actually used is recorded as `/settings/extraction@window`.
2. `reconstruct_project` accepts optional `donor_patches` and `acceptor_patches`.
   [`_resolve_patches`](https://github.com/bioedca/tether/blob/main/src/tether/project/reconstruct.py)
   (`src/tether/project/reconstruct.py`) **fills** an `(n_molecules, 21, 21)` stack **with zeros**
   for whichever is `None`, and the movie link makes the real crops cacheable again later.
    - No shipped caller passes them, because the `reconstruct_project` call of the GUI wizard omits
      both. So in practice the `/patches` of **every** reconstructed store are entirely zero.
    - A real reconstruction of the committed 4-molecule UCKOPSB `.mat` writes `donor` and
      `acceptor` of shape `(4, 21, 21)` float32 with `max() == 0.0`.
    - The native writer rejects out-of-frame crops before they reach disk. So a zero patch is a
      reconstruction placeholder or a really dark in-frame crop.
    - Check the provenance of the store first, and do not infer anything from the pixels.
      `/settings/extraction@profile_json` carries `"source": "m7-deeplasi-reconstruction"` for
      this path.

The two datasets are chunked and gzip-compressed, with `maxshape=(None, window, window)`. Only the
molecule axis grows, because the aperture window is fixed for each project and validated on every
later append. Row order matches `/molecules/table`. A subset export carries both stacks, with only
the rows of the exported molecules. **Analysis-only imports leave `/patches` empty**, because they
have no coordinates.

## `/settings` — provenance of each pipeline stage

Empty until a stage runs. Every child is a **group carrying scalar attributes and no datasets**,
with ten exceptions. `condition_audit`, `molecule_audit`, `lock_takeovers`, `jobs`,
`recorded_inputs`, `stale_marks`, `correction_transitions`, `gamma_contributors`,
`gamma_contributor_decisions` and `approvals` are datasets.

| Child | Written by | Lifecycle |
|---|---|---|
| `extraction` | `write_extraction` (`src/tether/imaging/extract.py`) | **Write-once** for each project, and later movies must match the recorded parameters or the write is rejected (note 1). |
| `correction` | `compute_corrected_fret` (`src/tether/project/correct.py`) | Replaced on each pass. |
| `leakage` | `compute_leakage_alpha` (`src/tether/project/leakage.py`) | Replaced on each pass. |
| `gamma` | `compute_gamma` (`src/tether/project/gamma.py`) | Replaced on each pass. A `compute_leakage_alpha` pass that withholds α **removes** it, so absent means that no γ pass is current (note 2). |
| `batch` | `run_batch` (`src/tether/project/batch.py`) | Replaced on each run. It adds one `<stage>_status` attribute for each stage. |
| `analysis_only` | `import_analysis_only_project` (`src/tether/project/analysis_import.py`) | A marker that the movie round-trip is not available for this store. |
| `condition_audit` | `tether.project.conditions` | A **dataset**: the append-only log of re-keys and merges, created when it is first needed. |
| `molecule_audit` | `tether.project.molecule_edits` | A **dataset**: the append-only log of category and analysis-window edits of molecules, created when it is first needed. |
| `lock_takeovers` | `tether.project.lock_takeovers` | A **dataset**: the append-only log of each successful lock Recover or Take over, created when it is first needed (ADR-0121). |
| `jobs` | `tether.project.jobs` | A **dataset** with one row for each job, created when it is first needed. A row changes as its job runs. |
| `recorded_inputs` | `tether.project.jobs` | A **dataset**: the append-only identities of the inputs of each record, created when it is first needed. |
| `stale_marks` | `tether.project.jobs` | A **dataset**: the append-only stale marks, created when it is first needed. A mark is never removed. |
| `correction_transitions` | `tether.project.correction_states` | A **dataset**: the append-only transitions of the correction states, created when it is first needed (ADR-0131). |
| `gamma_contributors` | `compute_gamma` (`src/tether/project/gamma.py`) | A **dataset**: the γ evidence table, one row for each examined molecule. Each γ pass replaces it (note 3). |
| `gamma_contributor_decisions` | `tether.project.gamma_contributors` | A **dataset**: the append-only exclusions and inclusions of γ contributors by a person or a recipe policy, created when it is first needed. |

**Notes**

1. Its child group `movies` gains one attribute for each movie (below).
2. That pass resets the processed `gamma` rows to `NaN`.
3. A `compute_leakage_alpha` pass that withholds α removes it, together with `gamma`.

`/settings/extraction` is the child that most readers need, because it records the integration
geometry behind `/traces` and `/patches`.

> **Do not read the `/settings/extraction` of a Deep-LASI reconstruction as the settings of an
> integration.** Tether writes the same attributes from the standard 21-pixel defaults. They
> "describe the *provenance* of the layers, not a fresh integration this module performed"
> (`src/tether/project/reconstruct.py`). Deep-LASI integrated those traces, not Tether.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `window` | `int` | scalar | pixels | — | The edge of the patch and aperture window. The default is 21. |
| `disk_radius` | `float` | scalar | pixels | — | The radius of the signal disk. |
| `ring_inner` | `float` | scalar | pixels | — | The inner radius of the background annulus. |
| `ring_outer` | `float` | scalar | pixels | — | The outer radius of the background annulus. |
| `bg_window` | `int` | scalar | frames | — | The length of the background moving average. |
| `n_psf` | `int` | scalar | pixels | — | The number of pixels in the disk mask, derived from the radii. |
| `molecule_key_quantum_px` | `float` | scalar | pixels | — | The coordinate quantum of `molecule_key` (`0.1`). |
| `app_version` | `str` | scalar | — | — | The writing Tether version. |
| `profile_json` | `str` | scalar | — | absent (note 1) | The caller's settings profile as JSON, with the `colocalization` rule that the writer stamps from a detected molecule list (ADR-0127). |

**Notes**

1. It is present only when there is a profile or a rule to record.

The stamp describes only the first extraction. So each movie's own settings profile is also
kept on the child group **`/settings/extraction/movies`**. It holds one string attribute for each
movie, named by its `movie_id`, with that movie's profile as JSON. The first movie gets one
too. A movie appended later records its `.tmap` and `.tdat` sources, detection mode and
registration verdict there.

The appends are `add_movie_to_project`
(`src/tether/project/extract.py`) and `add_reconstruction_to_project`
(`src/tether/project/reconstruct.py`). The stamp above does not change.
`read_movie_settings` (`src/tether/imaging/extract.py`) returns the record as
`{movie_id: profile}`. A project written before this record existed has no `movies` child.
It reads as `{}`
([ADR-0117](../adr/0117-a-movie-is-appended-to-a-project-once-under-its-writer-lock.md)).

`/settings/condition_audit` is a resizable 1-D compound dataset
([`_audit_dtype`](https://github.com/bioedca/tether/blob/main/src/tether/project/conditions.py) in
`src/tether/project/conditions.py`). It is append-only and never rewritten:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `event` | `str:utf-8` | scalar | — | — | `rekey`, or `merge` when the new condition already holds molecules. |
| `from_condition_id` | `str:utf-8` | scalar | — | — | The condition ID before the event. |
| `to_condition_id` | `str:utf-8` | scalar | — | — | The condition ID after the event. |
| `n_molecules` | `i8` | scalar | count | — | The number of molecules that the event re-keyed. |
| `labeler` | `str:utf-8` | scalar | — | — | Who made the change. |
| `timestamp` | `str:utf-8` | scalar | — | — | An offset-aware ISO-8601 time. |
| `reason` | `str:utf-8` | scalar | — | `""` | The reason that was given. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

`/settings/molecule_audit` is a resizable 1-D compound dataset with one row for each
`/molecules` row that an edit changed
([`_audit_dtype`](https://github.com/bioedca/tether/blob/main/src/tether/project/molecule_edits.py)
in `src/tether/project/molecule_edits.py`). The row is written **before** the `/molecules` field
changes, and an edit that changes nothing writes no row. Category edits are logged here and
**not** in `/labels`, whose `label_value` is the accept or reject codec.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `event` | `str:utf-8` | scalar | — | — | `set_category`, `clear_category`, `set_analysis_window` or `reset_analysis_window`. |
| `molecule_id` | `str:utf-8` | scalar | — | — | The `molecule_id` of the edited row. |
| `molecule_key` | `str:utf-8` | scalar | — | — | The `molecule_key` of the edited row. |
| `condition_id` | `str:utf-8` | scalar | — | — | The condition of the edited row. |
| `old_category` | `str:utf-8` | scalar | — | `""` (a window row) | The category before the edit. |
| `new_category` | `str:utf-8` | scalar | — | `""` (a window row) | The category after the edit. |
| `old_window` | `i4` | `(2,)` | frames | `[-1, -1]` (a category row) | The `[start, stop)` analysis window before the edit. |
| `new_window` | `i4` | `(2,)` | frames | `[-1, -1]` (a category row) | The `[start, stop)` analysis window after the edit. |
| `labeler` | `str:utf-8` | scalar | — | — | Who made the edit. |
| `timestamp` | `str:utf-8` | scalar | — | — | When the edit was made. |
| `reason` | `str:utf-8` | scalar | — | `""` | The reason that was given. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

`/settings/lock_takeovers` is a resizable 1-D compound dataset with one row for each successful
lock takeover
([`_dtype`](https://github.com/bioedca/tether/blob/main/src/tether/project/lock_takeovers.py) in
`src/tether/project/lock_takeovers.py`, ADR-0121). The row is written after the project has been
opened again for writing under the new lock. So a takeover that was refused or that failed writes
no row. It is append-only and never rewritten.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `timestamp` | `str:utf-8` | scalar | — | — | The UTC instant when the new lock was acquired. |
| `action` | `str:utf-8` | scalar | — | — | `recover` or `take_over`. |
| `holder_user` | `str:utf-8` | scalar | — | — | The user of the new lock. |
| `holder_host` | `str:utf-8` | scalar | — | — | The host of the new lock. |
| `holder_pid` | `i8` | scalar | — | — | The process ID of the new lock. |
| `ousted_corrupt` | `u1` | scalar | — | — | `1` when the ousted lock could not be parsed. |
| `ousted_user` | `str:utf-8` | scalar | — | `""` (corrupt lock) | The user of the ousted lock. |
| `ousted_host` | `str:utf-8` | scalar | — | `""` (corrupt lock) | The host of the ousted lock. |
| `ousted_pid` | `i8` | scalar | — | `-1` (corrupt lock) | The process ID of the ousted lock. |
| `ousted_timestamp` | `str:utf-8` | scalar | — | `""` (corrupt lock) | The last acquire or refresh of the ousted lock. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

### Jobs, recorded inputs and stale marks

`/settings/jobs` holds one row for each job of the job and dependency coordinator
([ADR-0126](../adr/0126-jobs-and-stale-marks-are-stored-in-the-project.md)). Every field is a
UTF-8 string:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `job_id` | `str:utf-8` | scalar | — | — | 32 hex characters. |
| `command` | `str:utf-8` | scalar | — | — | The command of the job. |
| `request` | `str:utf-8` | scalar | — | — | The service request in its text form (ADR-0100). |
| `state` | `str:utf-8` | scalar | — | — | One of the states in the table below. |
| `parts` | `str:utf-8` | scalar | — | — | A JSON array of the part names. |
| `completed` | `str:utf-8` | scalar | — | — | A JSON array of the complete part names: the checkpoint. |
| `inputs` | `str:utf-8` | scalar | — | — | A JSON object from `"<node key>:<instance id>"` to the identity of that input. |
| `owner` | `str:utf-8` | scalar | — | — | JSON: the `host`, `user`, `pid` and lock `nonce` of the writer that last ran the job. |
| `created_utc` | `str:utf-8` | scalar | — | — | An ISO-8601 UTC time. |
| `started_utc` | `str:utf-8` | scalar | — | `""` (not reached) | An ISO-8601 UTC time. |
| `ended_utc` | `str:utf-8` | scalar | — | `""` (not reached) | An ISO-8601 UTC time. |
| `error` | `str:utf-8` | scalar | — | — | Which part failed, and why. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

The `state` is one of:

| State | Meaning |
|---|---|
| `queued` | Stored and not started. |
| `running` | A writer session is doing the parts now. |
| `done` | Every part is complete. |
| `failed` | A part raised an error, and `error` says which and why. A retry continues from `completed`. |
| `cancelled` | Stopped by the user, and the complete parts are kept. A retry continues from `completed`. |
| `interrupted` | It was running when its owner stopped. A retry continues from `completed`. |

A stored `running` row can outlive its writer: the lock no longer carries its `nonce`, or its
process on this computer is not running. Such a row is **read** as `interrupted`, and the next
writer stores it so. A reader needs no lock and the file is closed between parts, so a reader
never waits on a job.

`/settings/recorded_inputs` has one row for each direct input of a record of the dependency graph
([ADR-0103](../adr/0103-the-project-dependency-graph-and-the-stale-rules.md)). Tether writes the
row once, when the record is made. `/settings/stale_marks` has one row for each stale mark. A
record with a mark is stale: it stays readable and never supplies a current result. Both datasets
are append-only, and every field of both is a UTF-8 string:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `node`, `instance` | `str:utf-8` | scalar | — | — | The record: its node of the graph and its instance. In `stale_marks`, the record that is stale. |
| `upstream_node`, `upstream_instance` | `str:utf-8` | scalar | — | — | The upstream record that the record was made from. In `stale_marks`, the record that the mark came from. |
| `identity` | `str:utf-8` | scalar | — | — | `recorded_inputs` only: the identity of that input. |
| `edge` | `str:utf-8` | scalar | — | — | `stale_marks` only: the number of the edge of ADR-0103 that the mark crossed. |
| `edited_node`, `edited_instance` | `str:utf-8` | scalar | — | — | `stale_marks` only: the edit that started the mark. |
| `event` | `str:utf-8` | scalar | — | — | `stale_marks` only: the event of that edge. |
| `timestamp` | `str:utf-8` | scalar | — | — | `stale_marks` only: an ISO-8601 UTC time. |
| `app_version` | `str:utf-8` | scalar | — | — | `stale_marks` only: the writing Tether version. |

### Correction states

`/settings/correction_transitions` holds one row for each transition of the correction states
([ADR-0131](../adr/0131-the-correction-states-are-a-transition-log-in-the-project.md)). The
present state of a scope is the `to_state` of its last row, and the rows are append-only. Every
field is a UTF-8 string:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `scope` | `str:utf-8` | scalar | — | — | The calibration scope, `project`. |
| `from_state` | `str:utf-8` | scalar | — | — | The state before the transition, such as `GAMMA_REVIEW`. |
| `event` | `str:utf-8` | scalar | — | — | The event, such as `approve`. |
| `to_state` | `str:utf-8` | scalar | — | — | The state after the transition. |
| `by` | `str:utf-8` | scalar | — | — | `person` or `policy`. |
| `actor` | `str:utf-8` | scalar | — | — | The person's name, or the policy text of the recipe. |
| `detail` | `str:utf-8` | scalar | — | — | A JSON object with the values that the guard read (note 1). |
| `timestamp` | `str:utf-8` | scalar | — | — | An ISO-8601 UTC time. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

**Notes**

1. The keys are `alpha`, `gamma`, `source`, `factor_version`, `correction_version`, and the
   `guard` and `action` of the edge. The first row of a project that already held factors also
   has `start_proposal`.

A scope with no row is in its start state: `APPARENT_REVIEW`, or `GAMMA_REVIEW` when
`/settings/leakage`, `/settings/gamma` or `/settings/correction` exists. Such a project never
reads as approved. A corrected analysis is refused in every state except `ANALYSIS_READY`, and an
apparent-E analysis is never refused.

### γ evidence table

`/settings/gamma_contributors` holds one row for each molecule that the last γ pass examined, in
`/molecules` order.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_key` | `str:utf-8` | scalar | — | — | The molecule. |
| `step` | `i4` | scalar | frames | — | The absolute acceptor-bleach frame. |
| `step_source` | `str:utf-8` | scalar | — | — | Where `step` came from. Today it is always `detector`. |
| `level_window` | `str:utf-8` | scalar | — | — | The level window that averaged the intensities: `half-window` or `whole-segment`. |
| `half_window` | `i4` | scalar | frames | `0` (`whole-segment`) | The half-width of a `half-window` level window. |
| `pre_window` | `i4` | `(2,)` | frames | — | The absolute half-open frame range of the level before the step. |
| `post_window` | `i4` | `(2,)` | frames | — | The absolute half-open frame range of the level after the step. |
| `alpha` | `f8` | scalar | dimensionless | — | The leakage factor that corrected the acceptor levels of the row. |
| `donor_pre` | `f8` | scalar | trace intensity | — | The donor level before the step. |
| `donor_post` | `f8` | scalar | trace intensity | — | The donor level after the step. |
| `acceptor_pre` | `f8` | scalar | trace intensity | — | The leakage-corrected acceptor level before the step. |
| `acceptor_post` | `f8` | scalar | trace intensity | — | The leakage-corrected acceptor level after the step. |
| `delta_donor` | `f8` | scalar | trace intensity | — | ΔI_D. |
| `delta_acceptor` | `f8` | scalar | trace intensity | — | ΔI_A. |
| `ratio` | `f8` | scalar | dimensionless | `NaN` (note 1) | The raw ΔI_A / ΔI_D, with its sign. |
| `gamma` | `f8` | scalar | dimensionless | `NaN` (the gates exclude the row) | The γ of the trace. |
| `gate` | `str:utf-8` | scalar | — | — | The estimator's reason: `ok`, `no-step`, `short-pre`, `short-post`, `degenerate-donor` or `out-of-range`. |
| `included` | `bool` | scalar | — | — | Whether the row is in the median. |
| `reason` | `str:utf-8` | scalar | — | — | The reason of an exclusion (note 1). |
| `by` | `str:utf-8` | scalar | — | — | The kind of author of an exclusion: `estimator`, `person` or `policy`. |
| `actor` | `str:utf-8` | scalar | — | — | The author of an exclusion. |

**Notes**

1. An excluded row keeps its `ratio`, also when it is negative, zero or above the ceiling. The
   `ratio` is `NaN` only when no step was measured (`no-step`, `short-pre` or `short-post`), or
   when ΔI_D is zero. Then the `reason` is `zero-donor-step`, followed by a semicolon and the
   reason of a person or a policy that excluded the row.

With the stored traces and the row's `alpha`, the windows give each row again. This is also true
after a later leakage pass changes `/molecules.alpha`.

The population γ is the median of the included rows. It is withheld below `min_qualifying_traces`
included rows, so with no decision it is the γ that `compute_gamma` gave before the table.
`/settings/gamma` counts the decisions in `n_excluded`.

`/settings/gamma_contributor_decisions` holds the decisions of people and policies. The latest row
for a `molecule_key` holds, and each γ pass reads it:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_key` | `str:utf-8` | scalar | — | — | The molecule. |
| `included` | `bool` | scalar | — | — | `True` for an inclusion, and `False` for an exclusion. |
| `reason` | `str:utf-8` | scalar | — | — | The reason. An exclusion needs one. |
| `by` | `str:utf-8` | scalar | — | — | `person` or `policy`. |
| `actor` | `str:utf-8` | scalar | — | — | The person's name, or the policy text. |
| `timestamp` | `str:utf-8` | scalar | — | — | When the decision was made. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

`set_gamma_contributor` writes the decision and the new γ pass in one guarded write. An exclusion
needs a reason. An inclusion withdraws a decision but never a gate. The correction state follows
the change:

- In `CALIBRATION_APPROVED` the evidence is frozen and the change is refused.
- In `CORRECTED_REVIEW` and `ANALYSIS_READY` the change is the evidence-change event, and the
  state becomes `CALIBRATION_STALE`.
- In `GAMMA_REVIEW` a withheld γ moves the state to `CALIBRATION_BLOCKED` before the write,
  so a failed event writes nothing. In `CALIBRATION_BLOCKED` a valid γ moves it back to
  `GAMMA_REVIEW` after the write, and the same decision again sends an event that failed.

### Approvals

`/settings/approvals` is a resizable compound dataset with one row for each approval of a step
that needs a person
([ADR-0102](../adr/0102-a-batch-run-stops-at-an-approval-step-or-applies-a-recipe-policy.md),
section 4). Only an approval writes a row, so a policy whose rule does not approve writes none.
The rows are append-only, and every field is a UTF-8 string:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `step` | `str:utf-8` | scalar | — | — | `map.accept`, `alpha.confirm`, `gamma.approve` or `fit.select`. |
| `subject` | `str:utf-8` | scalar | — | — | The map, the condition or the analysis set. |
| `by` | `str:utf-8` | scalar | — | — | `person` or `policy`. |
| `actor` | `str:utf-8` | scalar | — | — | The person's name, or the policy text exactly as the recipe gives it. |
| `recipe_sha256` | `str:utf-8` | scalar | — | `""` (no recipe run asked) | The identity of the recipe whose run asked for the approval. |
| `recipe_version` | `str:utf-8` | scalar | — | `0` (no recipe run asked) | The version of that recipe. |
| `report` | `str:utf-8` | scalar | — | — | A JSON object from each evidence file name to its SHA-256: the report's JSON and its PNG images. |
| `timestamp` | `str:utf-8` | scalar | — | — | An ISO-8601 UTC time. |
| `app_version` | `str:utf-8` | scalar | — | — | The writing Tether version. |

A recipe run (`tether run`) stores a job in `/settings/jobs` for each step that writes a project.
For a step that writes no project, it records progress in the checkpoint of each user. The job's
single part is a JSON object with `recipe_sha256`, `recipe_version` and `step` (the step number).
Jobs written by the current runner also store `run_id` and `attempt_id`, which identify the run
and the step attempt.

`--resume` checks project jobs against the recipe checkpoint in Tether's state for each user. A
step that stopped at an approval step is stored `cancelled`. The cancellation reason holds the
requested step and subject, so that a later approval can identify its recipe.

## `/idealization` — one subgroup per fitted model

Empty until an idealization runs. Each fit lands in `/idealization/{model_name}`
([ADR-0024](../adr/0024-idealization-store-layout-staleness-and-nstates.md)). It is staged first
as `{model_name}.__writing__` and then swapped in by **delete-then-rename**. The staged group is
complete before the swap, so a real model name never holds a half-written fit.

But the swap itself is **not fully atomic** (see
[Background](#why-the-idealization-swap-is-not-atomic)). The swap removes the old model and then
renames the staged one. A crash between those two steps can leave *only* the `.__writing__` group
and no model at all (`write_idealization_model`, `src/tether/project/idealize.py`). Running the write again
repairs it. A reader should normally skip a name that ends in `.__writing__`. But it must not
treat its presence as proof that a good model exists beside it.

`n_fitted` below is the number of molecules **that this fit covered**. It is *not* the molecule
count of the store, and the rows are *not* in the row order of the store.

> **Join the per-molecule arrays on `molecule_id`, never with a `/molecules/table` row number.**
> Unlike `/traces` and `/patches`, the five arrays of shape `(n_fitted, …)` cover only the
> molecules included in this fit (note 3).

> **Plot and export `frac`, not `pi`.** `pi` is the unnormalized Dirichlet posterior, and it is not
> a probability vector.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `mean` | `f8` | `(nstates,)` | dimensionless (E) | — | The FRET level of each state. Always present. |
| `var` | `f8` | `(nstates,)` | dimensionless | absent (no variances) | The variance of each state, present when the fit produced variances. |
| `tmatrix` | `f8` | `(nstates, nstates)` | count | absent | The unnormalized transition counts. |
| `norm_tmatrix` | `f8` | `(nstates, nstates)` | dimensionless | absent | The row-normalized transition probabilities. |
| `rates` | `f8` | `(nstates, nstates)` | per frame | absent | The rates per frame. |
| `pi` | `f8` | `(nstates,)` | dimensionless | absent | The **unnormalized** Dirichlet posterior. It sums to about the trace count, and it is *not* a probability vector. |
| `frac` | `f8` | `(nstates,)` | dimensionless | absent | The normalized populations. **Use this for plots and exports**, not `pi`. |
| `priors/<name>` | `f8` | `(nstates,)` or `(nstates, nstates)` | — | absent | An optional subgroup of the variational prior hyperparameters (note 1). |
| `idealized` | `f8` | `(n_fitted, n_frames)` | dimensionless (E) | `NaN` (outside the analysis window) | The idealized FRET level of each frame, gzip-compressed. Always present. |
| `state_path` | `i8` | `(n_fitted, n_frames)` | — | `-1` (`tether.idealize.NO_STATE`, outside the analysis window) | The state index of each frame, gzip-compressed. Always present. |
| `molecule_key` | `str:utf-8` | `(n_fitted,)` | — | — | The `molecule_key` of each fitted molecule. Always present. |
| `molecule_id` | `str:utf-8` | `(n_fitted,)` | — | — | The **unique** join key for staleness and export. Always present. |
| `input_hash` | `str:utf-8` | `(n_fitted,)` | — | — | A hash, for each molecule, of everything that the fit consumed (note 2). Always present. |

**Notes**

1. The `priors/` subgroup is not uniformly 1-D. `src/tether/idealize/driver.py` lists the
   canonical member names. `a_prior`, `b_prior`, `beta_prior`, `mu_prior` and `pi_prior` are all
   `(nstates,)`. `tm_prior`, the Dirichlet prior of the transition matrix, is `(nstates, nstates)`.
   Read the shape of each member, and do not size a buffer from `nstates` alone.
2. The paragraphs after these notes describe `input_hash`.
3. The writer sizes these arrays from `len(molecule_keys)` and stamps that count as the group's
   `n_molecules` attribute. A subset export narrows them further. For example, a real store with
   3 molecules, idealized over 2 of them, holds `idealized` of shape `(2, 40)` and
   `n_molecules = 2`.

The difference between `pi` and `frac` is the population model of
[ADR-0041](../adr/0041-population-model-and-ebfret.md).

`input_hash` is a composite hash, for each molecule, of everything that the
fit consumed. It covers the windowed donor and acceptor input, the `intensity_quantity`, the bounds
of the analysis window, and the molecule's **effective applied** α and γ.

The `correction_method` string is *not* folded into the digest itself. `input_provenance_hash`
passes it through
[`_effective_factors`](https://github.com/bioedca/tether/blob/main/src/tether/project/idealize.py)
(`src/tether/project/idealize.py`). That function maps every apparent-E method to the identity
`(α = 0, γ = 1)`, and only the resulting pair is hashed. The apparent-E methods are `""`,
`apparent-E (corrections unavailable)` and `apparent-E (user toggle)`.

So a row that moves between those three methods keeps the **same** `input_hash` (see
[Background](#why-an-apparent-e-method-does-not-change-input_hash)). If you fold the method string
in yourself, you will falsely mark live idealizations stale. A real correction change makes
exactly the dependent idealizations stale again, with a scope for each factor
([ADR-0029](../adr/0029-idealization-correction-provenance-hash-and-per-factor-staleness.md)).

The group attributes are these. `type`, `nstates`, `dtype` (the idealized quantity, such as
`FRET`), `intensity_quantity`, `nstates_selected_by`, `app_version` and `created_utc`.
`n_molecules` is the **fitted** count, `n_fitted` above, not the molecule count of the store.
`elbo` and `elbo_by_nstates` (a JSON string) are present when the fitter produced them. These
names are reserved, and the `extra_attrs` of a caller cannot override them
([`_RESERVED_MODEL_ATTRS`](https://github.com/bioedca/tether/blob/main/src/tether/project/idealize.py)).

## `/features` — the ML feature cache

Empty until `compute_features` runs, which writes `/features/table`. It is a resizable 1-D
compound dataset of **11 fields**: two ID columns, then **exactly one column for each
`tether.ml.features.FEATURE_NAMES` entry**, in that order.
`n_frames` is a member of that feature list, not a separate column beside it. It is the only
**integer** feature, the only entry in
[`_INT_FEATURES`](https://github.com/bioedca/tether/blob/main/src/tether/project/features.py), and
the other eight features are `f8`
([`_feature_table_dtype`](https://github.com/bioedca/tether/blob/main/src/tether/project/features.py),
`src/tether/project/features.py`). Undefined features are `NaN`, never fabricated.

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `molecule_id` | `str:utf-8` | scalar | — | — | The molecule. |
| `molecule_key` | `str:utf-8` | scalar | — | — | The `molecule_key` of the molecule. |
| `n_frames` | `i8` | scalar | frames | — | The length of the analysis window. |
| `total_intensity` | `f8` | scalar | trace intensity | `NaN` (undefined) | The mean total signal of each frame, `mean(D + A)`. |
| `snr` | `f8` | scalar | dimensionless | `NaN` (undefined) | The total-intensity signal-to-noise ratio, `mean(D + A) / std(D + A)`. |
| `fret_mean` | `f8` | scalar | dimensionless | `NaN` (undefined) | The mean apparent FRET efficiency over the window. |
| `fret_var` | `f8` | scalar | dimensionless | `NaN` (undefined) | The population variance of the apparent FRET efficiency. |
| `anticorr_lag0` | `f8` | scalar | dimensionless | `NaN` (undefined) | The zero-lag Pearson correlation of donor and acceptor. A negative value means anti-phase. |
| `anticorr_lag1_magnitude` | `f8` | scalar | dimensionless | `NaN` (undefined) | The absolute value of `r[+1]`, the lag-1 value of the normalized cross-correlation. |
| `neighbor_distance` | `f8` | scalar | pixels | `NaN` (no neighbor) | The distance from the donor spot to the nearest other donor spot in the same movie. |
| `aperture_overlap` | `f8` | scalar | — | `NaN` (undefined) | `1.0` when the aperture of that nearest neighbor overlaps this molecule's aperture, else `0.0`. |

The dataset attributes are `app_version`, `created_utc`, `intensity_quantity`, `n_molecules`,
`feature_names` and `feature_schema_version`. `feature_names` is a JSON list, and it is the
authoritative column order. `feature_schema_version` is **a second, independent version number**
(`FEATURE_SCHEMA_VERSION`, now `1`). It governs the cache layout only, and it is not the
store's `schema_version`. The table is a cache that can be computed again, so a mismatch is a
prompt to compute it again, not a migration.

Each row comes from that molecule's traces, sliced to its `analysis_window`. A later
window change makes the row stale. So each writer below deletes the whole table when it
changes the window of a row that the table holds. It deletes the table before it writes the
new window. The readers then raise "run compute_features first".

| Writer | Module |
|---|---|
| `set_analysis_window`, `reset_analysis_window` | `src/tether/project/molecule_edits.py` |
| `compute_photobleach` (the auto window) | `src/tether/project/photobleach.py` |
| `apply_reconcile` (an accepted tMAVEN window edit) | `src/tether/project/handoff.py` |

A writer that changes no featured window keeps the table. An SMD analysis import
(`import_analysis_only_project`) also seeds windows, but it builds a new store. The
store that it publishes has no feature table, also when it replaces a project that had one.

## `/calibration` — the channel registration maps

Empty until `write_calibration` runs. Each map lands under `/calibration/<calibration_id>`, which
`/movies.calibration_id` references, and it is **write-once** for each ID. It has two direction
subgroups, `ref_to_moving` and `moving_to_ref`. Each one holds four float64 datasets: `a` and `b`
(the polynomial coefficient vectors), and `norm_xy` and `norm_uv` (the `(3, 3)` normalization
matrices).

The group attributes record the scalar provenance:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `reference_channel` | `int` | scalar | — | — | The reference channel of the map. |
| `moving_channel` | `int` | scalar | — | — | The channel that the map moves. |
| `rms_residual` | `float` | scalar | pixels | — | The RMS residual of the fit. |
| `n_control_points` | `int` | scalar | count | — | The number of control points. |
| `gate_px` | `float` | scalar | pixels | — | The residual gate that the fit was judged against. |
| `degree` | `int` | scalar | — | — | The degree of the polynomial. |
| `low_confidence` | `bool` | scalar | — | — | True when `rms_residual` is above `gate_px`. |
| `source` | `str` | scalar | — | — | `native` or `imported`. |
| `provenance_json` | `str` | scalar | — | — | The provenance of the map, as JSON. |

The group also holds the geometry attributes of each channel, which
[`_write_geometry_attrs`](https://github.com/bioedca/tether/blob/main/src/tether/imaging/calibrate.py)
(`src/tether/imaging/calibrate.py`) writes.

## `/models` — a reference to each condition's model file

`/models` exists in every store because the schema freeze declared it in advance. The quality
ranker itself is **not** in the project. It is a standalone portable model file that travels with
a *condition* across many experiment files (`src/tether/ml/persistence.py`, ADR-0035). The
project only records **where that file is and which version of it the project last saw**.

The project keeps one group for each condition, which
[`record_model_reference`](https://github.com/bioedca/tether/blob/main/src/tether/project/model_artifact.py)
(`src/tether/project/model_artifact.py`, ADR-0119) writes. The group is empty until a model is
saved for a condition that the project holds. `/models/<condition_id>` is a group with no datasets
and these attributes:

| Field | Type | Shape | Unit | Empty value | Meaning |
|---|---|---|---|---|---|
| `reference_version` | `int` | scalar | — | — | The layout version of this group (`MODEL_REFERENCE_VERSION`, now `1`), independent of the store's `schema_version`. |
| `condition_id` | `str` | scalar | — | — | The condition, the same as the group name. |
| `artifact_path` | `str` | scalar | — | — | The model file, as an absolute path on the computer that saved it. |
| `artifact_sha256` | `str` | scalar | — | — | The SHA-256 of the model file at that save. Compare it with the file to see whether the model changed since. |
| `n_retrains` | `int` | scalar | count | — | The model's retrain count at that save. |
| `model_updated_utc` | `str` | scalar | — | — | The model's last fit time, in ISO-8601 UTC. |
| `recorded_utc` | `str` | scalar | — | — | When this reference was written, in ISO-8601 UTC. |
| `app_version` | `str` | scalar | — | — | The Tether version that wrote it. |

A later save replaces the attributes of that condition's group. A project keeps one
reference for each condition. The model file has its own single-writer lock: `<model>.lock`
beside it, the same record as a project's `.lock`. The condition's owner-curator
holds it while they retrain and save.

The reference has no user or host field. Its `artifact_path` is an absolute path. A path can
still name a user or a host, such as a home folder or a network share. If you share a project or a
subset of it, keep the model under a neutral folder.

Subset export copies the group verbatim (`src/tether/project/export.py`). A missing
`/models` child means "no model recorded". An `artifact_path` that does not exist on
the reader's computer is normal: the file can be on another computer or in a synced
folder.

## Background

### Why the raw and background layers travel together

A subset export keeps or drops the raw and background layers as one set, on purpose. The relation
`corrected = raw − background` is exact. So if a subset kept the background layer, the raw layer
could be reconstructed from it. That would defeat the invariant that a subset carries no movie
data.

### Why an apparent-E method does not change input_hash

The three apparent-E methods map to the same identity factors, so a row that moves between them
keeps its `input_hash` on purpose. The corrected E that such a row would feed is never displayed.
So nothing that it feeds is stale.

### Why the idealization swap is not atomic

HDF5 has no atomic rename-over inside a file. So `write_idealization_model` must delete the old
model before it renames the staged one, and a crash can fall between the two steps.

## Where to look next

- [Legacy Deep-LASI import](../io/legacy-import.md): what Tether reads *in*, and which legacy
  artifacts carry coordinates.
- [Architecture decisions](../adr/README.md): the record behind every rule on this page, notably
  [ADR-0005](../adr/0005-m0-schema-freeze.md) (the freeze),
  [ADR-0016](../adr/0016-extraction-trace-store-layout.md) (traces, patches and `molecule_key`)
  and [ADR-0024](../adr/0024-idealization-store-layout-staleness-and-nstates.md) (the
  idealization layout).
- [`tether-schema.json`](tether-schema.json) (`schema/schema_frozen.json` in the repository): the
  machine-checked manifest of the frozen skeleton (14 group paths, six compound tables, three
  root attributes). It is the tiebreaker if this page ever disagrees with it *about those*. It says
  nothing about the additive payloads. For those, the writer module named in each section is the
  authority.
