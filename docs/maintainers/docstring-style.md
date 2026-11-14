# Writing docstrings

**Who this page is for.** Anyone who writes or reviews Python code in `src/tether`. You want to
write a docstring that a script author can use and that the API reference can show. The page
assumes that you can read Python. It needs no write access to `bioedca/tether`.

Tether uses the **NumPy docstring style**.
[ADR-0093](../adr/0093-numpy-docstrings-and-mkdocstrings-cross-references.md) records this
decision and its reasons. The API reference is generated from the docstrings, so
the description and the code change in the same commit. The [writing style
guide](writing-style.md) gives the rules for the words. This page gives the rules for the
shape.

## Rule 1. A docstring says what. A comment says why.

- Write in the docstring **what** the object does and **how** to call it. The reader is a
  person who calls the code.
- Write in a comment **why** the code is written as it is. The reader is a person who changes
  the code.
- Do not put the history of a change in a docstring. Put it in the commit message or in an
  ADR.

## Rule 2. The summary line

- Write one sentence on the first line, directly after the opening quotes.
- Use the imperative: "Return the dwell times", not "Returns the dwell times".
- Keep the sentence on one line, and end it with a period.
- Put one empty line after the summary when more text follows.

## Rule 3. The sections and their order

Write the sections that apply, in this order. Underline each heading with hyphens of the same
length.

1. `Parameters`
2. `Returns` (or `Yields` for a generator)
3. `Raises`
4. `Notes`
5. `Examples`

Under `Parameters`, give each parameter on its own line as `name : type`. Under that line,
indent the meaning by four spaces. Give the default value when there is one. Under `Returns`,
give the type and the meaning of each value. Under `Raises`, give each exception and the
condition that raises it.

## Rule 4. The unit of each physical quantity

Give the unit of each parameter, return value and attribute that is a physical quantity. Use
these units:

| Quantity | Unit to write |
|---|---|
| time | `frames` or `seconds` |
| position and distance | `pixels` |
| intensity | `counts` (camera counts) |
| rate | `1 / frames` or `1 / seconds` |
| FRET efficiency | no unit (write "unitless") |

A time axis in frames can be published as seconds by mistake. The docstring is where a script
author learns which one a value holds. When a function converts between the two, name the
parameter that sets the frame time, and give its unit.

## Rule 5. When an example is necessary

Give an `Examples` section for each public function, class and method that a user calls from a
script. The how-to guides, such as [Extract traces](../how-to/extract-traces.md), show the same
calls. An example starts each line with `>>>` and shows the result that the call returns.

An internal helper does not need an example. A private name (a name that starts with `_`) does
not need a docstring.

## Rule 6. Cross-references

Write a link to another Tether object as a Markdown reference link, the form of
[mkdocstrings](https://mkdocstrings.github.io/) with its Python handler:

- ``[`load_project`][]`` links a name that the module defines or imports. The generator finds
  the name as Python finds it.
- ``[`Movie`][tether.io.movie.Movie]`` links an object by its full path, and shows the text in
  the first brackets.
- ``[`ValueError`][]`` links a builtin name.

Do not write a Sphinx role such as ``:func:`load_project` ``. A Markdown generator shows it as
plain text. `tests/test_docstring_standard.py` fails on a Sphinx role in `src/tether`.

## Rule 7. The words

The rules of the [writing style guide](writing-style.md) that apply to a docstring are these:

- Write short sentences (rule 2).
- Use the active voice and the simple present (rule 3).
- Use American spelling, and do not use idiom (rule 4).
- Use the approved name of each term in the [term list](writing-style.md#term-list) (rule 5).

## A complete example

The function below is an example. It is not part of Tether.

```python
def dwell_times_seconds(path: np.ndarray, frame_time: float) -> dict[int, np.ndarray]:
    """Return the dwell time of each state in a Viterbi path, in seconds.

    A dwell is a run of frames in one state. The first and the last dwell of the path are
    left out, because the path does not show where they start or end.

    Parameters
    ----------
    path : numpy.ndarray of int
        The state of each frame. A frame with no state holds
        [`NO_STATE`][tether.idealize.driver.NO_STATE].
    frame_time : float
        The time between two frames, in seconds. It must be greater than zero.

    Returns
    -------
    dict of int to numpy.ndarray of float
        For each state, the dwell times in seconds, in the order of the path.

    Raises
    ------
    ValueError
        If `frame_time` is zero or less.

    Notes
    -----
    The function counts frames first and multiplies by `frame_time` last, so the result
    has no rounding error from the time axis.

    Examples
    --------
    >>> dwell_times_seconds(np.array([0, 1, 1, 1, 0, 0, 1]), frame_time=0.1)
    {1: array([0.3]), 0: array([0.2])}
    """
```

## How the standard is held

Ruff checks the shape of each docstring in `src/tether` with its `D` rules and the NumPy
convention (`[tool.ruff.lint.pydocstyle]` in `pyproject.toml`). A file that broke a rule when
the check was turned on is in the allowlist under `[tool.ruff.lint.per-file-ignores]`. The
allowlist can only become shorter:

- `tests/test_docstring_standard.py` fails when the allowlist has an entry that `main` does
  not have.
- It also fails when an entry no longer hides a violation. Remove that entry in the same pull
  request that fixes the docstring.

Ruff cannot check the units (rule 4), the examples (rule 5) or the words (rule 7). Read your
docstrings against those rules before you ask for a review.
