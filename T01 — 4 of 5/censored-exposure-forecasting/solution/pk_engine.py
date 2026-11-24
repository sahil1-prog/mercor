"""Numeric core of the reference submission.

Oral absorption with a lag time into one central compartment, with apparent clearance
driven by an enzyme pool that is induced by the drug itself:

    dE/dt = kenz * (1 + Emax * C / (EC50 + C)) - kenz * E,   E(0) = 1,   CL = CL0 * E

This file is shared by fit_population.py (run once, offline, on /app/data) and by the
installed predictor. It never reads anything except what its callers pass in.
"""
import math

import numpy as np
from scipy.special import log_ndtr

PARAMS = ("ka", "tlag", "V", "cl0", "emax", "ec50", "kenz")


def node_grid(t_end, step, marks=()):
    """Nodes from 0 to ``t_end`` (both exact), every mark an exact node, sub-steps <= step."""
    cuts = sorted({0.0, float(t_end)} | {float(m) for m in marks if 0.0 <= float(m) <= float(t_end)})
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        n = max(1, int(math.ceil((b - a) / step - 1e-9)))
        parts.append(a + np.arange(n, dtype=float) * ((b - a) / n))
    parts.append(np.array([float(t_end)]))
    return np.concatenate(parts)


def dose_entries(events, tlag):
    """(time the dose reaches the gut, mg) for each administered dose row."""
    return [(t + tlag, mg) for t, mg, adm in events if adm]


def simulate(p, events, grid):
    """Concentration (ng/mL) at every node of ``grid`` for parameter dict ``p``.

    Doses land on the first node at or after their gut-entry time, so callers that need
    exact dose timing put the entry times in the grid's marks.
    """
    n = grid.size
    conc = np.zeros(n)
    at = {}
    for t_in, mg in dose_entries(events, p["tlag"]):
        j = int(np.searchsorted(grid, t_in - 1e-9, side="left"))
        if j < n:
            at[j] = at.get(j, 0.0) + mg
    ka, V, cl0 = p["ka"], p["V"], p["cl0"]
    emax, ec50, kenz = p["emax"], p["ec50"], p["kenz"]
    exp = math.exp
    agut = ac = 0.0
    enz = 1.0
    steps = np.diff(grid).tolist()
    for i, h in enumerate(steps):
        if i in at:
            agut += at[i]
        c_now = ac / V * 1000.0
        e_ss = 1.0 + emax * c_now / (ec50 + c_now)
        enz_new = e_ss + (enz - e_ss) * exp(-kenz * h)
        k = cl0 * 0.5 * (enz + enz_new) / V
        e_ka = exp(-ka * h)
        e_k = exp(-k * h)
        if abs(k - ka) > 1e-9:
            ac = ac * e_k + ka * agut * (e_ka - e_k) / (k - ka)
        else:
            ac = ac * e_k + ka * agut * h * e_ka
        agut *= e_ka
        enz = enz_new
        conc[i + 1] = ac / V * 1000.0
    return conc


def predict_at(p, events, times, step):
    """Concentrations at arbitrary sample times (each time is an exact node)."""
    times = list(times)
    if not times:
        return np.zeros(0)
    t_end = max(times) + 1e-6
    marks = times + [t for t, _ in dose_entries(events, p["tlag"])]
    grid = node_grid(t_end, step, marks)
    conc = simulate(p, events, grid)
    return np.interp(times, grid, conc)


def window_exposures(p, events, horizon, step=0.02):
    """AUC, Cmax and Ctrough of exactly ``[t14, t14 + horizon)``.

    t14 is the largest dose_time_h over every dose row, administered or not. The window
    ends are exact nodes, so the trough is the concentration at exactly t14 + horizon.
    """
    t14 = max(e[0] for e in events)
    t_end = t14 + float(horizon)
    marks = [t14, t_end] + [t for t, _ in dose_entries(events, p["tlag"])]
    grid = node_grid(t_end, step, marks)
    conc = simulate(p, events, grid)
    lo = int(np.searchsorted(grid, t14 - 1e-9))
    seg_t, seg_c = grid[lo:], conc[lo:]
    auc = float(np.trapezoid(seg_c, seg_t))
    return max(auc, 1e-9), max(float(seg_c.max()), 1e-9), max(float(conc[-1]), 1e-9)


def residuals(pred, obs, cens, lloq, sig_prop, sig_add):
    """Standardised residuals for least squares under a censored (M3) likelihood.

    A measured sample contributes (obs - pred) / sd(pred). A censored sample is only
    known to be below the LLOQ, so it contributes sqrt(-2 log P(y < LLOQ | pred)): zero
    when the prediction sits comfortably under the LLOQ, growing as it rises above it.
    """
    pred = np.maximum(pred, 1e-9)
    sd = np.sqrt((sig_prop * pred) ** 2 + sig_add ** 2)
    out = np.empty_like(pred)
    m = cens == 0
    out[m] = (obs[m] - pred[m]) / sd[m]
    if (~m).any():
        z = (lloq - pred[~m]) / sd[~m]
        out[~m] = np.sqrt(np.maximum(-2.0 * log_ndtr(z), 0.0))
    return out
