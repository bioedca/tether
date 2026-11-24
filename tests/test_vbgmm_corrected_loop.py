# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's corrected vbGMM loop for the sidecar key ``vbgmm`` (#1337, ADR-0139).

The pinned tMAVEN's plain vbGMM computes a lower bound that falls at every step, so its
relative-change test ends the fit at the second iteration. ``_sidecar_runner`` replaces that one
loop in the sidecar child with `vbgmm_outer_loop`, which keeps tMAVEN's start, priors and
updates and computes the bound of Bishop's (2006) equations 10.71 to 10.77 in one dimension.

The two functions need only NumPy and SciPy, so this base-tier module checks them without
tMAVEN:

* the bound is the expectation it claims to be, against a Monte Carlo estimate built from
  ``scipy.stats`` densities that share no formula with it;
* evaluated where the loop evaluates it, the bound never falls, and the loop runs on to its
  convergence test on the fixture's pooled FRET;
* the stopping rules and the returned values keep tMAVEN's contract.

The live fit against the independent scikit-learn reference (ADR-0138) is the sidecar test
``test_vbgmm_key_agrees_with_the_independent_reference``.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from _vbgmm_reference import TMAVEN_PRIORS, pooled_fret
from tether.idealize._sidecar_runner import vbgmm_lower_bound, vbgmm_outer_loop

#: tMAVEN's ``priors`` vector for ``vb_em_gmm``: beta, a, b, pi (``modeler.vbgmm.prior.*``).
PRIORS = np.array(
    [TMAVEN_PRIORS["beta"], TMAVEN_PRIORS["a"], TMAVEN_PRIORS["b"], TMAVEN_PRIORS["pi"]]
)


def _m_step(x, r, a0, b0, m0, beta0, alpha0):
    # tMAVEN's ``gmm_vb.m_updates`` (10f4230), written out again for the test.
    nk = r.sum(axis=0) + 1e-300
    xbar = (r * x[:, None]).sum(axis=0) / nk
    sk = (r * (x[:, None] - xbar) ** 2).sum(axis=0) / nk
    beta = beta0 + nk
    m = (beta0 * m0 + nk * xbar) / beta
    a = a0 + nk / 2.0
    b = b0 + 0.5 * (nk * sk + beta0 * nk / (beta0 + nk) * (xbar - m0) ** 2)
    alpha = alpha0 + nk
    return a, b, m, beta, alpha


def _monte_carlo_bound(x, r, a, b, m, beta, alpha, a0, b0, m0, beta0, alpha0, samples, seed):
    """E_q[ln p(x, Z, pi, mu, lambda)] - E_q[ln q] by sampling q(pi, mu, lambda).

    The expectation over Z is the exact sum over ``r``. Returns the four terms the bound reports
    (data, Normal-Gamma, Dirichlet, assignments) as (mean, standard error) pairs.
    """
    rng = np.random.default_rng(seed)
    k = m.size
    lam = rng.gamma(shape=a, scale=1.0 / b, size=(samples, k))
    mu = rng.normal(loc=m, scale=1.0 / np.sqrt(beta * lam))
    pi = rng.dirichlet(alpha, size=samples)

    sd = 1.0 / np.sqrt(lam)
    # sum_n sum_k r_nk ln N(x_n | mu_k, 1 / lambda_k), sample by sample.
    log_lik = stats.norm.logpdf(x[None, :, None], loc=mu[:, None, :], scale=sd[:, None, :])
    data = np.einsum("snk,nk->s", log_lik, r)

    ng = np.sum(
        stats.norm.logpdf(mu, loc=m0, scale=1.0 / np.sqrt(beta0 * lam))
        + stats.gamma.logpdf(lam, a0, scale=1.0 / b0)
        - stats.norm.logpdf(mu, loc=m, scale=1.0 / np.sqrt(beta * lam))
        - stats.gamma.logpdf(lam, a, scale=1.0 / b),
        axis=1,
    )
    dirichlet = stats.dirichlet(alpha0).logpdf(pi.T) - stats.dirichlet(alpha).logpdf(pi.T)
    assignments = np.log(pi) @ r.sum(axis=0) - np.sum(r * np.log(r))

    def summary(values):
        return float(values.mean()), float(values.std(ddof=1) / np.sqrt(samples))

    return [summary(v) for v in (data, ng, dirichlet, assignments)]


def test_the_bound_is_the_expectation_it_claims_to_be():
    rng = np.random.default_rng(1337)
    x = np.concatenate([rng.normal(-0.2, 0.1, 25), rng.normal(0.6, 0.15, 15)])
    k = 2
    r = rng.dirichlet(np.ones(k) * 2.0, size=x.size)
    a0, b0 = np.full(k, 2.5), np.full(k, 0.05)
    m0, beta0, alpha0 = np.array([-0.1, 0.5]), np.full(k, 0.25), np.full(k, 1.0)
    # Any parameters of the right form will do; these are an M step away from ``r``, then moved.
    a, b, m, beta, alpha = _m_step(x, r, a0, b0, m0, beta0, alpha0)
    m = m + np.array([0.03, -0.02])
    b = b * 1.1

    exact = vbgmm_lower_bound(x, r, a, b, m, beta, alpha, a0, b0, m0, beta0, alpha0)
    sampled = _monte_carlo_bound(
        x, r, a, b, m, beta, alpha, a0, b0, m0, beta0, alpha0, samples=100_000, seed=1337
    )
    names = ("data", "normal-gamma", "dirichlet", "assignments")
    for name, value, (mean, se) in zip(names, exact[1:], sampled, strict=True):
        assert se < 0.05, (name, se)
        assert abs(value - mean) < 5.0 * se + 1e-9, (name, value, mean, se)
    assert exact[0] == pytest.approx(np.sum(exact[1:]), abs=1e-9)


def _start(x, k):
    # A deterministic start in place of tMAVEN's k-means: quantile means, the data variance,
    # equal weights. The loop's prior is centred on these means, as tMAVEN's is on its start.
    mu = np.quantile(x, (np.arange(k) + 0.5) / k)
    return mu, np.full(k, x.var()), np.full(k, 1.0 / k)


def test_the_bound_never_falls_and_the_fit_reaches_its_convergence_test():
    x = pooled_fret()
    mu, var, ppi = _start(x, 3)
    maxiters = 5000
    r, a, b, m, beta, alpha, e_lnlam, e_lnpi, ll, iteration = vbgmm_outer_loop(
        x, mu, var, ppi, maxiters, 1e-8, PRIORS
    )
    bound = ll[: iteration + 1, 0]
    # tMAVEN's own bound fell at every step here and its fit ended at iteration 2 (#1337).
    assert iteration > 2
    assert iteration < maxiters - 1, "stopped at the iteration cap, not at the convergence test"
    steps = np.diff(bound)
    assert np.all(steps >= -1e-9 * np.abs(bound[1:])), steps.min()
    assert (bound[-1] - bound[-2]) / abs(bound[-2]) < 1e-8
    assert np.all(ll[iteration + 1 :] == 0.0)


def test_the_returned_parameters_belong_to_the_returned_responsibilities():
    x = pooled_fret()
    mu, var, ppi = _start(x, 3)
    r, a, b, m, beta, alpha, e_lnlam, e_lnpi, ll, iteration = vbgmm_outer_loop(
        x, mu, var, ppi, 200, 1e-8, PRIORS
    )
    k = mu.size
    a0, b0 = np.full(k, PRIORS[1]), np.full(k, PRIORS[2])
    beta0, alpha0 = np.full(k, PRIORS[0]), np.full(k, PRIORS[3])
    want = _m_step(x, r, a0, b0, mu, beta0, alpha0)
    for got, expected in zip((a, b, m, beta, alpha), want, strict=True):
        np.testing.assert_allclose(got, expected, rtol=1e-12)
    from scipy.special import digamma

    np.testing.assert_allclose(e_lnlam, digamma(a) - np.log(b), rtol=1e-12)
    np.testing.assert_allclose(e_lnpi, digamma(alpha) - digamma(alpha.sum()), rtol=1e-12)
    np.testing.assert_allclose(r.sum(axis=1), 1.0, atol=1e-9)
    assert ll[iteration, 0] == pytest.approx(
        vbgmm_lower_bound(x, r, a, b, m, beta, alpha, a0, b0, mu, beta0, alpha0)[0], abs=1e-9
    )


def test_the_stopping_rules_keep_tmavens_contract():
    x = pooled_fret()
    mu, var, ppi = _start(x, 2)
    # One iteration allowed: the bound of the start, and nothing after it.
    *_, ll, iteration = vbgmm_outer_loop(x, mu, var, ppi, 1, 1e-8, PRIORS)
    assert iteration == 0 and ll.shape == (1, 5) and ll[0, 0] != 0.0
    # The cap: rows 0..maxiters-1 are filled and ``iteration`` is the last one.
    *_, ll, iteration = vbgmm_outer_loop(x, mu, var, ppi, 4, 0.0, PRIORS)
    assert iteration == 3 and ll.shape == (4, 5) and np.all(ll[:, 0] != 0.0)
    # A threshold no step can meet ends the fit at the first test.
    *_, ll, iteration = vbgmm_outer_loop(x, mu, var, ppi, 100, np.inf, PRIORS)
    assert iteration == 1
    with pytest.raises(ValueError, match="maxiters"):
        vbgmm_outer_loop(x, mu, var, ppi, 0, 1e-8, PRIORS)
