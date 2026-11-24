#!/usr/bin/env python3
"""Fit population pharmacokinetic priors from the visible rich cohort.

Censored-likelihood one-compartment oral model with Emax autoinduction of apparent
clearance. Samples flagged censored=1 contribute their below-LLOQ likelihood (the
observation was only known to be under 1.00 ng/mL) rather than being dropped --
this is the signal a time-invariant, BLQ-dropping fit throws away.

Two stages:
  1. pooled fit of the typical values (ka, V, CL0, Emax, ET50) with a censored
     likelihood over every rich subject;
  2. empirical-Bayes estimates of the inter-individual SDs: per-subject MAP etas
     are fitted against those typical values and their SDs are recorded as priors.

Writes a JSON parameter artifact consumed by predict.py. Reads /app/data only; no
network, no randomness (deterministic given the input files).
"""
import csv
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import least_squares

LLOQ = 1.0
HILL = 1.6
DATA = "/app/data/rich"
DT = 0.1
SEX_V = {"M": 1.06, "F": 0.95}
SEX_CL = {"M": 1.05, "F": 0.95}
PARAM_NAMES = ("ka", "V", "cl0", "emax", "et50")
WEIGHT_CL_EXP = 0.75


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load(data_dir):
    doses = {}
    for r in read(data_dir + "/doses.csv"):
        doses.setdefault(r["subject_id"], []).append(
            (float(r["dose_time_h"]), float(r["dose_mg"]), int(float(r["administered"]))))
    samples = {}
    for r in read(data_dir + "/samples.csv"):
        samples.setdefault(r["subject_id"], []).append(
            (float(r["sample_time_h"]), float(r["concentration_ng_ml"]),
             int(float(r["censored"]))))
    cov = {r["subject_id"]: r for r in read(data_dir + "/covariates.csv")}
    return doses, samples, cov


def simulate(ka, V, cl0, emax, et50, events, t_end, dt=DT):
    n = int(round(t_end / dt)) + 1
    grid = np.arange(n) * dt
    conc = np.empty(n)
    conc[0] = 0.0
    agut = ac = 0.0
    at = {}
    for th, dm, adm in events:
        if adm:
            j = int(round(th / dt))
            if 0 <= j < n:
                at[j] = at.get(j, 0.0) + dm
    for i in range(n - 1):
        if i in at:
            agut += at[i]
        tm = max(grid[i] + 0.5 * dt, 0.0)
        tn = tm ** HILL
        k = cl0 * (1.0 + emax * tn / (et50 ** HILL + tn)) / V
        eka = math.exp(-ka * dt)
        ek = math.exp(-k * dt)
        if abs(k - ka) > 1e-9:
            ac_new = ac * ek + ka * agut * (eka - ek) / (k - ka)
        else:
            ac_new = ac * ek + ka * agut * dt * eka
        agut, ac = agut * eka, ac_new
        conc[i + 1] = ac / V * 1000.0
    return grid, conc


def censored_residual(pred, conc, censored):
    pred = max(pred, 1e-9)
    if censored:
        # A BLQ observation means only that the TRUE concentration was below LLOQ. Under a
        # squared-error objective the one-sided contribution is the part of the prediction
        # that violates that bound: penalise pred > LLOQ (max, positive) and leave pred <= LLOQ
        # unpenalised (0). Using min() here would be inverted - it would reward a prediction
        # above LLOQ and drive every censored trough up, discarding the induction signal.
        return max(0.0, math.log(pred) - math.log(LLOQ))
    return math.log(pred) - math.log(max(conc, 1e-9))


def main():
    doses, samples, cov = load(DATA)
    ids = sorted(doses)

    def pooled_res(theta):
        ka, V, cl0, emax, et50 = np.exp(theta)
        out = []
        for sid in ids:
            ev = doses[sid]
            t_end = max(e[0] for e in ev) + 24.0
            g, c = simulate(ka, V, cl0, emax, et50, ev, t_end + 1.0)
            for ts, conc, cens in samples.get(sid, []):
                out.append(censored_residual(float(np.interp(ts, g, c)), conc, cens))
        return np.asarray(out)

    start = np.log([1.1, 300.0, 15.0, 1.2, 120.0])
    ka, V0, cl0, emax, et50 = np.exp(
        least_squares(pooled_res, start, max_nfev=120).x)

    # Stage 2: estimate the inter-individual SDs empirically. Each subject's etas are
    # fitted with WEAK priors, so the spread of the fitted etas reflects real between-
    # subject variability rather than being shrunk to the prior. The visible rich cohort
    # has dense day-1 and day-14 profiles, so these etas are well identified.
    seed_sd = np.array([3.0, 3.0, 3.0, 3.0, 3.0])
    etas = []
    for sid in ids:
        ev = doses[sid]
        w = float(cov[sid]["body_weight_kg"])
        sex = cov[sid]["sex"]
        Vt = V0 * (w / 70.0) * SEX_V[sex]
        CLt = cl0 * (w / 70.0) ** WEIGHT_CL_EXP * SEX_CL[sex]
        t_end = max(e[0] for e in ev) + 24.0
        pts = samples.get(sid, [])

        def resid(eta):
            g, c = simulate(ka * math.exp(eta[0]), Vt * math.exp(eta[2]),
                            CLt * math.exp(eta[1]), emax * math.exp(eta[3]),
                            et50 * math.exp(eta[4]), ev, t_end + 1.0)
            r = [censored_residual(float(np.interp(ts, g, c)), conc, cens)
                 for ts, conc, cens in pts]
            r.extend((eta / seed_sd).tolist())
            return np.asarray(r)

        try:
            etas.append(least_squares(resid, np.zeros(5), method="trf",
                                      max_nfev=40).x)
        except Exception:  # noqa: BLE001 - a single bad subject must not abort the fit
            continue
    eta_arr = np.asarray(etas)
    sd = eta_arr.std(axis=0) if len(eta_arr) > 1 else np.full(5, 0.2)
    # Keep the priors in a plausible physiological band; a wild subject must not blow them up.
    sd = np.clip(sd, 0.05, 0.6)

    artifact = {
        "typical": {"ka": float(ka), "V": float(V0), "cl0": float(cl0),
                    "emax": float(emax), "et50": float(et50)},
        "iiv_sd": {"ka": float(sd[0]), "cl0": float(sd[1]), "V": float(sd[2]),
                   "emax": float(sd[3]), "et50": float(sd[4])},
        "hill": HILL, "lloq": LLOQ,
    }
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/app/submission/pop_params.json"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
    print(f"wrote {out_path}: {json.dumps(artifact['typical'])}")


if __name__ == "__main__":
    main()
