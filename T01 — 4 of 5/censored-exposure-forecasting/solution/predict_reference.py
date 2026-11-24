#!/usr/bin/env python3
"""Reference submission: induction-aware MAP individualization on the graded cohort.

Reads the population priors saved beside this file (pop_params.json), fits per-subject
MAP random effects against that subject's samples with a censored likelihood (BLQ
samples are left-censored observations, not dropped), then integrates the steady-state
window for AUCss / Cmaxss / Ctroughss.

The integration grid always carries ``t14`` and the window close ``t14 + horizon`` as
exact nodes, so a caller-supplied horizon (including a non-grid one such as 24.05)
reports the quantities of exactly ``[t14, t14+H)`` with the trough at exactly
``t14 + H`` - the window is never rounded to a fixed step.

Reads only --input-dir and its own directory; writes only --output.
"""
import argparse
import csv
import json
import math
import os

import numpy as np
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
SEX_V = {"M": 1.06, "F": 0.95}
SEX_CL = {"M": 1.05, "F": 0.95}
WEIGHT_CL_EXP = 0.75
DT = 0.1


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def node_grid(t_end, step=DT, marks=(0.0,)):
    """Nodes from 0 to ``t_end`` (both exact), split at every mark, sub-steps <= step."""
    cuts = sorted({0.0, float(t_end)} | {float(m) for m in marks if 0.0 <= float(m) <= float(t_end)})
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        span = b - a
        n = max(1, int(math.ceil(span / step - 1e-9)))
        part = a + np.arange(n + 1, dtype=float) * (span / n)
        parts.append(part[:-1])
    parts.append(np.array([float(t_end)]))
    return np.concatenate(parts)


def simulate(ka, V, cl0, emax, et50, hill, events, t_end, dt=DT, marks=(0.0,)):
    """Concentration on a node grid spanning [0, t_end] with the given marks as nodes."""
    grid = node_grid(t_end, dt, marks)
    n = grid.size
    conc = np.empty(n)
    conc[0] = 0.0
    # bucket each administered dose onto the first node at or after its actual time
    at = {}
    for th, dm, adm in events:
        if not adm:
            continue
        j = int(np.searchsorted(grid, th, side="left"))
        if 0 <= j < n:
            at[j] = at.get(j, 0.0) + dm
    agut = ac = 0.0
    for i in range(n - 1):
        if i in at:
            agut += at[i]
        ds = float(grid[i + 1] - grid[i])
        tm = max(float(grid[i]) + 0.5 * ds, 0.0)
        tn = tm ** hill
        k = cl0 * (1.0 + emax * tn / (et50 ** hill + tn)) / V
        eka = math.exp(-ka * ds)
        ek = math.exp(-k * ds)
        if abs(k - ka) > 1e-9:
            ac_new = ac * ek + ka * agut * (eka - ek) / (k - ka)
        else:
            ac_new = ac * ek + ka * agut * ds * eka
        agut, ac = agut * eka, ac_new
        conc[i + 1] = ac / V * 1000.0
    return grid, conc


def exposures(ka, V, cl0, emax, et50, hill, events, horizon=24.0):
    """AUC / Cmax / Ctrough of the window ``[t14, t14 + horizon)``.

    ``t14`` is the largest dose time over every planned dose row (administered or not);
    the window close is exactly ``t14 + horizon``. Both ends are integration nodes and
    the AUC covers the whole window, so the trough is the concentration at exactly
    ``t14 + horizon``.
    """
    t14 = max(e[0] for e in events)
    t_end = t14 + float(horizon)
    g, c = simulate(ka, V, cl0, emax, et50, hill, events, t_end, marks=(t14,))
    m = (g >= t14 - 1e-9) & (g <= t_end + 1e-9)
    auc = float(np.trapezoid(c[m], g[m]))
    cmax = float(np.max(c[m]))
    ctrough = float(c[-1])
    return max(auc, 1e-9), max(cmax, 1e-9), max(ctrough, 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(os.path.join(HERE, "pop_params.json")) as fh:
        art = json.load(fh)
    typ = art["typical"]
    sd = art["iiv_sd"]
    hill = art.get("hill", 1.6)
    lloq = art.get("lloq", 1.0)
    sd_vec = np.array([sd["ka"], sd["cl0"], sd["V"], sd["emax"], sd["et50"]])

    subjects = read(args.input_dir + "/subjects.csv")
    doses = {}
    for r in read(args.input_dir + "/doses.csv"):
        doses.setdefault(r["subject_id"], []).append(
            (float(r["dose_time_h"]), float(r["dose_mg"]), int(float(r["administered"]))))
    samples = {}
    for r in read(args.input_dir + "/samples.csv"):
        samples.setdefault(r["subject_id"], []).append(
            (float(r["sample_time_h"]), float(r["concentration_ng_ml"]),
             int(float(r["censored"]))))
    cov = {r["subject_id"]: r for r in read(args.input_dir + "/covariates.csv")}

    rows = []
    for s in subjects:
        sid = s["subject_id"]
        ev = doses.get(sid, [])
        if not ev:
            rows.append([sid, "nan", "nan", "nan"])
            continue
        w = float(cov[sid]["body_weight_kg"])
        sex = cov[sid]["sex"]
        Vt = typ["V"] * (w / 70.0) * SEX_V.get(sex, 1.0)
        CLt = typ["cl0"] * (w / 70.0) ** WEIGHT_CL_EXP * SEX_CL.get(sex, 1.0)
        t_end = max(e[0] for e in ev) + 24.0
        pts = samples.get(sid, [])

        def resid(eta):
            g, c = simulate(typ["ka"] * math.exp(eta[0]), Vt * math.exp(eta[2]),
                            CLt * math.exp(eta[1]), typ["emax"] * math.exp(eta[3]),
                            typ["et50"] * math.exp(eta[4]), hill, ev, t_end + 1.0)
            out = []
            for ts, conc, cens in pts:
                pred = max(float(np.interp(ts, g, c)), 1e-9)
                if cens:
                    # BLQ = only known under LLOQ: penalise pred above it, not below (see
                    # fit_population.censored_residual - min() here would be inverted).
                    out.append(max(0.0, math.log(pred) - math.log(lloq)))
                else:
                    out.append(math.log(pred) - math.log(max(conc, 1e-9)))
            out.extend((eta / sd_vec).tolist())
            return np.asarray(out)

        eta = least_squares(resid, np.zeros(5), method="trf", max_nfev=40).x
        A, CM, CT = exposures(typ["ka"] * math.exp(eta[0]), Vt * math.exp(eta[2]),
                              CLt * math.exp(eta[1]), typ["emax"] * math.exp(eta[3]),
                              typ["et50"] * math.exp(eta[4]), hill, ev)
        rows.append([sid, f"{A:.6f}", f"{CM:.6f}", f"{CT:.6f}"])

    with open(args.output, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"])
        writer.writerows(rows)


if __name__ == "__main__":
    main()
