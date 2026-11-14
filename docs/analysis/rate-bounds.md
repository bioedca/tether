# Rate constants with error bounds

**Who this page is for.** Anyone who has an HMM model in a Tether project and wants its rate
constants with a lower and an upper bound, as Campos-Chavez et al. (2024) report them. It assumes
you know what a transition matrix is. It does not assume you know Tether's code.

## What it gives

An HMM model has a transition count matrix: for each state pair `i → j`, a count of the
transitions that the fit saw (the Dirichlet posterior of the fit). tMAVEN stores this matrix with
each model as `tmatrix`, and Tether stores it with each fitted or imported model under
`/idealization/{model}` ([the project store](../reference/tether-format.md)).

From this matrix and the frame time, Tether calculates for each state pair:

- the transition probability per frame,
- its variance,
- the rate constant, and
- a lower and an upper bound of the rate constant.

## The method

The method is the one of the Source Data of Campos-Chavez, Paul, Zhou, Alonso, Verma, Fei and
Mondragón, *Nature Communications* **15**:6592 (2024),
[doi:10.1038/s41467-024-50885-x](https://doi.org/10.1038/s41467-024-50885-x)
(the script `Kmatrix/CalculateKbounds.py`). With `C` the count matrix and `dt` the frame time:

1. Each row `i` of `C` is a Dirichlet distribution. Its sum is `a0 = C[i, 0] + … + C[i, N-1]`.
2. The transition probability is `p = C[i, j] / a0`.
3. Its variance is `var = p × (1 − p) / (a0 + 1)`.
4. The rate constant is `k = −ln(1 − p) / dt`. A first-order process with the rate `k` leaves
   its state in one frame with the probability `1 − exp(−k × dt)`.
5. The upper bound is the rate at `p + √var`. The lower bound is the rate at `p − √var`.

The bounds are not symmetric about `k`, because the logarithm is not linear. The upper bound is
farther from `k` than the lower bound.

The off-diagonal entries (`i ≠ j`) are the rate constants. Tether also returns the diagonal
entries, `−ln(1 − p_ii) / dt`, because the published script prints them. The diagonal entries are
**not** rate constants.

### Cases outside the published files

| Case | Result |
| --- | --- |
| A count of zero | `p = 0`, `var = 0`, the rate and the bounds are `0` |
| A row with a sum of zero | the whole row is `NaN` (no probability is defined) |
| A probability of 1 (the only count of its row) | `var = 0`, the rate and the bounds are `+inf` |
| `p + √var` is 1 or more | the upper bound is `+inf` |
| `p − √var` is less than 0 | the lower bound is `0` |

A normalized matrix (each row sums to 1) does not have the counts that the variance needs, so
Tether refuses it with a message. [ADR-0092](../adr/0092-dirichlet-rate-bounds-from-the-count-matrix.md)
records these choices.

## How to use it

From a stored model of a project, with a frame time of 100 ms:

```python
from tether.analysis.rate_bounds import model_rate_bounds

result = model_rate_bounds("experiment.tether", "vbconhmm", dt=0.1)
print(result.rates)   # 1/s when dt is in seconds
print(result.lower)
print(result.upper)
print(result.provenance)  # method, app version, time, dt, and the source model
```

From a count matrix that you already have, use `dirichlet_rate_bounds(counts, dt)`. The project
is only read. Nothing is written to it.

## How it is tested

The test reads the five count matrices of the Source Data (`Kmatrix/*.txt`) and the output that
the authors' script printed for each (`Kmatrix/*.out`). For every entry of the transition matrix,
the variance, the normalized matrix, the rates, the two bounds and the two bound differences,
Tether prints the same digits as the published file. These files are under CC BY-NC-ND 4.0, and
[Validation & benchmarks](../validation.md) gives their terms. The test is
`tests/test_rate_bounds.py`.

If you use these rate bounds in a publication, cite the article above as the source of the method,
and cite Tether as [Citing](../citing.md) shows.
