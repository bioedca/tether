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

from tether.services import batch, extract
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
    text form; ``summary`` is its one-line help.
    """

    name: str
    request_type: type[Request]
    result_type: type
    run: Callable[[Any], Any]
    summary: str

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
        name="batch",
        request_type=batch.BatchRequest,
        result_type=batch.BatchResult,
        run=batch.run,
        summary="run the extract -> correct -> idealize pipeline over many movies",
    ),
)


def command(name: str) -> ServiceCommand:
    """Return the registered command called *name*; raise [`KeyError`][] if none."""
    for entry in COMMANDS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def run(request: Request) -> Any:
    """Run *request* through its registered command and return the command's result."""
    entry = command(request.COMMAND)
    if type(request) is not entry.request_type:
        raise TypeError(f"expected {entry.request_type.__name__}, got {type(request).__name__}")
    return entry.run(request)


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
