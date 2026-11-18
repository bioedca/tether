---
description: "The generated API reference: every public function and class of the tether package, one page for each subpackage."
---

# API reference

**Who this page is for.** You want to call Tether from a Python script instead of from the
window. For example, you draw a histogram, read a project file or export a dataset with no
display. This page tells you where each part of the package is documented. It needs no write
access to `bioedca/tether`.

The pages in this section are generated from the docstrings of the package when the site is
built. So a name, a signature and a default value on these pages always match the code of this
version. Each page shows one subpackage, one module at a time. Each module shows only the names
in its `__all__` list.

| Subpackage | What it holds |
|---|---|
| [`tether.io`](io.md) | Movie and file readers, the project file schema and the exporters |
| [`tether.imaging`](imaging.md) | Movie splitting, spot detection, registration and trace extraction |
| [`tether.fret`](fret.md) | Photobleaching detection, leakage and gamma corrections, corrected FRET |
| [`tether.idealize`](idealize.md) | The tMAVEN sidecar driver, the standalone hand-off and parity checks |
| [`tether.ml`](ml.md) | Trace features, the quality ranker and the deep classifier |
| [`tether.analysis`](analysis.md) | Histograms, transition-density plots, dwell times, rates and statistics |
| [`tether.project`](project.md) | The project model, the headless core and the batch runner |
| [`tether.services`](services.md) | One Qt-free command for each action, shared by the application, the command line and recipes |
| [`tether.gui`](gui.md) | The desktop window, its docks and its dialogs |

The command line has its own page, [Command line](../cli.md).

## The top-level package

::: tether

::: tether.cli

::: tether.cli_export

::: tether.extract_choices
