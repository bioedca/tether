# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The private data of oracle (a): video 010 of ADR-0022, under its neutral id (#699).

The movie, its ``.tmap``, its ``.tdat`` and its two Deep-LASI exports are private laboratory
data. They are on no CI runner, so every test that reads them is a ``workstation`` test
(ADR-0071) and finds them through the ``private_data`` fixture of ``tests/conftest.py``. The
names of the files stay in the name table on the workstation; a test and its assertion
messages name only the neutral id.
"""

from __future__ import annotations

from pathlib import Path

#: The neutral id of video 010 in the workstation's name table.
ORACLE_A = "lab-01"


def oracle_a_file(private_data, role: str) -> Path:
    """One file of video 010: ``movie``, ``tmap``, ``tdat``, ``mat`` or ``txt``."""
    return private_data.path(ORACLE_A, role)


def oracle_a_folder(private_data) -> Path:
    """The folder that holds the whole bundle of video 010 (the folder of its movie)."""
    return oracle_a_file(private_data, "movie").parent
