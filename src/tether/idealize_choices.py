# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The methods ``tether idealize --method`` offers (#657).

[`tether.project.idealize`][] exports this tuple as its own list of whole-project methods,
and [`tether.cli`][] gives it to argparse as ``choices``, so the parser and the library
cannot offer different methods.

This module imports nothing. [`tether.cli`][] reads it when it builds the parser, and that
also happens for ``tether --version``, which must not load the HDF5 stack (ADR-0018), as
`tether.extract_choices` does for ``tether extract``.
"""

from __future__ import annotations

__all__ = ["IDEALIZE_METHODS"]

IDEALIZE_METHODS: tuple[str, ...] = ("vbconhmm", "ebhmm")
"""The whole-project fits: the consensus VB-HMM (the default, first) and ebFRET.

These are [`MODEL_TYPE_DEFAULT`][tether.project.idealize.MODEL_TYPE_DEFAULT] and
[`MODEL_TYPE_EBHMM`][tether.project.idealize.MODEL_TYPE_EBHMM]; a test keeps them equal. A
model scan over every tMAVEN modeler is #937's, not this list's.
"""
