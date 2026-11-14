# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Requests, their text form, and the errors of a service command (ADR-0100).

A *request* is plain data: a frozen, keyword-only dataclass whose fields are ``str``,
``int``, ``float``, ``bool``, ``None`` or a tuple of ``str``. Each request class names its
command (:attr:`Request.COMMAND`) and the version of its text form
(:attr:`Request.VERSION`).

The text form is one JSON object::

    {"command": "extract", "fields": {...every field...}, "version": 1}

written with sorted keys and no spaces. Every field is written, including the ones that
hold their default, so a later change of a default never changes what a stored request
means. A reader accepts a missing field only when the field has a default (a field that
was added after the request was written), and refuses an unknown field, a value of the
wrong type, and a version it does not know.

This module imports only the standard library.
"""

from __future__ import annotations

import dataclasses
import json
import math
import types
import typing
from typing import Any, ClassVar, Union

__all__ = [
    "CommandFailed",
    "Request",
    "RequestError",
    "RequestRefused",
    "ServiceError",
    "request_from_mapping",
    "request_to_mapping",
    "request_to_text",
]


class ServiceError(Exception):
    """The base class of the expected failures of a service command."""


class RequestError(ServiceError, ValueError):
    """A request text is not a valid request (bad JSON, field, type or version)."""


class RequestRefused(ServiceError):
    """The command refused the request before it read or wrote any project."""


class CommandFailed(ServiceError):
    """The command started and failed; the message says why."""


@dataclasses.dataclass(frozen=True, kw_only=True)
class Request:
    """The base class of every service-command request."""

    COMMAND: ClassVar[str] = ""
    VERSION: ClassVar[int] = 1


_SCALARS: tuple[type, ...] = (str, int, float, bool)


def _hints(cls: type[Request]) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    return {f.name: hints[f.name] for f in dataclasses.fields(cls)}


def _allows_none(hint: Any) -> tuple[bool, Any]:
    """Split ``X | None`` into ``(True, X)``; any other hint into ``(False, hint)``."""
    if typing.get_origin(hint) in (Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1 and len(typing.get_args(hint)) == 2:
            return True, args[0]
        raise TypeError(f"unsupported request field type {hint!r}")
    return False, hint


def _check(name: str, value: Any, hint: Any) -> Any:
    """Return *value* in the field's Python type, or raise :class:`RequestError`."""
    optional, base = _allows_none(hint)
    if value is None:
        if optional:
            return None
        raise RequestError(f"field {name!r} may not be null")
    if typing.get_origin(base) is tuple:
        args = typing.get_args(base)
        if len(args) != 2 or args[0] is not str or args[1] is not Ellipsis:
            raise TypeError(f"unsupported request field type {base!r}")
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise RequestError(f"field {name!r} must be a list of strings")
        return tuple(value)
    if base not in _SCALARS:
        raise TypeError(f"unsupported request field type {base!r}")
    # bool is a subclass of int: refuse it where a number is wanted, and a number
    # where a bool is wanted.
    if base is bool:
        if isinstance(value, bool):
            return value
    elif not isinstance(value, bool):
        if base is float and isinstance(value, (int, float)):
            try:
                number = float(value)
            except OverflowError:  # an int too large for a float
                number = math.inf
            if math.isfinite(number):
                return number
            raise RequestError(f"field {name!r} must be a finite number")
        if isinstance(value, base):
            return value
    raise RequestError(f"field {name!r} must be {base.__name__}, not {type(value).__name__}")


def request_to_mapping(request: Request) -> dict[str, Any]:
    """Return the text form of *request* as a JSON-ready mapping.

    Raises :class:`RequestError` when a field holds a value that has no text form.
    """
    if not isinstance(request, Request) or not dataclasses.is_dataclass(request):
        raise TypeError(f"not a request: {request!r}")
    fields: dict[str, Any] = {}
    for name, hint in _hints(type(request)).items():
        value = _check(name, getattr(request, name), hint)
        fields[name] = list(value) if isinstance(value, tuple) else value
    return {"command": request.COMMAND, "version": request.VERSION, "fields": fields}


def request_to_text(request: Request) -> str:
    """Return the one-line JSON text form of *request* (sorted keys, no spaces)."""
    return json.dumps(
        request_to_mapping(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def request_from_mapping(data: Any, cls: type[Request]) -> Request:
    """Build a request of type *cls* from its text form *data*.

    The caller has already matched ``data["command"]`` to *cls*.
    """
    if not isinstance(data, dict):
        raise RequestError("a request must be a JSON object")
    extra = set(data) - {"command", "version", "fields"}
    if extra:
        raise RequestError(f"unknown request key(s): {sorted(extra)}")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != cls.VERSION:
        raise RequestError(
            f"{cls.COMMAND!r} request version {version!r} is not supported "
            f"(this Tether reads version {cls.VERSION})"
        )
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise RequestError("a request needs a 'fields' object")
    hints = _hints(cls)
    unknown = set(fields) - set(hints)
    if unknown:
        raise RequestError(f"unknown field(s) for {cls.COMMAND!r}: {sorted(unknown)}")
    values: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in fields:
            values[f.name] = _check(f.name, fields[f.name], hints[f.name])
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise RequestError(f"missing field {f.name!r} for {cls.COMMAND!r}")
    return cls(**values)
