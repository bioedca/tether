# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Application services: one Qt-free command for each action (PRD §7.11, FR-WORKFLOW-7).

The application, the command line and a recipe file drive the workflow through the same
*service commands* (ADR-0100). A command takes a *request*, which is plain data with one
text form ([`request_to_text`][], [`request_from_text`][]), and returns a result. The
command line builds the same request from its options, so each action has one
implementation and the command line cannot fall behind the application.

This package imports no Qt module and no part of [`tether.gui`][]; a test checks it. The
imaging and HDF5 stack is imported only when a command runs.

>>> from tether.services import ExtractRequest, request_from_text, request_to_text
>>> req = ExtractRequest(movie="m.tif", output="m.tether")
>>> request_from_text(request_to_text(req)) == req
True
"""

from __future__ import annotations

from tether.services.base import (
    CommandFailed,
    Request,
    RequestError,
    RequestRefused,
    ServiceError,
    request_to_text,
)
from tether.services.batch import BatchRequest, BatchResult
from tether.services.extract import ExtractRequest, ExtractResult
from tether.services.maps import MapCreateRequest, MapCreateResult
from tether.services.registry import (
    COMMANDS,
    ServiceCommand,
    command,
    request_from_text,
    run,
)

__all__ = [
    "COMMANDS",
    "BatchRequest",
    "BatchResult",
    "CommandFailed",
    "ExtractRequest",
    "ExtractResult",
    "MapCreateRequest",
    "MapCreateResult",
    "Request",
    "RequestError",
    "RequestRefused",
    "ServiceCommand",
    "ServiceError",
    "command",
    "request_from_text",
    "request_to_text",
    "run",
]
