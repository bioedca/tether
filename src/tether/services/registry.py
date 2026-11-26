# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The registry of service commands (ADR-0100).

[`COMMANDS`][] lists every service command, in the order the command line shows them.
The command-line routes ([`tether.cli`][]) and the command list on the reference page
(``docs/cli.md``) are made from it, and a test fails when a registered command has no
command-line route.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any

from tether.services import approve, batch, compositions, extract, idealize, maps, reextract
from tether.services.base import Request, RequestError, request_from_mapping

__all__ = [
    "COMMANDS",
    "ServiceCommand",
    "command",
    "reference_table",
    "request_from_text",
    "run",
]


@dataclasses.dataclass(frozen=True)
class ServiceCommand:
    """One registered service command.

    ``name`` is the command-line subcommand and the ``command`` key of the request's
    text form; ``summary`` is its one-line help. ``approval_steps`` names each approval
    step of ADR-0102 that the command can reach; a recipe run then passes the command an
    ``approvals`` gate ([`tether.services.recipe.ApprovalGate`][]), and a recipe may name
    a policy for those steps only.
    """

    name: str
    request_type: type[Request]
    result_type: type
    run: Callable[[Any], Any]
    summary: str
    approval_steps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a name that is not the request type's ``COMMAND``."""
        if self.name != self.request_type.COMMAND:
            raise ValueError(
                f"{self.request_type.__name__}.COMMAND is {self.request_type.COMMAND!r}, "
                f"not {self.name!r}"
            )


COMMANDS: tuple[ServiceCommand, ...] = (
    ServiceCommand(
        name="extract",
        request_type=extract.ExtractRequest,
        result_type=extract.ExtractResult,
        run=extract.run,
        summary="extract traces from a dual-channel movie into a .tether project",
    ),
    ServiceCommand(
        name="reextract",
        request_type=reextract.ReextractRequest,
        result_type=reextract.ReextractResult,
        run=reextract.run,
        summary="extract a movie of a .tether project again, as a new trace generation",
    ),
    ServiceCommand(
        name="batch",
        request_type=batch.BatchRequest,
        result_type=batch.BatchResult,
        run=batch.run,
        summary="run the extract -> correct -> idealize pipeline over many movies",
    ),
    ServiceCommand(
        name="idealize",
        request_type=idealize.IdealizeRequest,
        result_type=idealize.IdealizeResult,
        run=idealize.run,
        summary="fit the molecules of a .tether project, or refit its stale models",
    ),
    ServiceCommand(
        name="map",
        request_type=maps.MapCreateRequest,
        result_type=maps.MapCreateResult,
        run=maps.run,
        summary="make a channel map file (.npz) from a bead video or image",
    ),
    ServiceCommand(
        name="approve",
        request_type=approve.ApproveRequest,
        result_type=approve.ApproveResult,
        run=approve.run,
        summary="record that a person approved a step that needs a person",
    ),
    ServiceCommand(
        name="save-composition",
        request_type=compositions.SaveCompositionRequest,
        result_type=compositions.SaveCompositionResult,
        run=compositions.run_save,
        summary="save a plot composition of the plot builder to a file (no trace data)",
    ),
    ServiceCommand(
        name="open-composition",
        request_type=compositions.OpenCompositionRequest,
        result_type=compositions.OpenCompositionResult,
        run=compositions.run_open,
        summary="open a saved plot composition and name each series that cannot be found",
    ),
)


def command(name: str) -> ServiceCommand:
    """Return the registered command called *name*; raise `KeyError` if none."""
    for entry in COMMANDS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def run(request: Request, **observers: Any) -> Any:
    """Run *request* through its registered command and return the command's result.

    Keyword arguments go to the command unchanged. They watch the run, such as the
    ``progress`` of the ``batch`` command, and are not part of the request.
    """
    entry = command(request.COMMAND)
    if type(request) is not entry.request_type:
        raise TypeError(f"expected {entry.request_type.__name__}, got {type(request).__name__}")
    return entry.run(request, **observers)


def request_from_text(text: str) -> Request:
    """Read a request from its text form; raise [`RequestError`][] if it is not one."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RequestError(f"a request is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RequestError("a request must be a JSON object")
    name = data.get("command")
    try:
        entry = command(name) if isinstance(name, str) else None
    except KeyError:
        entry = None
    if entry is None:
        known = ", ".join(c.name for c in COMMANDS)
        raise RequestError(f"unknown command {name!r} (known: {known})")
    return request_from_mapping(data, entry.request_type)


def reference_table() -> str:
    """Return the Markdown table of the registered commands for ``docs/cli.md``."""
    rows = [
        "| Command | Request | Result | What it does |",
        "|---|---|---|---|",
    ]
    for entry in COMMANDS:
        module = entry.run.__module__
        rows.append(
            f"| `tether {entry.name}` | `{module}.{entry.request_type.__name__}` "
            f"(version {entry.request_type.VERSION}) | `{module}.{entry.result_type.__name__}` "
            f"| {entry.summary} |"
        )
    return "\n".join(rows)
