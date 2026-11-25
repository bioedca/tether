---
description: "Each analysis default that Tether applies, how to change it, and if your project records it for a methods section."
---

# Analysis parameters

**Who this page is for.** The person who runs the experiment and wants to know a default, change
it, or report it in a methods section. It is not for the person who maintains the code. It assumes
that you know the stages of an analysis in [How Tether works](../pipeline.md).

Every default on this page comes from the code, not from a specification. Where the frozen
specification and the code disagree, **this page prints the value of the code, because the code is
what ships**.

## How to read this page

Each table row is one parameter. Every row has the same six columns, in this order:

| Column | What it holds |
| --- | --- |
| **Parameter** | The keyword or field that you set, and the module that defines its default. |
| **Default** | The shipped value. |
| **Type and unit** | The type, the unit and the allowed values. Counts of frames are in frames, lengths are in pixels and times are in seconds. |
| **Meaning** | What the parameter controls, in at most 25 words. A longer note is in the numbered notes below the table. |
| **Set via** | A real surface: a `tether` CLI flag, a GUI control, an extraction profile or a keyword argument. The cell says so when there is no CLI flag or no GUI control. |
| **Recorded in `.tether`** | The path in the project file that stores the value that you used, or **no** if the project file does not store it. |

A note gives the effect of a larger or a smaller value. A warning before a table gives a fact that
can change a published number. The [Background](#background) section at the end gives the reasons.

Tether has no settings dialog. You set a value in one of these ways:

- **An extraction profile.** All nineteen `ExtractOptions` fields can be set from an
  [extraction profile](../cli.md#extraction-profiles). A profile is a JSON file that
  `tether extract --profile` and `tether batch --profile` read. A field that the file leaves out
  keeps the default in the table.
- **A `tether extract` flag.** Eleven of the nineteen fields also have a flag: `--donor-side`,
  `--detection-mode`, `--detection-threshold`, `--detection-block`, `--min-separation`,
  `--window`, `--prealign`, `--pair-tol`, `--rms-gate`, `--coloc-distance` and
  `--colocalization`. A profile replaces these flags, and you cannot use the two together.
- **A profile or Python only.** The other eight fields have no flag and no GUI control. They are
  the channel layout (`channel_layout`), the aperture group (`disk_radius`, `ring_inner`,
  `ring_outer`, `bg_window`) and the prealign group (`prealign_upsample`, `prealign_low_sigma`,
  `prealign_high_sigma`).
- **A `tether batch` flag.** `tether batch` has a flag for one field, `--colocalization`. A profile
  replaces it in the same way. Every movie in a batch is extracted with the values of the profile.
  Without a profile, every movie is extracted at the `ExtractOptions` defaults and the rule that
  you name. The `--tdat` option is the one exception, and the
  [Detection and extraction](#detection-and-extraction) section tells what it does.
- **A keyword argument from Python.** Python drives the downstream stages: photobleaching,
  corrections, idealization and analysis. Their "Set via" cells name the keyword argument of the
  project-level function. The table says so when a value has no surface at all.

Tether records all nineteen extraction fields in the same way, whether the value came from a flag,
a profile or Python.

## Science tunables vs rendering defaults

Each default is one of two kinds. Most constants name their kind in a comment in the source code,
and this page uses the same labels.

- A **science tunable** changes a reported number. Examples are the leakage ceiling, the γ
  estimator half-window, the bootstrap resample count and the idealization state grid.
- A **rendering default** changes only how a distribution is drawn. Examples are the histogram
  bin count, the transition-density grid resolution and the dwell-time histogram bin count. A
  change redraws a plot. It does not change a fitted value.

This page differs from the source labels in four places. Three parameters are labeled
*analysis/rendering* in their source comments. This page tables them as **science tunables**: the
frame interval (`dt` / `time_dt`), the dwell-fit model and CI level, and the HMM convergence
budget (`n_iter`). The fourth is the dwell `state` selector, which is in the science table but
changes no number. The [Background](#why-three-analysis-parameters-are-science-tunables) gives the
reasons.

> **Tune a science parameter to change a number, and a rendering parameter to change a picture.**
> A rendering default can still change what you see. For example, **half the bin count can visibly
> move the apparent position of a peak, but no fitted state mean changes.**

The two groups have separate tables under [Analysis and histograms](#analysis-and-histograms).

## Detection and extraction

The defaults are the fields of `ExtractOptions` (`tether.project.extract`), which is the pinned
CLI contract. Sixteen of the nineteen fields pin an explicit value, and do not pass through to the
default of the primitive below them. Three fields are `None` sentinels. `detection_threshold` and
`min_separation` defer to the faithful default of each detection mode, in the table below.
`channel_layout` stands for the side-by-side split that `donor_side` describes.

Tether serializes all nineteen fields verbatim into the `profile_json` attribute of
`/settings/extraction`. It does so on the **first** extraction into a project. A later movie that
you append to the same project leaves that record untouched. Tether checks the aperture and
integration parameters of the later movie against the record, and does not record them again.

> **Check which half of the frame is the donor before you extract.** A `donor_side` that is
> backwards swaps donor and acceptor, which inverts every FRET efficiency. The advisory
> [donor-side check](#tolerances-and-gates) warns after extraction when there are enough bleach
> events to tell.

> **After a run with `--tdat`, read the detection settings from `/settings/extraction`.** The
> `--tdat` option of `tether extract` and `tether batch` applies a Deep-LASI detection config. It
> replaces `detection_mode` and `detection_threshold` with the values decoded from the file, before
> Tether writes the extraction profile. Their two rows below then do not describe your run.
>
> Tether also records the source file name as `tdat_source`. It refuses `--tdat` beside a flag or a
> profile that sets either field ([the rule](../cli.md#detection-settings-pick-one-source)).
> `--tdat` changes no other extraction parameter. The `--tmap` option replaces no field, but it
> makes `donor_side` inert (note 1).

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| <a id="param-donor-side"></a>`donor_side`<br>`tether.project.extract` | `left` | enum `left`/`right` | Which horizontal half of the frame is the donor channel (note 1). | `tether extract --donor-side` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| <a id="param-channel-layout"></a>`channel_layout`<br>`tether.project.extract` | `None` | a `ChannelLayout` (`tether.imaging.layout`): kind `side-by-side`/`top-bottom`/`rectangles` | Where the donor channel and the acceptor channel are in the frame (note 2). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), in its four `channel_layout` fields, or `ExtractOptions(channel_layout=…)` from Python — no flag of its own, no GUI control | `/settings/extraction`; the layout each movie was split by is also stored as `/movies/layouts/<movie_id>` |
| <a id="param-detection-mode"></a>`detection_mode`<br>`tether.project.extract` | `wavelet` | enum `wavelet`/`intensity`/`bandpass`/`zmw` | Which particle finder runs (note 3). | `tether extract --detection-mode` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`); supplied automatically by `--tdat` | `/settings/extraction` |
| <a id="param-detection-threshold"></a>`detection_threshold`<br>`tether.project.extract` | `None` (per-mode: `0.5` intensity, `0.98` bandpass, `0.5` zmw) | dimensionless fraction of the detection-image maximum | How bright a pixel must be to survive (note 4). **Recorded but inert under `wavelet`.** | `tether extract --detection-threshold` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`); supplied automatically by `--tdat` | `/settings/extraction` |
| `detection_block`<br>`tether.project.extract` | `50` | frames | Block size of the moving-average max-projection that the detector runs on (note 5). | `tether extract --detection-block` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| <a id="param-min-separation"></a>`min_separation`<br>`tether.project.extract` | `None` (per-mode: `8.0` wavelet, `3.0` intensity, bandpass and zmw) | pixels | Non-maximum-suppression radius: two candidates closer than this collapse to one (note 6). | `tether extract --min-separation` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| `window`<br>`tether.project.extract` | `21` | pixels, odd | Side length of the aperture and colocalization crop box (note 7). | `tether extract --window` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| `disk_radius`<br>`tether.imaging.aperture` | `3.0` | pixels | Radius of the signal disk that Tether sums in each frame: 29 pixels at the default, on the default 21-pixel aperture window (note 8). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(disk_radius=…)` or `integrate_traces(disk_radius=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `ring_inner`<br>`tether.imaging.aperture` | `6.0` | pixels | Inner radius of the background annulus (note 9). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(ring_inner=…)` or `integrate_traces(ring_inner=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `ring_outer`<br>`tether.imaging.aperture` | `8.0` | pixels | Outer radius of the background annulus: 84 pixels at the defaults (note 10). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(ring_outer=…)` or `integrate_traces(ring_outer=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `bg_window`<br>`tether.imaging.aperture` | `10` | frames | Length of the uniform temporal moving average that Tether applies to the crop before it takes the ring mean (note 11). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(bg_window=…)` or `integrate_traces(bg_window=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |

**Notes**

1. Tether ignores `donor_side` when you import a Deep-LASI `.tmap`, because the crop geometry of
   the map then splits the channels. The warning above the table tells what a backwards value does.
2. The channel layouts:
    - `None` is the side-by-side split, with the donor on the `donor_side` half.
    - `top-bottom` cuts the frame into a top half and a bottom half. The donor is `top` or
      `bottom`. The halves are stacked, not rotated.
    - `rectangles` takes one `[y1, x1, y2, x2]` rectangle for each channel. The coordinates are
      1-based and inclusive.
    - Both channels must be the same size, so a half layout drops an odd trailing column or row.
      Tether refuses two rectangles of different sizes, or a rectangle outside the frame, with an
      error. Rectangles that overlap give a `ChannelOverlapWarning`.
    - Each channel can also take a `ChannelRecipe` (`tether.imaging.recipe`, with `donor_recipe` and
      `acceptor_recipe`). Its rotate, flip, translate and crop steps run in order on the cut of the
      channel, with no resampling. The two channels must then be the same size after the recipes.
      A profile cannot hold a recipe yet.
    - Tether refuses a layout beside a non-default `donor_side`, or beside an imported `.tmap`.
3. `wavelet` is an à trous multi-scale detector that is robust to smooth background gradients.
   `intensity` and `bandpass` are Crocker-Grier style band-pass finders driven by a brightness
   threshold. `zmw` is Deep-LASI mode 5, made for zero-mode waveguides. It is the `intensity`
   finder with a small 3 × 3 disc average in place of the band-pass, so it keeps spots up to the
   image edge. The mode also sets the default `min_separation`. It therefore changes both which
   spots are found and how close two of them can be.
4. A higher threshold finds fewer, brighter molecules. A lower threshold finds more, with false
   positives that you must curate out later. Under `bandpass` the value has two uses. It floors
   the image, and it keeps only the top `1 - threshold` fraction of band-pass values. It therefore
   has a much stronger effect under `bandpass` than under `intensity`.
5. A larger block averages away more blinking and shot noise. But Tether drops the trailing partial
   block (the `T % block` remainder frames), so a large block silently discards more of the end of
   the movie. A smaller block lets single-frame spikes into the per-pixel maximum, and changes
   which spots are found. A movie shorter than one block falls back to a sum projection.
6. A larger radius merges distinct molecules and costs recall. A smaller radius keeps duplicate
   centroids on a single PSF. A value that you supply overrides the per-mode default for **every**
   mode.
7. The value must be odd and at least twice `ring_outer`. Tether rejects a smaller value. A larger
   box excludes more molecules near the frame edge, because their crop no longer fits.
8. A larger disk captures more of the PSF tail. But it admits more shot noise, and it can include
   the PSF of a neighbor. A smaller disk clips real signal and lowers both channels.
9. The gap between the disk and this radius is a dead zone on purpose. It keeps the PSF tail out of
   the background estimate. A value near `disk_radius` lets signal leak into the background, and
   biases the integrated intensity down.
10. A larger radius averages the background over more pixels, which gives a quieter estimate. But
    it reaches further into neighboring molecules, and it forces a larger `window`.
11. A longer average gives a smoother background, but lags a real background change, for example a
    slow lamp drift. `1` gives the instantaneous ring mean of each frame. It is noisier and puts
    that noise into every trace.

**Fixed detector values.** `ExtractOptions` has no field for these detector values. They are the
faithful Deep-LASI values:

- the à trous scale count (`n_scales`) and scale pair,
- the MAD multiplier (`k`),
- the band-pass `lnoise` and `lobject` lengths (`lobject` is 7 under `intensity` and 9 under
  `bandpass`),
- the `fine_threshold` of the intensity and zmw modes,
- the `border_margin`,
- the sub-pixel refinement switch (`refine`).

No CLI flag, batch run or GUI control can change them. They remain keyword arguments with those
defaults on `detect_spots`, `detect_spots_intensity`, `detect_spots_bandpass` and
`detect_spots_zmw` in `tether.imaging.detect`. But a value that you set on a direct call is not what
the pipeline runs, and Tether does **not** record it in `/settings/extraction`.

Molecule-key quantization (`MOLECULE_KEY_QUANTUM_PX` in `tether.imaging.extract`) is also fixed.
Tether records it as `molecule_key_quantum_px` in `/settings/extraction`, because a change to it
would break the identity of a molecule across files.

## Registration and colocalization

> **Choose `donor-anchored` colocalization for weak-acceptor and low-FRET samples.** The default
> `partner-in-both` rule **drops the low-FRET and acceptor-dark molecules**. Their acceptor is too
> weak to be detected as a spot of its own. A FRET histogram of such a sample is then biased toward
> high E. Before you run, `tether extract --count-only` prints how many molecules each rule keeps.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `prealign`<br>`tether.project.extract` | `translation` | enum `translation`/`similarity` | Degrees of freedom of the coarse pre-alignment before Tether pairs the control points (note 1). | `tether extract --prealign` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| `prealign_upsample`<br>`tether.imaging.register` | `10` | reciprocal pixels (the shift is resolved to `1/upsample` pixel) | Sub-pixel resolution of the phase-correlation shift (note 2). | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(prealign_upsample=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `prealign_low_sigma`<br>`tether.imaging.register` | `1.0` | pixels (Gaussian standard deviation) | Low cut of the difference-of-Gaussians band-pass applied before the log-polar pass (note 3). **Used only when `prealign` is `similarity`, so inert under the default.** | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(prealign_low_sigma=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `prealign_high_sigma`<br>`tether.imaging.register` | `20.0` | pixels (Gaussian standard deviation) | High cut of the same band-pass, which must be strictly greater than the low cut (note 4). **Inert under the default `translation` prealign.** | a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`), or `ExtractOptions(prealign_high_sigma=…)` from Python — no flag of its own, no GUI control | `/settings/extraction` |
| `pair_tol`<br>`tether.imaging.register` | `2.0` | pixels (Euclidean, inclusive) | Mutual nearest-neighbor gate for accepting a control-point pair (note 5). | `tether extract --pair-tol` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| `rms_gate`<br>`tether.imaging.calibrate` | `0.5` | pixels | Residual gate on the fitted channel map: a fit whose RMS residual is strictly above it is flagged low-confidence, **not** rejected (note 6). | `tether extract --rms-gate` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| `min_bead_spots`<br>`DEFAULT_MIN_BEAD_SPOTS` in `tether.imaging.beadmap` | `6` | bead spots in **each** channel half (a count) | The least number of bead spots that each half of a bead video or bead image must have before Tether makes a channel map (note 7). | `bead_map(min_bead_spots=…)` — no CLI flag, no GUI control | `/calibration/<id>`, in the map's provenance (`bead_settings`), when the map is written into the project with `write_calibration`; the same provenance is in a map file from `save_map` |
| over-gate policy<br>`tether.project.batch` | `warn` | enum `warn`/`fail` | What a batch run does with an over-gate movie: `warn` keeps it, flagged, and `fail` fails the stage of that movie (note 8). | `tether batch --policy` | `/settings/batch` (`policy`) |
| `coloc_distance`<br>`tether.imaging.coloc` | `3.0` | pixels (strict `<`) | Nearest-neighbor radius of the `acceptor_detected` test, which is Deep-LASI's `findColoc` gate (note 9). | `tether extract --coloc-distance` or a [profile](../cli.md#extraction-profiles) (`tether extract --profile`, `tether batch --profile`) | `/settings/extraction` |
| <a id="param-colocalization"></a>`colocalization`<br>`tether.project.extract` | `partner-in-both` | enum `partner-in-both`/`donor-anchored` | Which molecules an extraction keeps, as ADR-0127 sets out (note 10). Read the warning above the table. | `tether extract --colocalization`, `tether batch --colocalization` or a [profile](../cli.md#extraction-profiles) | `/settings/extraction` (`colocalization` in the profile of each movie); each molecule's flag in `/molecules/acceptor_detected` |

**Notes**

1. The default resolves translation only. `similarity` adds rotation and isotropic scale through a
   Fourier-Mellin log-polar pass. It is reliable only near identity: rotations up to a few degrees,
   and scales within about 2 % of unity (ADR-0089). Use it when your two channels are really
   rotated. It costs time and can mislead on large warps. The prealign never enters the fitted map.
   It only helps the pairing step find partners.
2. A larger value refines the prealign at a compute cost. `1` gives whole-pixel alignment only,
   which can leave true partners outside `pair_tol`.
3. A larger value strips fine structure that the rotation and scale estimate needs. At `3.0`, the
   default before ADR-0089, Tether does not find a 1° rotation and a 1 % scale.
4. A larger value lets saturated background into the FFT magnitude, and destabilizes the estimate.
5. A larger gate pairs more points, including spurious ones, which drag the fitted map. A smaller
   gate can leave fewer than the two pairs that extraction requires, and the movie then fails.
6. Tether tags every molecule that a flagged calibration extracts. A marginal registration is
   therefore visible downstream, and is not silently dropped. A larger gate hides a bad map. A
   smaller gate flags more runs for review.
7. Under the limit, Tether refuses the source with both counts and the limit, and fits no map. Six
   is the number of coefficients of one output of the degree-2 map. Fewer spots therefore cannot
   give the pairs that the fit needs. A larger limit refuses sparse bead slides earlier. A smaller
   limit lets Tether fit a map from so few beads that the result is the similarity fallback or a
   poorly constrained polynomial. Tether first reduces a video to one image over the frame range
   that you choose, with a `max` (default) or `mean` projection.
8. A failed movie does not enter the pooled analysis. `tether extract` on a single movie always
   warns. Only the batch runner can fail a movie.
9. An acceptor spot within this radius of the mapped position of a donor, warped into donor
   coordinates, is the partner of that donor. A separation of exactly the threshold is **not** a
   match. Under the default `partner-in-both` rule, the radius decides which molecules exist. A
   larger radius keeps more molecules, with more chance pairings with the acceptor of a neighbor.
   A smaller radius keeps fewer. Under `donor-anchored`, the radius sets only the stored flag, and
   adds, removes or re-pairs no molecule. Under both rules, Tether reads the acceptor at the
   *mapped* position, never at the matched spot.
10. `partner-in-both` is the rule of Deep-LASI. It keeps a donor spot only when Tether detected an
    acceptor spot within `coloc_distance` of the mapped position of the donor. `donor-anchored`
    keeps every in-frame donor spot. Projects extracted before this option existed were
    donor-anchored, and Tether reads them as such.

**Fixed values.** The polynomial map degree and the fallback ladder are fixed. Six or more control
points fit a degree-2 polynomial. Two to five control points fall back to a similarity with four
degrees of freedom. Fewer than two is an error. These thresholds are constants in
`tether.imaging.calibrate`, with no user surface.

## Photobleaching and the analysis window

Tether detects photobleaching in each channel with a Bayesian single-step change-point model. The
default analysis window runs from the start of the trace to the first bleach of the **summed**
donor + acceptor intensity. An analysis window that you narrowed by hand is a manual override. A
later detection pass never overwrites it.

> **Write down each photobleach setting that you change, or leave all of them at the defaults.**
> **Photobleach detection records none of its own inputs.** `tether.project.photobleach` writes the
> *results*, `bleach_frames` and `analysis_window`, into `/molecules`. But it writes no attributes
> at all: not the priors that produced the results, and not the trace layer that they were detected
> on. The layer choice travels only in the transient `PhotobleachSummary` that the call returns.
>
> A change to `a`, `b`, `beta`, `mu` or `intensity_quantity` is therefore **not recoverable from
> the project file**. A reader of your `.tether` cannot tell a default run from a run with other
> priors, or a `corrected` pass from a `raw` one. Every other correction stage stamps its own
> settings, but this stage does not.

> **Lower `mu` if your traces are dim.** `mu` is an absolute intensity. A signal level far below
> `mu` can make the detector call the whole trace bleached at frame 0, at the default `beta`
> (note 5).

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `intensity_quantity`<br>`tether.project.photobleach` | `corrected` | enum `corrected`/`raw` | Which trace layer the detector runs on (note 1). | `compute_photobleach(intensity_quantity=…)` — no CLI flag, no GUI control | **no** |
| `a`<br>`PB_PRIOR_A` in `tether.fret.photobleach` | `1.0` | dimensionless | Shape of the inverse-Gamma prior over the noise variance of each segment (note 2). | `compute_photobleach(a=…)` / `detect_photobleach(a=…)` — no CLI flag, no GUI control | **no** |
| `b`<br>`PB_PRIOR_B` in `tether.fret.photobleach` | `1.0` | intensity² (same units as the trace, squared) | Scale of the same inverse-Gamma prior, which is in effect an assumed noise floor (note 3). | `compute_photobleach(b=…)` / `detect_photobleach(b=…)` — no CLI flag, no GUI control | **no** |
| `beta`<br>`PB_PRIOR_BETA` in `tether.fret.photobleach` | `1.0` | dimensionless (pseudo-observations) | Precision of the Normal prior over the pre-bleach signal level (note 4). | `compute_photobleach(beta=…)` / `detect_photobleach(beta=…)` — no CLI flag, no GUI control | **no** |
| `mu`<br>`PB_PRIOR_MU` in `tether.fret.photobleach` | `1000.0` | raw detector counts (ADU) of the background-subtracted trace | Prior mean of the pre-bleach signal level, as an absolute intensity, not a normalized one (note 5). Read the warning above the table. | `compute_photobleach(mu=…)` / `detect_photobleach(mu=…)` — no CLI flag, no GUI control | **no** |
| analysis window override | auto (start → first bleach of the summed intensity) | frames | The analysis window that the **windowed readers** slice each trace to. They fall back to `frame_range` when it is unset (note 6). | the curation keys in note 6; `apply_reconcile(accept_windows=…)` — the standalone-tMAVEN return leg, surfaced as the GUI reconcile dialog — or `import_analysis_only_project` from a legacy SMD; auto-set by `compute_photobleach` only where the analysis window is still at the extraction default | `/molecules` (`analysis_window`, `bleach_frames`) |

**Notes**

1. The model treats the post-bleach segment as mean-zero, which is true only after Tether removes
   the background pedestal. Detection tolerates a modest pedestal. But when the pedestal grows to
   about twice the bleach step, the trace stops looking bleached. The analysis window then silently
   spans the whole movie. A run on `raw` traces has this risk.
2. At the default, the prior is weak, and the data dominate on any trace longer than a few frames.
   A larger value makes the detector more conservative about calling a small step.
3. `b` enters only as an additive term beside the summed squared deviations of the trace. It has an
   effect only when it is comparable with that sum. On a 200-frame trace with a 1000-count step, it
   is inert from `1e-9` up to about `1e6`, and suppresses the step only near `1e8`. A value below
   the default has no practical effect, because the data term dominates.
4. At `1.0`, the prior adds about one pseudo-observation at `mu`. It therefore moves a fit on a
   trace of tens of frames very little. A larger value pulls the fitted pre-bleach level toward
   `mu`, and biases the change point on short or dim traces.
5. The asymmetry matters:
    - A level far *above* `mu` is harmless.
    - A level far *below* `mu` is the dangerous case. It has its effect at the **default** `beta`,
      with no larger `beta`, because the evidence penalty of the prior does not shrink as the trace
      gets longer.
    - Measured on a 200-frame synthetic trace that bleaches at frame 100, levels from 1e6 down to 100
      counts all recover frame 100. A level of 10 counts collapses to the all-bleached hypothesis at
      frame 0.
    - `compute_photobleach` then reads the trace as dark. `bleach_frames` records the start frame,
      and the analysis window stays at full extent on purpose. Downstream, a zero-length analysis
      window looks the same as "unset".
    - Set `mu` to your own single-molecule level to restore detection.
6. The analysis window:
    - The windowed readers each cut a trace to the analysis window. They are the analysis reader
      behind the FRET histograms and cloud, the anticorrelation and cross-correlation scan, the
      engineered features and exports of each molecule, and idealization.
    - **The correction-factor stages do not read it.** `compute_leakage_alpha`, `compute_gamma` and
      `compute_corrected_fret` slice each trace by its native `frame_range`. They find the trace
      tails from the stored `bleach_frames`. A narrower analysis window therefore does not change α,
      γ or the background-subtracted traces.
    - Manual bounds win, so a new detection run does not restore the automatic window.
    - In the curation view, `-` and `=` move the start by one frame, and `[` and `]` move the end.
      `R` restores `frame_range`, and `P` ends the window at the photobleach of the summed
      intensity. Each key writes through `Project.set_analysis_window` or `reset_analysis_window`
      ([Curate traces](../how-to/curate-traces.md#4-set-a-category-and-the-analysis-window)).

## Correction factors

Tether estimates leakage α and the detection-correction factor γ for each trace, and then pools
them to a dataset median. Both estimators are conservative on purpose. A trace that does not
qualify contributes nothing. If too few traces qualify, Tether **withholds** the factor and does not
guess it. Corrections are never required. A project with no usable factors falls back to apparent
E, and does not produce NaNs.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `intensity_quantity` (α and γ)<br>`tether.project.leakage` / `tether.project.gamma` | `corrected` | enum `corrected`/`raw` | Which `/traces` layer Tether measures the per-trace α and γ from (note 1). | `compute_leakage_alpha(intensity_quantity=…)`, `compute_gamma(intensity_quantity=…)` — no CLI flag, no GUI control | `/settings/leakage`, `/settings/gamma` |
| `min_window_frames` (α)<br>`DEFAULT_MIN_WINDOW_FRAMES` in `tether.fret.leakage` | `20` | frames | Shortest tail after the acceptor bleach from which α can be measured: a tail of exactly this length qualifies (note 2). | `compute_leakage_alpha(min_window_frames=…)` — no CLI flag, no GUI control | `/settings/leakage` |
| `ceiling` (α)<br>`LEAKAGE_CEILING` in `tether.fret.leakage` | `0.3` | dimensionless | Tether rejects a per-trace α outside the inclusive range from zero to the ceiling as unphysical (note 3). | `compute_leakage_alpha(ceiling=…)` — no CLI flag, no GUI control | `/settings/leakage` |
| `min_qualifying_traces` (α)<br>`DEFAULT_MIN_QUALIFYING_TRACES` in `tether.fret.leakage` | `10` | molecules | How many traces must give an α before Tether trusts the dataset median: exactly this many is enough (note 4). | `compute_leakage_alpha(min_qualifying_traces=…)` — no CLI flag, no GUI control | `/settings/leakage` |
| `half_window` (γ)<br>`DEFAULT_GAMMA_HALF_WINDOW` in `tether.fret.gamma` | `20` | frames each side of the acceptor-bleach step | How many frames Tether averages on each side of the step to measure the acceptor drop and the donor rise (note 5). | `compute_gamma(half_window=…)` — no CLI flag, no GUI control | `/settings/gamma` (`half_window` is `0` and `level_window` is `whole-segment` when the whole segments were used) |
| `min_window_frames` (γ)<br>`DEFAULT_MIN_WINDOW_FRAMES` in `tether.fret.leakage` | `20` | frames | Both the pre-step FRET segment and the post-step donor-only segment must be **strictly longer** than this (note 6). | `compute_gamma(min_window_frames=…)` — no CLI flag, no GUI control | `/settings/gamma` |
| `ceiling` (γ)<br>`GAMMA_CEILING` in `tether.fret.gamma` | `5.0` | dimensionless | Tether rejects a per-trace γ outside the range above zero up to and including the ceiling (note 7). | `compute_gamma(ceiling=…)` — no CLI flag, no GUI control | `/settings/gamma` |
| `min_qualifying_traces` (γ)<br>`DEFAULT_MIN_QUALIFYING_TRACES` in `tether.fret.leakage` | `10` | molecules | Same contract as for α: below the limit, Tether withholds γ. | `compute_gamma(min_qualifying_traces=…)` — no CLI flag, no GUI control | `/settings/gamma` |
| `min_clean_step_fraction` (γ)<br>`DEFAULT_MIN_CLEAN_STEP_FRACTION` in `tether.fret.gamma` | `0.65` | fraction of the measured steps | When the clean share of the measured acceptor-bleach steps is below this fraction, Tether flags the applied γ and does not change it (note 8). | `compute_gamma(min_clean_step_fraction=…)` — no CLI flag, no GUI control | `/settings/gamma` (`n_step_evaluated`, `n_step_clean`, `min_clean_step_fraction`, `few_clean_steps`) |
| <a id="param-alpha-gamma-override"></a>`alpha_override` / `gamma_override`<br>`tether.project.correct` | `None` | dimensionless | A factor measured elsewhere, for example on a donor-only or acceptor-only sample, that replaces the dataset median for every molecule (note 9). | `compute_corrected_fret(alpha_override=…, gamma_override=…)` — no CLI flag, no GUI control | `/settings/correction` |
| `apparent_e_only`<br>`tether.project.correct` | `False` | boolean | Forces apparent E even when factors are available. Use it to put the uncorrected quantity on record when you distrust the corrections (note 10). | `compute_corrected_fret(apparent_e_only=True)` — no CLI flag, no GUI control | `/settings/correction` |

**Notes**

1. `corrected` is the background-subtracted layer, and `raw` is the layer before background
   subtraction. On `raw`, the background stays in the means of the post-bleach tails, and in the
   heights of the acceptor-drop and donor-rise steps. The choice therefore changes the factors
   themselves, and every corrected E computed from them. Unlike the analysis-side argument of the
   same name, this choice **is** stamped. A reader of your project can tell which layer a factor
   came from.
2. A larger value keeps only long, well-averaged tails. Fewer traces then contribute, and the median
   gets noisier if you go too far. A smaller value admits short tails whose mean is mostly shot
   noise.
3. A larger ceiling admits traces whose "leakage" is really a mis-detected bleach or a contaminating
   acceptor. A smaller ceiling discards real high-leakage dye pairs, and can put the dataset below
   the qualifying count.
4. Below the limit, Tether withholds the factor and the project falls back to apparent E. This is a
   deliberate refusal, not a failure. A smaller value lets a median from a few traces correct a
   whole condition.
5. [ADR-0130](../adr/0130-the-gamma-levels-are-averaged-over-20-frames-each-side.md) set the value,
   which was `3` before it. A smaller value makes γ depend on frame-to-frame noise, which ADR-0130
   measured against Deep-LASI. `None` averages the whole segment on each side, as Deep-LASI does. A
   larger value or `None` lets a donor that dims slowly over a long segment hide the donor rise, so
   fewer molecules qualify. The window after the step never reaches past the donor bleach.
6. This gate is exclusive, but the α gate is inclusive. A larger value demands longer stable
   segments, and gives fewer, cleaner γ values.
7. A larger ceiling admits extreme ratios. These usually mean that the "step" was not a true
   acceptor bleach before the donor bleach. A smaller ceiling discards real bright-acceptor dye
   pairs.
8. The clean-step fraction:
    - Among the molecules whose acceptor-bleach step was measured, a step is clean when the donor
      rose and the leakage-corrected acceptor dropped.
    - When an applied γ rests on fewer clean steps than this fraction, the correct stage of the
      batch log says so and is flagged. `/settings/gamma` records `few_clean_steps`. No γ value
      changes.
    - The flag is a reason to check γ, not proof that it is wrong. Such a γ can be the median of the
      positive tail of noise that the ceiling gate keeps. Or it can be sound, because a step whose
      donor did not rise never enters the median.
    - `0` turns the warning off.
    - The [Background](#why-the-clean-step-fraction-is-above-one-half) gives the reason for `0.65`.
9. Tether stamps an override as such. The molecules that it corrects are labeled `manual`, not
   `corrected`.
10. The stamp tells a user choice apart from unavailable corrections.

### Leakage is one value for the whole project

α is **project-wide** in Tether. `compute_leakage_alpha` fits one dataset median over every
analyzable molecule in the `.tether`. It does **not** partition by
[condition](../glossary.md#condition). It writes that single value into every one of their
`/molecules.alpha` cells, which is the field `tether.project.correct` reads back as the applied
factor. The provenance of the estimator goes on `/settings/leakage`.

`/conditions.leakage_alpha` also exists in the schema (`tether.io.schema`), but it is a
**curator-editable field**. Its only writer is `register_condition(..., leakage_alpha=...)`, the
idempotent upsert in `tether.project.conditions`. There is no separate `update_condition`. The
same call inserts a missing row, and updates the provided fields of an existing one. Nothing in
the correction chain reads that field, and the estimator never writes it.

ADR-0027 describes a move of the applied α there for each condition, but files it under
*Deferred*. The move has not happened, so read the "α is per-condition" of that ADR as intent, not
as current behavior.

### How Tether estimates gamma

γ uses the same 20-frame window and 10-trace minimum as α, but its frame gate is **stricter and
two-sided**. Both the pre-step FRET segment `[0, step)` and the post-step donor-only segment
`[step, donor_bleach)` must be **strictly longer than** 20 frames (`> 20`). The α tail only
needs `≥ 20`. Tether drops a trace that fails as `short-pre` or `short-post`.

γ is **per-molecule with a population-median fallback**. A qualifying molecule keeps its own γ,
and a non-qualifying one silently takes the dataset median. Tether counts those in
`/settings/gamma.attrs["n_fallback"]`. No marker on a row tells the two apart. A molecule with
the median γ is still stamped `corrected`, with confidence `1.0`.

## Idealization

Idealization runs in an isolated sidecar interpreter. The default model is a variational-Bayes
**consensus** HMM (`vbconhmm`, where `con` stands for *consensus*). It is one global HMM fitted
across all the molecules of the project, not a model for each trace. Tether chooses the state count
automatically: it maximizes the evidence lower bound over a small grid.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| <a id="param-model-type"></a>`model_type`<br>`MODEL_TYPE_DEFAULT` in `tether.project.idealize` | `vbconhmm` | enum | Which fitter the sidecar runs (note 1). | `tether idealize --method` (`vbconhmm` and `ebhmm` only); `idealize_molecules(model_type=…)` (all sidecar keys); the GUI one-click idealize passes whatever it was constructed with | `/idealization/<model>` (`type`) |
| <a id="param-idealize-nstates"></a>`nstates`<br>`tether.project.idealize` | `None` (automatic) | count | Unset, Tether selects the state count by maximum ELBO over the grid. Set, it forces K (note 2). | `tether idealize --nstates`; `idealize_molecules(nstates=…)` | `/idealization/<model>` (`nstates`, `nstates_selected_by`) |
| `nstates_grid`<br>`NSTATES_GRID_DEFAULT` in `tether.project.idealize` | `(1, 2, 3, 4)` | counts | The candidate state counts that the automatic selection searches (note 3). | `tether idealize --nstates-grid`; `idealize_molecules(nstates_grid=…)` | `/idealization/<model>` (`elbo_by_nstates`, written when the grid was searched) |
| `intensity_quantity`<br>`tether.project.idealize` | `corrected` | enum `corrected`/`raw` | Which trace layer is idealized. The state means of a fit on `raw` traces are not comparable to a run on the `corrected` layer. | `idealize_molecules(intensity_quantity=…)` — no CLI flag | `/idealization/<model>` (`intensity_quantity`) |
| `nrestarts`<br>`tether.project.idealize` | `None` (tMAVEN's own default) | restarts per fit | Overrides tMAVEN's `modeler.nrestarts` preference: how many restarts the variational fitter runs before it keeps the best model (note 4). | `idealize_molecules(nrestarts=…)` — no CLI flag | **no** |
| <a id="param-sidecar-timeout"></a>`timeout`<br>`DEFAULT_SIDECAR_TIMEOUT` in `tether.idealize.supervisor` | `1800.0` | seconds per idealization call | How long one fit can run before Tether kills the sidecar. `None` waits forever (note 5). | `tether batch --sidecar-timeout`; `tether idealize --sidecar-timeout`; `idealize_molecules(timeout=…)` | **no** |
| `max_restarts`<br>`DEFAULT_MAX_RESTARTS` in `tether.idealize.supervisor` | `3` | restarts per movie | How many times Tether retries a *transient* sidecar failure, which is a crash or a timeout (note 6). | `tether batch --max-restarts`; `tether idealize --max-restarts` | **no** |
| `probe_timeout`<br>`DEFAULT_PROBE_TIMEOUT` in `tether.idealize.supervisor` | `120.0` | seconds | Time limit of the one-shot startup probe that decides whether the sidecar environment works at all. If it is too short on a cold file system, Tether declares a healthy environment unavailable. | `SidecarSupervision(probe_timeout=…)` — no CLI flag | **no** |
| `defer_if_unavailable`<br>`tether.idealize.supervisor` | `True` | boolean | With the default, a missing sidecar defers idealization for the whole run, instead of failing every movie one at a time. Turn it off to make the idealization stage of each movie fail on its own. | `tether batch --no-defer` | **no** |

**Notes**

1. The two documented models:
    - Both documented models are population fits, so "pools across traces" is not what separates
      them.
    - The ebFRET alternative (`ebhmm`) adds an empirical-Bayes hierarchy. It learns a shared prior
      across the molecules, and keeps posteriors and state selection for each trace. This makes it
      more seed-variable, and it is why `ebhmm` is ratified against its own, looser parity
      tolerance (see [Tolerances and gates](#tolerances-and-gates)).
    - It is a different statistical object, not a tuning setting.
    - The sidecar dispatch accepts five more keys that this page does not document:
      `vbconhmm_modelselection`, `ebhmm_modelselection`, `vbgmm_vbhmm`, `kmeans_vbhmm` and
      `vbgmm`. [Citing Tether](../citing.md#idealization) says what each one fits.
2. A forced K that is larger than the data support splits one state into two noise-separated
   states, and inflates your transition counts. A forced K that is too small merges states and
   hides transitions.
3. A wider grid lets the ELBO pick a larger K. It costs one fit for each candidate, so the run time
   scales with the grid. A tie goes to the smaller K.
4. Fewer restarts are faster, and the documented use is to speed up tests. But they also make a
   local optimum more likely. **Do not confuse `nrestarts` with `max_restarts`.** `max_restarts`
   retries a *crashed* sidecar and has no effect on the fit. Tether forwards `nrestarts` to the
   sidecar, but never stamps it on the model group.
5. Just-in-time compilation takes most of the time of the first fit of a session, which is
   therefore much slower than the rest. A short timeout kills cold runs that would have succeeded.
6. **Do not confuse `max_restarts` with `nrestarts`**, which is a property of the fit itself.
   Tether never retries a fit error that the sidecar itself reports. `0` disables restarts, so one
   transient crash fails that movie. A larger value can turn a systematically broken environment
   into a very long batch run.

## Analysis and histograms

### Science tunables

These change a reported number. The one exception is the dwell `state` selector, marked in its
row. It chooses which state's analysis comes back, and does not change the fit of any state.

> **Pass the frame interval as `dt` or `time_dt`.** At the default of `1.0`, every dwell time is
> in frames and every fitted rate is per frame, not per second. **This is the one default that
> silently gives a published number the wrong unit.** `/movies` carries the frame interval of each
> movie as `frame_time` (zero when the source movie declared none). But no analysis function reads
> it automatically, so you must pass it.

> **Do not report an E from these functions as absolute E.** Every function that takes
> `intensity_quantity` computes **apparent** E from the layer that it is handed. Nothing under
> `tether.analysis` reads `/molecules.alpha` or `/molecules.gamma`. Neither setting therefore gives
> an efficiency corrected for leakage and γ.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `intensity_quantity`<br>the intensity-layer `tether.analysis` population functions | `corrected` | enum `corrected`/`raw` | Which trace layer a population statistic is computed on, and the largest single change in a reported E in this table (note 1). | `population_apparent_e_histogram(intensity_quantity=…)`, the same argument on the other intensity-layer population functions listed above — no CLI flag, no GUI control | **no** |
| `state`<br>`tether.analysis.dwell` | `None` | state index | Which state's dwell-time analysis comes back. **A selector, not a tunable:** it changes no number (note 2). | `population_dwell_times(state=…)` — no CLI flag, no GUI control | **no** |
| `include_first`<br>`tether.analysis.dwell`, `tether.analysis.kinetics` | `False` | boolean | Whether Tether keeps the first run of each molecule, although its start was not observed (note 3). | `population_dwell_times(include_first=True)`, `pooled_exit_rates(include_first=True)`, and `state_dwells(include_first=True)` directly on the extraction core — no CLI flag, no GUI control | **no** |
| <a id="param-n-resamples"></a>`n_resamples`<br>`DEFAULT_BOOTSTRAP_RESAMPLES` in `tether.analysis.histogram` | `1000` | resamples | How many molecule-level bootstrap replicates build the confidence band on a population FRET histogram (note 4). | `bootstrap_histogram_ci(n_resamples=…)` — no CLI flag, no GUI control | **no** (analysis results are computed on demand, not stored) |
| <a id="param-bootstrap-ci-level"></a>`ci_level`<br>`DEFAULT_CI_LEVEL` in `tether.analysis.histogram` | `0.95` | probability | Coverage of the percentile interval: the default takes the 2.5th and 97.5th percentiles of the replicate distribution. A larger value widens the band. | `bootstrap_histogram_ci(ci_level=…)` — no CLI flag, no GUI control | **no** |
| <a id="param-bootstrap-seed"></a>`seed`<br>`DEFAULT_SEED` in `tether.analysis.histogram` | `0` | seed | Fixes the resampling, so that the band is the same from run to run (note 5). | `bootstrap_histogram_ci(seed=…)` — no CLI flag, no GUI control | **no** |
| `per_molecule_equal_weight`<br>`tether.analysis.histogram` | `False` | boolean | With the default, every *frame* contributes equally, so a long trace counts for more than a short one. Turn it on to give every *molecule* equal weight when trace lengths differ systematically between conditions. | `population_apparent_e_histogram(per_molecule_equal_weight=True)` — no CLI flag, no GUI control yet | **no** |
| `include_rejected`<br>`tether.analysis` population functions | `False` | boolean | Whether curator-rejected molecules enter a population statistic. Turn it on only to audit the effect of curation, not to report a result. | `population_apparent_e_histogram(include_rejected=True)` and the equivalent argument on the other population functions — no CLI flag, no GUI control yet | **no** |
| `include_stale`<br>the model-derived `tether.analysis` population functions | `False` | boolean | Whether molecules whose idealization is **stale** enter a statistic that is derived from the model (note 6). | `population_transition_density(include_stale=True)`, and the same argument on `population_dwell_times`, `population_transition_prob_histogram`, `population_state_number`, `population_transition_sync_histogram2d` and `population_model_gaussian_overlay` — no CLI flag, no GUI control yet | **no** |
| <a id="param-dt"></a>`dt` / `time_dt`<br>`DEFAULT_DWELL_DT` in `tether.analysis.dwell`, `DEFAULT_TIME_DT` in `tether.analysis.histogram`, `DEFAULT_CLOUD_TIME_DT` in `tether.analysis.cloud` | `1.0` | seconds per frame | Converts frames to physical time. Read the warning above the table. | `population_dwell_times(dt=…)`, `time_signal_histogram2d(time_dt=…)`, `raw_fret_cloud(time_dt=…)` — no CLI flag, no GUI control yet | **no** |
| <a id="param-dwell-model"></a>`model` / `ci_level` (dwell fits)<br>`DEFAULT_DWELL_CI_LEVEL` in `tether.analysis.dwell` | `single`, `0.95` | model name, probability | Which survival form is fitted to the dwell-time distribution, and the coverage of the reported parameter intervals (note 7). | `population_dwell_times(model=…, ci_level=…)` — no CLI flag, no GUI control yet | **no** |
| `nstates`<br>`tether.analysis.kinetics` | `2` | count | How many hidden states the shared-parameter Gaussian HMM for rate-constant estimation has (note 8). | `fit_gaussian_hmm(traces, nstates=…)` — no CLI flag, no GUI control yet. The convenience wrapper `two_state_rate_constants` hardcodes two states and takes no `nstates` argument, so a non-two-state model means calling `fit_gaussian_hmm` / `viterbi_paths` / `pooled_exit_rates` yourself. | **no** |
| `n_iter` / `tol`<br>`DEFAULT_HMM_MAX_ITER`, `DEFAULT_HMM_TOL` in `tether.analysis.kinetics` | `200`, `1e-6` | iterations, relative log-likelihood change | Convergence budget of the Gaussian HMM for rate-constant estimation: `n_iter` is an iteration cap, and `tol` is a stopping threshold (note 9). | `fit_gaussian_hmm(n_iter=…, tol=…)` — no CLI flag, no GUI control | **no** |
| `var_floor`<br>`DEFAULT_HMM_VAR_FLOOR` in `tether.analysis.kinetics` | `1e-6` | apparent-E units squared (a variance) | Lower bound that Tether clamps onto the fitted emission variance of every state, at each M-step of the same Gaussian HMM (note 10). | `fit_gaussian_hmm(var_floor=…)` — no CLI flag, no GUI control | **no** |
| successive-frame FRET cloud<br>`DEFAULT_SUCCESSIVE_GRID_POINTS`, `DEFAULT_SUCCESSIVE_RANGE`, `DEFAULT_SUCCESSIVE_BANDWIDTH`, `DEFAULT_SUCCESSIVE_HDR_COVERAGES`, `DEFAULT_SUCCESSIVE_N_CENTROIDS`, `DEFAULT_SUCCESSIVE_KMEANS_RESTARTS`, `DEFAULT_SUCCESSIVE_KMEANS_MAX_ITER`, `DEFAULT_SUCCESSIVE_SEED` in `tether.analysis.successive` | `200`, `(0.0, 1.0)`, `0.05`, `(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)`, `4`, `5`, `100`, `0` | grid nodes per axis, apparent-E units, apparent-E units, coverage fractions, centroids, starts, iterations, seed | The plot of apparent E at frame *i* against apparent E at frame *i* + 1, with the values of the laboratory's FRET_cloud scripts (note 11). | `successive_frame_cloud(grid_points=…, value_range=…, bandwidth=…, hdr_coverages=…, n_centroids=…, kmeans_restarts=…, kmeans_max_iter=…, seed=…)`, `population_successive_frame_cloud(…)` — no CLI flag, no GUI control | **no** — but the returned `provenance` holds every value that made the result |

**Notes**

1. The trace layer of a population statistic:
    - `corrected` selects the background-subtracted layer (`/traces/donor_corrected` and
      `/traces/acceptor_corrected`). `raw` selects the layer before background subtraction. Its E
      values are not comparable to a `corrected` run.
    - The layer name is all that `corrected` means here. Read the warning above the table.
    - Only the views that read a trace layer take the argument. They are the 1-D histograms (with
      the CI band and the per-condition overlay), the 2-D and post-synchronized heatmaps, the cloud
      views, the cross-correlation and the anticorrelation finder.
    - The four views derived from state paths have no `intensity_quantity` argument at all:
      `population_dwell_times`, `population_state_number`, `population_transition_density` and
      `population_transition_prob_histogram`. Their numbers come from the stored state paths and
      model means, not from a trace layer.
    - `compute_leakage_alpha` and `compute_gamma` have the same argument, with the same default.
      There it decides which layer α and γ are measured from, and Tether *does* record it. See
      [Correction factors](#correction-factors).
2. The dwell `state` selector:
    - Tether builds the survival curve and fit of every state in the same way, whichever state you
      ask for. The selector changes which keys the returned mapping holds, and nothing inside them.
    - The return is always a `dict[int, DwellTimeAnalysis]`
      ([ADR-0109](../adr/0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md)).
    - At the default, the mapping is keyed by ascending state. It holds every state that
      contributed **at least one retained dwell**. That is not the same as every state that the
      molecules occupied. A state has no key when every run in it was the last of its molecule, or
      when its molecules never transitioned. **Do not read absence as "never visited."**
    - `include_first` decides what counts as retained. At its default, the retained set is the
      interior runs only. When you turn it on, it also admits the first run of each molecule, so a
      state can gain a key that it did not have.
    - When you pass an integer, the mapping holds that one key, `result[state]`. A state that
      contributed no retained dwell then holds an analysis with `n_dwells = 0` and `fit = None`,
      not a missing key. Its `tau` and `survival` are the degenerate single-element `[0]` and
      `[0.0]`, not empty arrays. Test `n_dwells`, not `tau.size`.
    - Code against this difference. At the default, such a state is absent from the mapping. A
      named state is always present, also an out-of-range or negative index, which comes back with
      `level` as `NaN`.
3. The first run of each molecule:
    - The molecule was already in that state when the analysis window opened.
    - Tether drops the **last** run of every molecule unconditionally, so this switch moves only the
      left edge. A molecule that never transitions contributes nothing at either setting.
    - Both entry points pass the slicing to the same public extraction core, `state_dwells`, which
      takes the same keyword.
    - **The effect on the reported rate is direct.** `pooled_exit_rates` takes `k = 1/⟨τ⟩` over the
      pooled dwells, which is the estimator that the kinSoft challenge benchmark uses. It pools a
      mean and does not fit, so it has no censoring model. The switch changes which dwells are in
      the pool, and therefore changes `k`. The survival fit moves for the same reason.
    - **Do not read the default as the unbiased setting.** A finite analysis window cannot record a
      dwell longer than itself. Neither setting is corrected for truncation, and the two truncate
      the sample differently. The difference is largest when the analysis window is short relative
      to the dwell times, and it gets smaller as the window gets longer.
    - The default is the only setting that `two_state_rate_constants` can give, because that
      wrapper has no `include_first` argument.
    - Report which setting you used.
4. Fewer replicates make the band a coarser estimate of the true interval. They do **not** make the
   band change from run to run. The resampling is seeded (`seed`, two rows down), so a band is
   bit-identical on identical data at any resample count. With a different seed, a band from few
   resamples visibly moves. More replicates cost time, and give little once the band is stable.
5. Change it only to show that the band does not depend on the draw. Report the value that you
   used, because a band computed with a different seed is a different band.
6. Stale molecules:
    - A molecule is stale when the traces or corrections that its state path was fitted on changed
      after the fit. The stored path then no longer belongs to the signal under it. A re-extraction
      or a recalibration of α or γ is enough.
    - The default excludes stale molecules. That keeps a transition-density plot, dwell-time fit,
      transition-probability histogram or state-number count consistent with the traces it is drawn
      over.
    - When you turn it on, a state path is mixed with signal that it was not fitted on. To fix a
      stale cohort, use `reidealize`, not this flag.
    - Only the six population functions that read `/idealization` take it. The other views do not
      need it: the 1-D histogram, the raw time-versus-signal 2-D heatmap and the cloud views have no idealization to go stale.
    - All six apply one rule
      ([ADR-0109](../adr/0109-one-selection-argument-and-one-return-shape-on-the-population-functions.md)):
      Tether cuts the selection to the live keys of the model before it reads anything.
    - The post-synchronized A2 heatmap (`population_transition_sync_histogram2d`) pairs the stored
      Viterbi paths with the current traces. With the flag on, it can draw a stale path over signal
      that the path was not fitted on. Its own guard compares the extent of a state path with the
      width of the whole `/traces` array. That array is zero-padded to the experiment-max frame
      count, not trimmed for each molecule. The guard therefore catches neither a re-extraction that
      shortens one molecule, nor a recalibration of α or γ.
    - The A1 model-Gaussian overlay (`population_model_gaussian_overlay`) is different in kind. Its
      curves are the fitted means, variances and populations of the model, which no selection
      changes. The flag, with `molecule_keys` and `include_rejected`, therefore decides only
      **whether** Tether draws the overlay. Tether withholds it with an error when no selected,
      accepted and fresh molecule has an idealized analysis window in the model.
7. A double or stretched form in place of a single exponential changes the reported rate constants,
   not only the quality of the fit. Choose the model on physical grounds, and report which model you
   used.
8. The kinetics state count:
    - Unlike the two rows below, which govern only how the fit stops, `nstates` defines the model.
    - The initialization is deterministic and comes entirely from K. The state means start at evenly
      spaced quantiles of the pooled signal. The width of each state is the pooled standard
      deviation divided by K. The transition matrix is diagonal-dominant, with a stay probability
      of 0.95 when K > 1. The start distribution is uniform.
    - A larger K splits one state into two noise-separated states, and inflates transition counts.
      A smaller K merges states and hides transitions. This is the same trade-off as in the
      idealization `nstates` row above.
    - The emissions change, so the Viterbi paths change too, and so does every rate constant
      derived from them.
    - `nstates=1` sets the stay probability to `1.0`. That model has no transitions at all, so every
      exit rate degenerates. A value below `1` raises `ValueError`.
9. The convergence budget:
    - **The two act in opposite directions.** EM stops when the absolute change in the total
      log-likelihood between two successive iterations falls to a limit. The limit is
      `tol`·(1 + the magnitude of the previous one). The first iteration has no previous value, so
      it never stops the loop.
    - The fitted model reports which rule stopped it. `converged` is `True` only when the `tol` rule
      stopped it, and `False` when the `n_iter` cap did. Its `n_iter` is the number of iterations
      done.
    - A smaller `n_iter` *or a larger* `tol` can return an unconverged fit that still looks
      plausible. A fit that the cap stopped says so in `converged`. But a fit that a loose `tol`
      stopped reports `True`.
    - A larger `n_iter` or a smaller `tol` lets EM run more iterations. That costs time. It can also
      change the fitted parameters, and so every rate estimated from them, as the fit moves closer
      to convergence.
10. The floor stops a state that has collapsed onto identical values from giving a zero-width
    Gaussian and a division by zero. At the default, it is far below any real FRET noise level and
    never binds. The clamped width goes straight back into the next E-step. A value near your actual
    noise variance therefore does more than widen a reported σ. It changes the responsibilities,
    and so the state assignment and every rate estimated from it. Raise it only to rescue a fit that
    degenerates, and say so if you do.
11. The successive-frame cloud:
    - The values are those of the FRET_cloud scripts of the laboratory (FRET_cloud scripts, Eduardo Campos Chavez, 2025,
      unpublished). The seed and the iteration cap are Tether's.
    - The density is a Gaussian kernel sum, with the one fixed bandwidth on both axes. Tether
      evaluates it at the `grid_points` × `grid_points` nodes of the range, and normalizes it to a
      total mass of 1 on the grid.
    - A larger bandwidth merges nearby states and moves every contour. A smaller one breaks one
      state into noise.
    - The bandwidth is not estimated from the data, so Tether does not leave out a pair outside the
      range. The part of its kernel that reaches the grid counts, and `n_out_of_range` reports how
      many such pairs there are. A narrower range therefore crops the grid and moves the
      normalization, not the pairs.
    - The coverages are the contour levels that are returned.
    - The centroids are k-means on **all** the pairs, inside and outside the range. `n_centroids`,
      the starts and the seed therefore change the reported centroid positions. More starts never
      give a larger within-cluster sum of squares. Two seeds can end in two different local minima.
      On the first 51 frames of the smfret-analysis2 test traces, seeds 4, 6 and 8 of 0 to 9 end up
      to 0.003 in E from the other seven.
    - `n_centroids=None` computes no centroids. Fewer distinct pairs than centroids give fewer
      centroids.
    - A centroid shows where the cloud is dense. It is not a state count, which comes from the model
      view.

**Six rows say "no GUI control yet".** They are `per_molecule_equal_weight`, `include_rejected`,
`include_stale`, `dt` / `time_dt`, the dwell-fit `model` / `ci_level` and the kinetics `nstates`.
Until the plot builder ships, set these from Python, as the table says. The
[Background](#the-planned-plot-builder) tells what the builder is planned to do.

### Rendering defaults

These redraw a plot, and none of them changes a fitted value. Two rows have more effect than the
rest, and say so in place. The transition-sync selection chooses *which* transitions Tether draws.
The `density` switch changes the scale on which a binned view is returned.

Most rows are faithful ports of tMAVEN display defaults. The last six rows are Tether's own. The
[Background](#where-the-rendering-defaults-come-from) names the tMAVEN file for each port, and the
precedents for the rows that are not ports.

Most of these constants carry the rendering-default label in their own source comment. The 1-D
histogram bins and range and the plot-export constants do not. Some values have no constant behind
them, and are plain keyword defaults:

- the two arguments of the cross-correlation,
- the three arguments of the transition-sync selection,
- the `density` switch,
- the `kde` of the transition-probability view,
- the `time_range` of the cloud and its own `kde`,
- the `k_min` and `in_grid_only` of the elbow,
- the `alpha` and `in_grid_only` of the α-shape,
- the `states_high` of the state-number chart.

Each Parameter cell names the constant when there is one.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `bins` / `value_range`<br>`DEFAULT_NBINS`, `DEFAULT_RANGE` in `tether.analysis.histogram` | `151`, `(-0.25, 1.25)` | bin count, apparent-E units | Resolution and extent of the one-dimensional FRET histogram (note 1). | `apparent_e_histogram(bins=…, value_range=…)` — no CLI flag, no GUI control | **no** |
| `n_points`<br>`DEFAULT_OVERLAY_POINTS` in `tether.analysis.histogram` | `1001` | grid points | Resolution of the A1 model-Gaussian overlay: how finely Tether samples the drawn curve, and nothing else (note 2). | `model_gaussian_overlay(n_points=…)`, `population_model_gaussian_overlay(n_points=…)` — **Python only**: no CLI flag, no GUI control, and no `tether` subcommand reaches either | **no** — like every other analysis output, the overlay is computed on demand and neither it nor its grid is written to the project |
| `density`<br>a keyword default on the binned `tether.analysis` functions (no module constant) | `True` (one-dimensional views), `False` (two-dimensional views) | boolean | Whether a binned view is returned as a probability density or as raw (weighted) occupancy counts (note 3). | `population_apparent_e_histogram(density=…)` and the same keyword on the other binned population functions — no CLI flag, no GUI control (the GUI histogram's y-axis label does follow it: `density` versus `count`) | **no** |
| `time_bins` / `signal_bins` / `signal_range`<br>`DEFAULT_TIME_BINS`, `DEFAULT_SIGNAL_BINS`, `DEFAULT_SIGNAL_RANGE` in `tether.analysis.histogram` | `100`, `61`, `(-0.2, 1.2)` | bin counts, apparent-E units | Grid of the time-versus-signal two-dimensional histogram. It differs from the one-dimensional defaults on purpose, as in tMAVEN (note 4). | `time_signal_histogram2d(…)` — no CLI flag, no GUI control | **no** |
| `sync_preframe`<br>`DEFAULT_SYNC_PREFRAME` in `tether.analysis.histogram` | `50` | time columns before the transition | Where the selected transition sits on the post-synchronized plot, centered at half the time-bin count. Lower it to show more of the departure, or raise it to show more of the approach. | `transition_sync_histogram2d(sync_preframe=…)` — no CLI flag, no GUI control | **no** |
| `from_state` / `to_state` / `single_dwell`<br>keyword defaults on `transition_sync_histogram2d` in `tether.analysis.histogram` | `-1`, `-1`, `True` | state index, state index, boolean | Which transitions the post-synchronized heatmap is built from, and the shape of the post-synchronization window around each (note 5). | `transition_sync_histogram2d(from_state=…, to_state=…, single_dwell=…)` and the same arguments on `population_transition_sync_histogram2d` — no CLI flag, no GUI control | **no** |
| `nskip` / `signal_bins` / `signal_range`<br>`DEFAULT_TDP_NSKIP`, `DEFAULT_TDP_SIGNAL_BINS`, `DEFAULT_TDP_SIGNAL_RANGE` in `tether.analysis.tdp` | `2`, `101`, `(-0.25, 1.25)` | frames, bin count per axis, apparent-E units | Grid of the transition-density plot, and how many frames apart Tether reads the initial and final points of a transition, to skip the transition frame. | `population_transition_density(…)` — no CLI flag, no GUI control | **no** |
| smoothed transition-density view (the `smooth`, `interp_res` and normalize keywords)<br>`DEFAULT_TDP_SMOOTH`, `DEFAULT_TDP_INTERP_RES`, `DEFAULT_TDP_NORMALIZE` in `tether.analysis.tdp` | `(1.0, 1.0)`, `800`, `True` | Gaussian width in bins along the initial and final axes, spline grid points per axis, boolean | The smoothed *view* of the transition-density plot, which is the array that a published TDP figure draws (note 6). | `smooth_transition_density(…)`, drawn by `render_transition_density` — no CLI flag, no GUI control | **no** |
| `nbins` (dwell histogram)<br>`DEFAULT_DWELL_NBINS` in `tether.analysis.dwell` | `51` | bins | Bin count of the dwell-time distribution *view*. The survival fit is not binned, so this changes only the picture. | `DwellTimeAnalysis.histogram(nbins=…)` — no CLI flag, no GUI control | **no** |
| `prob_bins` / `prob_range` / `kde` / `kde_bandwidth` / `kde_points`<br>`DEFAULT_TPROB_NBINS` and `DEFAULT_TPROB_RANGE`, then `kde` (a plain keyword default with no constant behind it), then `DEFAULT_TPROB_KDE_BANDWIDTH` and `DEFAULT_TPROB_KDE_POINTS` — all in `tether.analysis.transition_prob` | `25`, `(-0.05, 1.05)`, `True`, `0.25`, `100` | bin count, probability units, boolean, dimensionless bandwidth **factor**, grid points | Histogram and kernel-density overlay of the transition probabilities of each trace (note 7). | `transition_prob_histogram(…)`, `population_transition_prob_histogram(…)` — no CLI flag, no GUI control | **no** |
| `states_low` / `states_high`<br>`DEFAULT_STATE_NUMBER_LOW` in `tether.analysis.state_number` | `1`, `None` (derived from the data) | state counts (bar-chart x-axis bounds) | Extent of the x-axis of the state-number bar chart, which counts how many *distinct* states the Viterbi path of each molecule occupies (note 8). | `population_state_number(states_low=…, states_high=…)` — no CLI flag, no GUI control | **no** |
| cloud grid and contours<br>`DEFAULT_CLOUD_SIGNAL_BINS`, `DEFAULT_CLOUD_TIME_BINS`, `DEFAULT_CLOUD_SIGNAL_RANGE`, `DEFAULT_CLOUD_HDR_COVERAGES`, `DEFAULT_CLOUD_BW_METHOD` in `tether.analysis.cloud`, plus the `time_range` and `kde` keyword defaults there | `100`, `100`, `(-0.25, 1.25)`, `(0.5, 0.95)`, `scott`, `None` (the whole cloud), `True` | bin counts, apparent-E units, coverage fractions, rule name, time units (seconds when `time_dt` is set, frames at its default of `1.0`), boolean | Resolution of the raw-FRET cloud and of the highest-density contours drawn on it (note 9). | `raw_fret_cloud(time_range=…, kde=…)`, `population_raw_fret_cloud(…)` — no CLI flag, no GUI control | **no** |
| k-vs-RMSE elbow<br>the `k_min` signature default, `DEFAULT_ELBOW_K_MAX`, `DEFAULT_ELBOW_RESTARTS`, `DEFAULT_ELBOW_SEED` in `tether.analysis.cloud`, then the `in_grid_only` signature default | `1`, `8`, `10`, `0`, `True` | cluster-count floor, cluster-count cap, restarts, seed, boolean | A k-means sweep over pooled apparent E that hints at a state count. It is a **hint, not a determination** (note 10). | `k_rmse_elbow(k_min=…, k_max=…, restarts=…, seed=…)`, `population_fret_cloud_state_number_elbow(k_min=…, in_grid_only=…)` — no CLI flag, no GUI control | **no** |
| α-shape support boundary<br>`DEFAULT_ALPHA_FACTOR` in `tether.analysis.cloud`, and the `alpha` / `in_grid_only` keyword defaults there | `2.0`, `None`, `True` | multiplier, dimensionless circumradius threshold, boolean | The concave support boundary drawn around the raw-FRET cloud, and the `time·E` support area reported with it (note 11). | `population_fret_cloud_alpha_shape(alpha=…, in_grid_only=…)`, `alpha_shape(points, alpha=…)` — no CLI flag, no GUI control | **no** — but the returned `AlphaShape.alpha` reports the threshold actually applied, including the auto-selected one |
| cross-correlation lag axis<br>`max_lag`, `normalize` keyword defaults in `tether.analysis.crosscorr` | `None` (the full axis), `pearson` | frames, normalization name | Extent of the donor–acceptor cross-correlation curve (note 12). | `population_cross_correlation(max_lag=…)`, `cross_correlation(max_lag=…)` — no CLI flag, no GUI control | **no** |
| anticorrelation event finder<br>`DEFAULT_ANTICORR_WINDOW`, `DEFAULT_ANTICORR_STEP`, `DEFAULT_ANTICORR_MIN_MAGNITUDE`, `DEFAULT_ANTICORR_MIN_WINDOWS` in `tether.analysis.anticorrelation` | `15`, `1`, `0.5`, `1` | frames, frames, correlation magnitude, consecutive sliding windows | Tether's own sliding-window quality-control flag for anti-phase donor and acceptor behavior, with no tMAVEN counterpart (note 13). | `population_anticorrelation_events(…)` — no CLI flag, no GUI control | **no** |
| plot export<br>`DEFAULT_EXPORT_DPI`, `DEFAULT_FIGSIZE`, `DEFAULT_PLOT_FORMATS` in `tether.analysis.plot_export` | `200`, `(6.4, 4.8)`, `("pdf", "svg", "png")` | dots per inch, inches, formats | Resolution, size and file formats of exported figures, for presentation only. The two vector formats ignore the raster resolution. | `export_figure(dpi=…, formats=…)` and the `render_*` functions' `figsize=` — no CLI flag, no GUI control | **no** |

**Notes**

1. The range is wider than zero to one on purpose, so that out-of-range values are visible and not
   silently clipped. Fewer bins smooth the distribution, and can move the apparent position of a
   peak. More bins show sampling noise.
2. The overlay grid:
    - Tether evaluates the fitted state Gaussians on `linspace(*value_range, n_points)` and sums
      them.
    - The state means, variances and populations that it draws are inputs, and `n_points` changes
      none of them. The population entry point reads them from the stored model. The bare entry
      point takes them from the caller.
    - The value is the grid of the tMAVEN `data_hist1d.py`, kept faithfully. It is a rendering
      fidelity in the same sense as the bin count above, not a science tunable.
    - Both entry points reject fewer than two points, which cannot span the range.
3. The `density` switch:
    - At `True`, Tether divides each bin by its width and by the total in-range mass, so the view
      integrates to 1 over its range. At `False` you get the counts themselves.
    - The split is by dimensionality, not by stage. The default is `True` on the one-dimensional
      apparent-E histogram, its bootstrap CI band, the per-condition overlay set and the
      transition-probability histogram. It is `False` on the time-versus-signal and
      post-synchronized two-dimensional heatmaps, and on the transition-density plot.
    - The two one-dimensional defaults are faithful tMAVEN ports. `data_hist1d.py` and `tm_hist.py`
      both hard-code density normalization, and Tether makes it a switch. The two-dimensional views
      of tMAVEN also bin raw counts. Its `hist_normalize` and `hist_normalizeframe` preferences
      rescale the color axis, and are **not** this switch.
    - The transform is exact and invertible and moves no peak, so no fitted value changes. But two
      histograms drawn at different settings are not numerically comparable.
    - Two overlay curves are densities whatever this switch is. One is the A1 model-Gaussian overlay,
      which takes no `density` argument at all. The other is the transition-probability KDE, which
      is a `gaussian_kde` evaluation either way. Either curve drawn over a `density=False`
      histogram is on a different scale.
    - The dwell-time histogram is always density-normalized and has no switch at all.
4. To share one E axis across the two plots, pass the one-dimensional range.
5. The transition-sync selection:
    - `-1` matches any state, so the defaults synchronize on **every** state jump.
    - A real state index keeps only the transitions that leave `from_state` and enter `to_state`.
      This changes the picture, and the `n_transitions`, `n_molecules` and `n_samples` reported
      with the plot.
    - The tMAVEN equivalents are `sync_hmmstate_1` and `sync_hmmstate_2`, whose defaults are `0`
      and `-1`: transitions *that leave state 0*. Tether's `-1` / `-1` is a deliberate divergence,
      not a port.
    - `single_dwell=True` is the default post-synchronization window of tMAVEN. A transition sits at
      the last frame before a change between two assigned states. The window runs from the frame
      after the previous transition (or the trace start) up to, but not including, the frame of the
      next transition. With no next transition, it runs to the last frame of the trace, inclusive.
    - In a trace with no unassigned (`no_state`) frames, each transition therefore contributes the
      dwell before it and the dwell after it. The dwell after it loses its last frame when another
      transition follows, as in tMAVEN. A trailing dwell is kept whole.
    - A `no_state` frame breaks a run without making a transition. A window can therefore also span
      such a gap and the runs on both sides of it.
    - Tether bins only the window frames that fall inside the time columns and the signal range.
    - `False` takes a fixed `sync_preframe` frames before every selected transition, and
      `time_bins - sync_preframe + 1` frames after it. The post-synchronization windows can then
      overlap and count a frame twice, as in tMAVEN.
    - These three are less cosmetic than the rest of this table: they choose which events Tether
      draws, not how. But no fit is involved, and nothing records the selection that you used.
6. The smoothed view:
    - Tether filters the counts with a Gaussian, interpolates them with a bicubic spline onto an
      `interp_res` × `interp_res` grid, sets negative values to 0 and divides by their maximum.
    - The defaults are the settings of the `checkTDP.py` that Campos-Chavez et al. 2024 published
      with their Source Data. Tether's view equals the array of that script on all seven TDP panels
      of the paper.
    - tMAVEN (`data_tdp.py`) uses the same width and normalization, but does not interpolate by
      default. `interp_res=0` gives that view on the bin-center grid.
    - `0` on a width leaves that axis unfiltered.
    - The counts stay the stored result, and no reported number is read off the view. The view
      therefore changes only the picture.
7. The transition-probability view:
    - The bandwidth is a *factor*, not a width. Tether passes it to `gaussian_kde` as a scalar
      `bw_method`, which scales the spread of the sample itself. `0.25` is therefore a multiplier,
      not 0.25 in probability units.
    - A larger bandwidth smooths the overlay. It does not change the underlying probabilities.
    - `kde` turns the overlay off. At `False`, the returned `kde_x` and `kde_y` are both `None`,
      and only the histogram comes back.
    - At `kde=False`, `kde_bandwidth` and `kde_points` are inert, but **not unchecked**. Tether
      validates both *before* it reads `kde`, so an out-of-range value raises an error at either
      setting.
    - A `None` curve is not by itself evidence that you turned it off. At the default it is also
      `None` when the estimate is undefined. That happens with fewer than two molecules with a
      defined probability, or when every probability is the same. The second case is a singular
      covariance, which the estimator refuses and does not fabricate.
    - This `kde` is different from the `kde` of the raw-FRET cloud further down this table. The
      difference is in the *contract*, not in the value. That `kde` is a separately defined keyword
      default that also happens to be `True`. At `False`, it nulls `density`, `hdr_levels` and
      `bandwidth`, not these two.
8. `1` is the natural floor, because a molecule with a valid idealization occupies at least one
   state. `states_high` at `None` takes the largest occupied count observed. Tether raises it to
   `states_low` if necessary, so that the axis stays valid on an empty cohort or on a cohort that
   is all below the floor. A narrower axis does **not** silently drop molecules. The result carries
   `n_molecules`, `n_in_range` and `n_out_of_range`, so the molecules outside the bars are still
   counted and visible. Tether rejects a `states_high` below `states_low`.
9. The cloud grid and contours:
    - The coverages are the contour levels, not a test. The bandwidth rule controls how smooth the
      density looks.
    - `time_range` at `None` spans `0` to the largest pooled time. A `(lo, hi)` value must have
      `hi > lo`.
    - A `time_range` does more than crop the axis. Tether fits the KDE and its HDR levels on the
      in-grid points only. A narrower range therefore changes the fitted density, the Scott
      bandwidth and the reported `n_out_of_range` count of finite samples left out of the fit.
    - The same argument on `population_fret_cloud_alpha_shape` and
      `population_fret_cloud_state_number_elbow` selects the points that the support boundary goes
      around, and the E values that Tether clusters.
    - `kde` at `True` computes the density surface and the contour levels. At `False`, the returned
      `density`, `hdr_levels` and `bandwidth` are all `None`, and only the raw scatter comes back.
      The α-shape and elbow wrappers at project level always call it that way.
10. The k-vs-RMSE elbow:
    - The bare helper `k_rmse_elbow` takes **none** of the grid and contour parameters in the
      previous row. It clusters the E values that it is handed.
    - `k_min` and `k_max` are the inclusive floor and cap of the k range that Tether probes. `k_max`
      is also capped at the number of distinct pooled E values.
    - The elbow is the k furthest from the chord that joins the two endpoints of the curve. A
      `k_min` above `1` therefore does more than shorten the curve: it can move the returned
      `elbow_k`.
    - More restarts keep the codebook with the lowest distortion, and steady the RMSE curve. The
      seed fixes the initialization.
    - The project-level entry point `population_fret_cloud_state_number_elbow` pools from a
      `.tether` project. It *does* take the `signal_range` of the cloud, `time_range` and
      `time_dt`. The `signal_range` default is the same `(-0.25, 1.25)` as in the previous row.
    - At the default `in_grid_only=True`, Tether clusters only the samples inside that range. An
      off-grid bleach or blink excursion therefore cannot add a false extra E band.
      `in_grid_only=False` clusters the whole scatter.
    - At the default `time_range=None`, the time span is the whole cloud. The E range, not the time
      range, does the filtering.
    - The state count of record comes from the model view.
11. The α-shape:
    - `alpha` is a circumradius cut. Tether applies it after it rescales each axis to `[0, 1]` over
      the bounding box of the cloud. It is therefore dimensionless and comparable across the time
      and E scales.
    - At `None`, `alpha` resolves to `DEFAULT_ALPHA_FACTOR` times the **median finite** triangle
      circumradius. This adapts to the density of the cloud itself.
    - A larger `alpha` keeps more Delaunay triangles. The boundary relaxes toward the convex hull,
      and the reported area grows.
    - A smaller `alpha` drops the slivers that span gaps, for a more concave shape. When no triangle
      survives, Tether returns an empty boundary and does not fabricate one.
    - `in_grid_only` exists **only** on the two `population_fret_cloud_*` entry points, not on
      `alpha_shape` itself. At the default, only the points inside the `time_range` ×
      `signal_range` of the cloud bound the shape. That is the same plot area that the density
      surface is drawn on.
    - A finite but off-grid bleach or blink outlier then stays in the honest scatter, but does not
      pull the boundary out to it. `False` bounds the full scatter.
    - Nothing downstream uses the boundary. It is a QC visualization, not a factor in any fitted
      value.
12. The cross-correlation lag axis:
    - At the default, the curve spans every lag that the data supports: ±(N − 1) frames for one
      trace. For a population, it is ±(N − 1) of the shortest *usable* molecule.
    - `max_lag` truncates that axis *after* the full FFT. It saves no work and moves none of the
      values that remain.
    - The reported `lag0` and `lag1_magnitude` do not change, with one exception. `max_lag=0` drops
      lag +1, and leaves `lag1_magnitude` as `NaN`.
    - `normalize` is not a choice. `"pearson"` is the only accepted value, and any other value
      raises an error. No run can therefore quietly use a different normalization.
    - The `intensity_quantity` and `include_rejected` arguments work as in their rows above.
13. The `0.5` magnitude cut is the classical "clear correlation" threshold. It rejects white
    shot-noise anticorrelation in the same frame. Nothing downstream uses the events, so these
    values change only what Tether highlights for you. Raise the magnitude or the sliding-window
    count to suppress marginal flags.

## Curation ranker

The quality ranker orders molecules for review. It never drops a molecule. Ranking changes the
order in which you see the molecules, not the population that enters an analysis.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `intensity_quantity` / `include_rejected`<br>`compute_features` in `tether.project.features` | `corrected`, `True` | enum `corrected`/`raw`, boolean | Which trace layer the cached `/features/table` is computed on, and which molecules get a row in it (note 1). | `compute_features(intensity_quantity=…, include_rejected=…)` — no CLI flag, no GUI control | `intensity_quantity` **yes**, as the `/features/table` attribute of the same name; `include_rejected` **no** |
| `w0`<br>`DEFAULT_SEED_WEIGHT` in `tether.ml.weighting` | `0.3` | relative weight | Weight of a provisional seed label relative to a human label, which is fixed at `1.0` (note 2). | `train_ranker(w0=…)`, `recompute_label_weights(w0=…)` and `build_deep_dataset(w0=…)` at project level, and `train_condition_model(w0=…)` and `retrain_condition_model(w0=…)` in `tether.project.condition_model` (note 2) — no CLI flag, no GUI control | depends on the surface, and no surface records `w0` itself (note 2) |
| `learning_rate`<br>`RankerHyperparams` in `tether.ml.gbranker` | `0.1` | dimensionless | Gradient-boosting shrinkage. A lower rate learns more slowly and needs more iterations, and a higher rate can overfit a small label set. | `train_quality_ranker(hyperparams=…)`, `ranker_prequential_uplift(hyperparams=…)`, `ranker_leave_one_video_out_uplift(hyperparams=…)` — no CLI flag, no GUI control | **no** (note 3) |
| `max_iter`<br>`RankerHyperparams` in `tether.ml.gbranker` | `100` | boosting iterations | Number of trees: more capacity, and more risk of overfitting on the modest label sets that this ranker is built for. | as above | **no** (note 3) |
| `max_leaf_nodes`<br>`RankerHyperparams` in `tether.ml.gbranker` | `15` | leaves per tree | Tree capacity, set below the library default on purpose because a curator's label set is small. A larger value lets the model make finer distinctions and memorize noise. | as above | **no** (note 3) |
| `min_samples_leaf`<br>`RankerHyperparams` in `tether.ml.gbranker` | `5` | labeled molecules | Least number of labels behind any leaf, lowered from the library default so that the model can split on modest label sets. A larger value makes the model refuse to split until you have labeled more. | as above | **no** (note 3) |
| `l2_regularization`<br>`RankerHyperparams` in `tether.ml.gbranker` | `1.0` | dimensionless | Guards against overfitting a small sample. | as above | **no** (note 3) |
| `early_stopping`<br>`RankerHyperparams` in `tether.ml.gbranker` | `False` | boolean | Off by design. Enabling it would take an internal validation split from a label set that is already small, and the fit would then depend on that split. | as above | **no** (note 3) |
| `random_state`<br>`RankerHyperparams` in `tether.ml.gbranker` | `0` | seed | Fixes the histogram-binning subsample so that a ranking is reproducible. | as above | **no** (note 3) |
| `alpha` (drift advisory)<br>`DEFAULT_DRIFT_ALPHA` in `tether.ml.drift` | `0.05` | family-wise probability | Family-wise significance of the per-feature two-sample test that warns when a model trained on one condition is applied to a visibly different one (note 4). | `cross_condition_drift(alpha=…)` — no CLI flag, no GUI control | **no** |
| `ship_bar_pts`<br>`DEFAULT_SHIP_BAR_PTS` in `tether.ml.prequential` | `10.0` | percentage points of precision-at-k | The uplift over file order, median across videos, at which the prequential evaluation calls the ranker worth using (note 5). | `ranker_prequential_uplift(ship_bar_pts=…)`, `ranker_leave_one_video_out_uplift(ship_bar_pts=…)` — no CLI flag, no GUI control | **no** |

**Notes**

1. The feature table:
    - The ranker is trained and scored on that table (`ranking_dataset` reads it). Both settings
      therefore reach every ranking.
    - A change of layer changes all seven trace-derived features. A molecule with no row cannot be
      ranked at all.
    - The two spatial features (`neighbor_distance`, `aperture_overlap`) come from the `/molecules`
      coordinates. The layer does not change them.
    - **`include_rejected` has the opposite default here from everywhere else on this page.**
      Feature generation keeps rejected molecules by design, because a reject is the negative
      training label of the ranker. The `tether.analysis` population functions exclude them.
    - Compute the table again after a re-extraction or a recalibration. It is a derived cache, not a
      record.
2. The seed weight:
    - Seed weight decays as human labels accumulate, so the model relies on the seed only while you
      have labeled little. A larger `w0` makes a cold-start model trust its own priors longer. A
      smaller `w0` makes early rankings closer to random until you have labeled enough.
    - **`w0` is not only the ranker's.** `build_deep_dataset` uses the same weighted training set
      again. `w0` therefore also sets the per-row weights of the training data of the optional deep
      classifier. The same decay law `w₀/(1 + n_human)` applies on every surface that takes it.
    - `train_condition_model` and `retrain_condition_model` take it for a condition that spans
      projects. They count `n_human` over every project that they read (ADR-0077).
    - The Set via cell is not an index of the API. `w0` is the same keyword, with the same default,
      on every public function of the layer below: `tether.ml.weighting`,
      `tether.project.gbranking` and `tether.project.active`. If you call one of those directly, it
      is there too.
    - `train_ranker` is read-only. It derives the decayed weights in memory for the fit, and writes
      nothing. A training run with a non-default `w0` therefore leaves no trace in the project.
    - `build_deep_dataset` is read-only in the same way, and the deep path loses the value a second
      time. `w0` is **not** one of the keys stamped into the returned
      `TrainedDeepClassifier.hyperparameters`.
    - A newly trained deep model records the seed, device and network shape that it was fitted at.
      A fine-tuned model records the seed, device and freeze mode, and keeps the network shape
      nested under `base_hyperparameters`. Neither records the seed weight of its training set.
    - `recompute_label_weights` is the one surface that changes the project file. It writes the
      resulting `weight` of each label into `/labels/table`. Even then, Tether does **not** stamp
      `w0` itself.
    - The portable model that `train_condition_model` and `retrain_condition_model` return does not
      record it either. Its `manifest.json` names the source projects, not the seed weight.
3. None of the surfaces in the Set via cells writes the value anywhere. `train_quality_ranker`
   returns an in-memory `QualityRanker`. `ranker_prequential_uplift` and
   `ranker_leave_one_video_out_uplift` are read-only and save no model. Only the separate
   portable-model path writes a `manifest.json` with the hyper-parameters: `train_portable_model`
   or `warm_start_retrain` in `tether.ml.persistence`, written out by `save_model`. Without that
   path, a non-default `hyperparams` is recoverable only from your own notes.
4. The test is Bonferroni-corrected across the features that are actually tested. The report is
   **advisory**, and nothing is blocked. A larger value gives more warnings.
5. The leave-one-video-out evaluation reports against the same value. It is a reporting threshold
   on an evaluation, and it does not change a ranking.

Both evaluations measure **one condition**, because the ranker is per condition. Pass
`condition_id` to `ranker_prequential_uplift` or `ranker_leave_one_video_out_uplift` to choose
it. Without one, a project whose labeled molecules span more than one condition raises
`ValueError`, which names the conditions. The [Background](#earlier-ranker-evaluations) tells how
the prequential evaluation worked before.

## Deep trace classifier

The optional GPU add-on has its own defaults.
[Deep trace classifier (optional GPU add-on)](../ml/deep-classifier.md) tells how to set it up and
use it. None of these defaults affect the base application. None are recorded in `.tether`,
because a trained deep model is a separate artifact.

| Parameter | Default | Type and unit | Meaning | Set via | Recorded in `.tether` |
| --- | --- | --- | --- | --- | --- |
| `intensity_quantity`<br>`tether.project.deep_dataset` | `corrected` | enum `corrected`/`raw` | Which `/traces` layer supplies the model input (note 1). | `build_deep_dataset(intensity_quantity=…)` — no CLI flag, no GUI control | **no** — stamped on the returned `TrainedDeepClassifier`, which `predict_proba` and `fine_tune` both check before scoring or adapting |
| `channels`<br>`DEFAULT_DEEP_CHANNELS` in `tether.ml.deep.dataset` | `("donor", "acceptor")` | channel names | Which measured channels Tether stacks, in order, as the model input (note 2). | `build_deep_dataset(channels=…)` or `assemble_dataset(channels=…)` — no CLI flag, no GUI control | **no** — stamped on the returned `TrainedDeepClassifier`, which `predict_proba` checks before scoring |
| `window_length`<br>`DEFAULT_WINDOW_LENGTH` in `tether.ml.deep.dataset` | `500` | frames | Fixed length to which Tether crops or zero-pads every trace (note 3). | `assemble_dataset(window_length=…)` — no CLI flag, no GUI control | **no** |
| `normalization`<br>`DEFAULT_NORMALIZATION` in `tether.ml.deep.dataset` | `per_trace_total` | enum | Per-trace scaling of the two channels by their combined total, which keeps the donor/acceptor ratio and removes absolute brightness. `none` keeps absolute counts, which makes the model sensitive to gain and exposure. | `assemble_dataset(normalization=…)` — no CLI flag, no GUI control | **no** |
| `val_fraction` / `split_seed` / `stratify`<br>`DEFAULT_VAL_FRACTION`, `DEFAULT_SPLIT_SEED` in `tether.ml.deep.dataset`; `stratify` is a keyword default there, with no constant behind it | `0.2`, `0`, `True` | fraction, seed, boolean | Size, reproducibility and class balance of the held-out split **only** (note 4). | `train_val_split(val_fraction=…, seed=…, stratify=…)`, `DeepTraceDataset.split(…)` — no CLI flag, no GUI control | **no** |
| `epochs` / `batch_size` / `learning_rate`<br>`DEFAULT_EPOCHS`, `DEFAULT_BATCH_SIZE`, `DEFAULT_LEARNING_RATE` in `tether.ml.deep.model` | `20`, `32`, `0.001` | epochs, traces, dimensionless | Training budget. Too few epochs underfit, and too many overfit a small label set, with no early stopping to catch it. | `train_classifier(…)` and `fine_tune(…)`, which take the same `batch_size` default; `predict_proba(…)` and `rank_by_deep_model(…)` take it too, but there it only chunks the scoring pass — no CLI flag, no GUI control | **no** |
| training `seed`<br>`DEFAULT_SPLIT_SEED` in `tether.ml.deep.dataset`, reused as the signature default of `train_classifier` and `fine_tune` in `tether.ml.deep.model` | `0` | seed | The training random number generator: the same constant as the split seed, but a different job (note 5). | `train_classifier(seed=…)`, `fine_tune(seed=…)` — no CLI flag, no GUI control | **no** — stamped as `seed` in the returned `TrainedDeepClassifier`'s `hyperparameters` |
| `num_conv_layers` / `conv_channels` / `kernel_size` / `lstm_hidden` / `bidirectional` / `dropout`<br>`DEFAULT_NUM_CONV_LAYERS`, `DEFAULT_CONV_CHANNELS`, `DEFAULT_KERNEL_SIZE`, `DEFAULT_LSTM_HIDDEN`, `DEFAULT_BIDIRECTIONAL`, `DEFAULT_DROPOUT` in `tether.ml.deep.model` | `2`, `32`, `5`, `32`, `True`, `0.0` | layers, channels, frames, units, boolean, probability | Network shape. Larger capacity fits more complex trace morphologies and overfits sooner, and dropout above zero trades training fit for generalization. | `train_classifier(…)` — no CLI flag, no GUI control | **no** |
| fine-tuning `epochs` / `learning_rate` / `freeze_conv`<br>`DEFAULT_FINE_TUNE_EPOCHS`, `DEFAULT_FINE_TUNE_LEARNING_RATE`, `DEFAULT_FREEZE_CONV` in `tether.ml.deep.model` | `10`, `0.0001`, `False` | epochs, dimensionless, boolean | Transfer of a trained model to a new condition (note 6). | `fine_tune(…)` — no CLI flag, no GUI control | **no** |
| `device`<br>`DEFAULT_DEVICE` in `tether.ml.deep.model` | `cpu` on `train_classifier`; `None` on `fine_tune` and `predict_proba` | device string | Where training runs. It is not a scientific parameter, but it is probably the one that you change first (note 7). | `train_classifier(device=…)`, `fine_tune(device=…)`, `predict_proba(device=…)` — no CLI flag, no GUI control | **no** — but the device actually used is stamped as `device` in the returned `TrainedDeepClassifier`'s `hyperparameters`, which is the record the fine-tune and scoring paths inherit from |

**Notes**

1. The trace layer of the model input:
    - `corrected` selects the background-subtracted layer (`/traces/donor_corrected` and
      `/traces/acceptor_corrected`). `raw` selects the layer before background subtraction.
    - You choose it independently of the layer of the engineered features. The labels and weights
      belong to each molecule and do not depend on the layer.
    - Training on `raw` changes the model input as much as dropping a channel does. The per-trace
      scaling does not undo it. `per_trace_total` divides both channels by one shared number, which
      rescales an additive background pedestal but never removes it. On `raw`, the pedestal
      therefore stays in the tensor and inflates the divisor.
    - A model trained on one layer cannot score a dataset built on the other: `predict_proba`
      refuses.
    - Only `build_deep_dataset` reads a layer. `assemble_dataset` accepts the same argument as a
      **provenance label only**, because the caller supplies the arrays.
2. The tuple is axis 1 of the tensor, and so the input **channel count** of the first convolution.
   A donor-only or acceptor-only set therefore trains a different classifier on strictly less of
   the trace. Tether accepts only a non-empty subset of `("donor", "acceptor")` with no duplicates.
   Dropping a channel does not change the scaling. Tether still computes the shared per-trace scale
   from donor **plus** acceptor.
3. A shorter model window discards the tail of long traces, which is real data. A longer window
   only costs compute. A boolean mask marks the real frames, and the convolutional stack keeps the
   length. The LSTM is packed against the valid length of each trace, so padded frames never reach
   the recurrent summary or the classification head.
4. The held-out split:
    - The same `DEFAULT_SPLIT_SEED` constant also supplies the training seed, which is a different
      job. See the training `seed` row below.
    - A smaller validation fraction leaves more data for training, and gives a noisier estimate of
      how well training worked.
    - At the default, Tether draws the split **within** each label class. The accept/reject ratio
      is therefore the same on both sides, and a class of two or more rows never empties out of
      either side.
    - `stratify=False` draws the validation rows from one global pool. The class balance of the
      held-out rows can then drift away from the training one. A held-out score from a split that
      is not stratified is not comparable with a score from the default.
5. Tether passes it to `torch.manual_seed` before it initializes the weights, and it seeds the
   `DataLoader` shuffle generator. Two runs that differ only in `seed` therefore give **different
   trained classifiers** and different per-trace scores. Report the seed that you trained at. The
   source claims determinism for a fixed seed **on CPU**. A CUDA run is not promised to be
   bit-reproducible.
6. The low fine-tuning rate keeps the transferred model from forgetting the source condition.
   Freezing the convolutional stack restricts adaptation to the recurrent head, which helps when the
   new condition has very few labels. `fine_tune` also takes `batch_size`, at the training default
   of `32`. Tether stamps it into the hyperparameters of the fine-tuned model beside the fine-tune
   values, so report it if you changed it.
7. The device:
    - **The `cpu` default is `train_classifier`'s.** `fine_tune` and `predict_proba` take
      `device=None` and resolve it from the model that they are handed.
    - `fine_tune` reads `base.hyperparameters['device']`, and `predict_proba` reads the device of
      the trained classifier. Both fall back to `DEFAULT_DEVICE` when the model records no device. A
      bare `nn.Module` handed to `predict_proba` gets the same fallback.
    - A classifier trained on CUDA therefore fine-tunes and scores on CUDA, unless you pass `device`
      yourself.
    - `rank_by_deep_model` takes no `device` argument at all, and always inherits.

## Tolerances and gates

Some numbers in Tether are acceptance thresholds, not settings. They decide whether a result is
trustworthy. This page does **not** repeat their values on purpose. The evidence for each value is
in the decision record that set it, and a second copy would drift. Use this section to find the
authority for each value, and then read the value there. A row says so when you can set a value for
each call.

[Validation &amp; benchmarks](../validation.md) carries the evidence for the gates that were
measured, not chosen. It gives the frozen numbers and what produced them. It also states which
direction of change CI catches, and which direction it does not catch.

| Gate | Where the value lives | What it decides |
| --- | --- | --- |
| Registration RMS residual | `DEFAULT_RMS_GATE_PX` in `tether.imaging.calibrate`; rationale in [ADR-0014](../adr/0014-registration-map-rms-gate-and-over-gate.md) | Whether a channel map is flagged low-confidence, and, under `tether batch --policy fail`, whether the movie is failed. You can set it as `--rms-gate`, but the *policy* is the gate. |
| Donor-side check | `SIDE_CHECK_MIN_EVENTS`, `SIDE_CHECK_MAJORITY`, `SIDE_CHECK_STEP_Z`, `SIDE_CHECK_LEVEL_FRAMES` and `SIDE_CHECK_MIN_FRAMES` in `tether.imaging.side_check`; thresholds, measured false-alarm rates and rationale in [ADR-0070](../adr/0070-advisory-check-for-swapped-donor-and-acceptor-halves.md) | Whether `tether extract` warns, and `tether batch` lists the movie, as having its donor and acceptor halves swapped. **Advisory only** (note 1). |
| Leakage α acceptance range | `LEAKAGE_CEILING` in `tether.fret.leakage`; estimator rationale in [ADR-0027](../adr/0027-leakage-alpha-tail-estimator.md) | Which per-trace α values enter the dataset median. |
| γ acceptance range | `GAMMA_CEILING` in `tether.fret.gamma`; estimator rationale in [ADR-0028](../adr/0028-gamma-acceptor-bleach-step-estimator.md) | Which per-trace γ values enter the dataset median. |
| Idealization parity tolerance | `schema/parity_tolerance.json`, loaded by `load_frozen_tolerance` in `tether.idealize.parity`; rationale in [ADR-0009](../adr/0009-parity-metrics-and-freeze.md) and [ADR-0043](../adr/0043-per-method-parity-tolerance.md); measured spreads and re-freeze recipe in [Validation &amp; benchmarks](../validation.md) | Whether Tether's idealization is accepted as matching the reference implementation. Read the row of your method in the file, not the top-level row (note 2). |
| Bleach-frame agreement | asserted directly in [`tests/test_fret_photobleach.py`](https://github.com/bioedca/tether/blob/main/tests/test_fret_photobleach.py) | How closely the detector must recover a known bleach frame. It exists only as a test assertion, with no module constant, and therefore nothing to set. |
| Photobleach analysis-window default | rationale in [ADR-0026](../adr/0026-photobleach-detection-and-window-default.md) | How the automatic analysis window is derived, and why a manual analysis window always wins. |
| Per-trace interaction budget | `PER_TRACE_LATENCY_BUDGET_S` in `tether.project.perf` | A performance budget, not a scientific one: how long one molecule can take to render and advance. |
| Lock staleness | `DEFAULT_STALENESS_TIMEOUT_S` in `tether.project.lock` | When the write lock of another process on a project is treated as abandoned. `Project.acquire_lock(timeout_s=…)` and `Project.write_lock(timeout_s=…)` accept it for each call, but Tether does not store the value that you used. |
| Return-leg identity match | `DEFAULT_MATCH_ATOL` / `DEFAULT_MATCH_RTOL` in `tether.project.handoff`; rationale in [ADR-0025](../adr/0025-tmaven-handoff-and-return-leg-reconcile.md) | Which traces from standalone tMAVEN are recognized as the molecules they were when they left the project (note 3). It is an equality guard, not a tunable. |

**Notes**

1. The check changes no data and no exit code. On a sample that it cannot judge, it reports "not
   enough bleach events", not a verdict. You cannot set it from the CLI, but `check_donor_side(…)`
   takes each threshold as a keyword. Tether stores the result and the thresholds that it used as
   `donor_side_check` in `/settings/extraction`.
2. The file carries a row for **each method**. The empirical-Bayes model is ratified against its
   own, looser tolerance, not the default one.
3. The return-leg match:
    - Recognition decides whether an accepted analysis-window edit, class change or imported model
      reaches a trace at all.
    - Tether reports an unrecognized trace as unmatched and drops it. It never attributes the trace
      to the wrong molecule.
    - tMAVEN keeps the SMD `raw` array unchanged across a save, so the correct test is equality
      with a float guard.
    - You can set it for each call, but only on the module-level
      `tether.project.handoff.read_return_leg` and `apply_reconcile`. The `Project` methods of the
      same name and the GUI reconcile dialog do not forward it, and no CLI flag exposes it.

## Recording parameters in a methods section

If you ran everything at the defaults, this page describes your run, and you can cite it. If you
changed a value, what you can recover afterwards depends on the stage:

- **Extraction and registration.** These are fully recoverable for the one-movie project that
  `tether extract` and `tether batch` make. Read `/settings/extraction`. The aperture and
  integration parameters are attributes. The `profile_json` attribute holds every field of
  `ExtractOptions` as it was applied. There are three exceptions:
    - The two Deep-LASI legacy-import paths, `reconstruct_project` and
      `import_analysis_only_project`, never had an `ExtractOptions`. They are the two legs that
      the Deep-LASI wizard of the GUI drives. A reconstruction writes the group. But its five
      aperture and integration attributes are fixed module constants. They describe the provenance
      of the pre-integrated legacy traces, not an integration that the import did. Its
      `profile_json` holds a short provenance dict.
    - An analysis-only import (a coordinate-less SMD or `.txt`, with no movie) writes no
      `/settings/extraction` group at all.
    - The *first* extraction into a project stamps the group, and Tether never writes it again. It
      checks a later movie for consistency only on `window`, `disk_radius`, `ring_inner`,
      `ring_outer` and `bg_window`. You can append several movies into one project from Python
      (`write_extraction`), but no CLI or GUI command does this. `/settings/extraction` then
      describes the first movie only. A later movie's `detection_mode`, `prealign`, `pair_tol`,
      `rms_gate` or `coloc_distance` is neither rejected nor recorded, so you must state those
      values yourself.
- **Batch policy.** This is recoverable from `/settings/batch`. Tether does not record the sidecar
  supervision settings (timeout and restarts). Without `--profile` or `--colocalization`, the
  `/settings/extraction` of a batch run holds the shipped `ExtractOptions` defaults. A run driven
  from Python with an explicit `extract_options` is an exception. A run with `--tdat` is another,
  because `--tdat` replaces the detection mode and threshold for every movie in the run. A `--tdat`
  run is still fully recoverable. `/settings/extraction` holds the values that were applied, and
  names the file that they came from in `tdat_source`.
- **Corrections.** These are fully recoverable. `/settings/leakage` and `/settings/gamma` record
  the estimator window, ceiling and qualifying-count thresholds that were used. They also record
  the resulting factor, and how many molecules qualified. `/settings/correction` records any
  overrides, and whether apparent E was forced.
- **Idealization.** The `/idealization/<model>` group records the model type, the selected state
  count and how it was selected. It also records the evidence for each K when the state count was
  chosen automatically. Tether does not record the sidecar timeout and restart budget, or
  `nrestarts`. If you overrode the restart count of the fitter, write it down.
- **Photobleaching.** Tether records the *results*, not the *inputs*: neither the priors nor the
  `intensity_quantity` trace layer that detection ran on. If you changed either, write it down
  yourself.
- **Analysis.** Tether records nothing, because it computes analysis outputs on demand and does
  not store them. State in your methods section any bin count, bootstrap setting, trace layer or
  frame interval that you passed. Check the frame interval first: at the default, dwell times and
  rates come out per frame, not per second. State the `intensity_quantity` too. A histogram
  computed on `raw` traces is not comparable to one computed on `corrected`, and nothing in the
  project file records which you used.
- **Curation ranking.** The trace layer of the cached feature table *is* recoverable, as the
  `intensity_quantity` attribute of `/features/table`. Whether rejected molecules were featured is
  not, and neither are the hyper-parameters of the ranker. Only the separate portable-model path
  writes them down. `train_portable_model` or `warm_start_retrain`, saved with `save_model`, stamps
  them into the `manifest.json` of the artifact. The three surfaces that accept a `hyperparams`
  override produce no artifact at all: `train_quality_ranker`, `ranker_prequential_uplift` and
  `ranker_leave_one_video_out_uplift`. The project-level `train_ranker` takes no override, and
  always fits at `DEFAULT_HYPERPARAMS`. State a non-default hyper-parameter outside the
  portable-model path in your methods section.

## Background

This section gives the reasons behind some entries above. You do not need it to look up a value.

### Why three analysis parameters are science tunables

The frame interval (`dt` / `time_dt`), the dwell-fit model and CI level, and the HMM convergence
budget (`n_iter`) are labeled *analysis/rendering* in their own source comments. This page tables
them as **science tunables**, because each one changes a number that you report. The frame interval
has the largest effect. At its default, it silently gives every published rate the wrong unit.

### Why the clean-step fraction is above one half

Noise alone can make many steps read clean by chance. About half read clean when the corrected
acceptor drops and the donor change is equally likely to have either sign. That is the noise model
that [ADR-0134](../adr/0134-a-gamma-from-few-clean-acceptor-bleach-steps-is-reported-with-a-warning.md)
states. The fraction is therefore set above one half.

On the workstation run of 2026-11-19, `0.65` separated two groups of three acquisitions. In the
first group, γ is within 1.2 times the γ of Deep-LASI, with clean fractions from 0.70 to 0.77. In
the second group, γ is 2.7 to 3.0 times that of Deep-LASI, with clean fractions from 0.21 to 0.58.

### Where the rendering defaults come from

Most rendering defaults are faithful ports of the tMAVEN display defaults. Each of these rows cites
the tMAVEN file that it came from:

- the 1-D histogram and its A1 model overlay: `data_hist1d.py`,
- the 2-D and post-synchronized heatmaps: `data_hist2d.py`,
- the transition-density grid: `data_tdp.py`,
- the dwell histogram: `survival_dwell.py`,
- the transition-probability view: `tm_hist.py`,
- the state-number bar chart: `model_vbstates.py`.

The last six rows are Tether's own: the raw-FRET cloud, its k-vs-RMSE elbow helper, its α-shape
support boundary, the donor–acceptor cross-correlation, the anticorrelation event finder and the
plot-export settings. The anticorrelation event finder is a QC aid. It is the counterpart, before
idealization, of the transition list of the HMM. The three export formats are a requirement of the
product specification. The DPI and the figure size are cosmetic output settings.

None of the six is a port of a tMAVEN *display default*, but two have a precedent. tMAVEN keeps its
own figure width and height as plot preferences (`analysis_plots/base.py`). Its trace-plot canvas
sets its savefig format and DPI as global matplotlib rcParams (`trace_plot/multi_plot.py`). tMAVEN
also ships k-means as a modeling routine (`modeler/kmeans.py`, `modeler.run_kmeans`).

The cross-correlation is the only one of the six that targets the same quantity as a named tMAVEN
routine. Tether's version is a deliberate divergence, not a port. The `selection.py` heuristic of
tMAVEN is **unnormalized**, gradient-based and lag-zero-only. Here "unnormalized" does not mean the
`raw` layer of the science table, because the heuristic runs on the corrected data of tMAVEN itself.

### The planned plot builder

The plot builder is planned for the analysis section of the application. It is to set each of the
six values without a GUI control on a plot, or on one series of a plot. It is to save them in the
composition file of the plot. A planned `tether plot` command renders that file again with the same
values. The builder is also planned to take `dt` from the `frame_time` of each movie itself. It is
to refuse a series whose movies have different frame times.

### Earlier ranker evaluations

Before the evaluations took a condition, the prequential evaluation pooled every condition. A
median from a project with several conditions therefore mixed them. A project with one condition
gives the same result as before.
