#!/usr/bin/env python3
"""Reference day-14 exposure forecaster.

For each subject: build covariate-adjusted typical parameters from the population model
saved beside this file (pop_params.json), fit the subject's random effects by MAP
against its own samples under a censored (M3) likelihood, then integrate the forecast
window [t14, t14 + H) with the subject's actual dose rows - including any titration step
after the sampling period, which the concentration-driven enzyme model carries forward.

  --horizon-h H          window length in hours, 0 < H <= 168 (default 24)
  --estimator map        per-subject MAP fit (default)
  --estimator population no random effects: dose rows and covariates only

Reads only --input-dir and this directory; writes only --output.
"""
import argparse
import csv
import json
import math
import os
import sys
from multiprocessing import Pool

import numpy as np
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pk_engine as E  # noqa: E402

DEFAULT_WEIGHT_KG = 70.0
DEFAULT_AGE_YEARS = 45.0
DEFAULT_SEX = "M"
MAX_HORIZON_H = 168.0
ESTIMATORS = ("map", "population")
FIT_STEP = 0.25
OUT_STEP = 0.02
FLOOR = 1e-9


def parse_horizon(raw):
    try:
        h = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"--horizon-h must be a number, got {raw!r}")
    if not math.isfinite(h) or h <= 0.0 or h > MAX_HORIZON_H:
        raise ValueError(f"--horizon-h must be finite with 0 < H <= {MAX_HORIZON_H:g}, got {raw!r}")
    return h


def parse_estimator(raw):
    if raw not in ESTIMATORS:
        raise ValueError(f"--estimator must be exactly 'map' or 'population', got {raw!r}")
    return raw


def usable_number(raw):
    try:
        v = float((raw or "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0.0 else None


def resolve_covariates(row):
    """(weight, age, sex), each falling back to its own default when unusable."""
    if row is None:
        return DEFAULT_WEIGHT_KG, DEFAULT_AGE_YEARS, DEFAULT_SEX
    wt = usable_number(row.get("body_weight_kg"))
    age = usable_number(row.get("age_years"))
    sex = row.get("sex")
    return (wt if wt is not None else DEFAULT_WEIGHT_KG,
            age if age is not None else DEFAULT_AGE_YEARS,
            sex if sex in ("M", "F") else DEFAULT_SEX)


def typical_for(model, wt, age, sex):
    th, cv = model["typical"], model["covariates"]
    fem = sex == "F"
    return {
        "ka": th["ka"], "tlag": th["tlag"], "emax": th["emax"], "ec50": th["ec50"],
        "kenz": th["kenz"],
        "V": th["V"] * (wt / 70.0) * (cv["sex_v_f"] if fem else 1.0),
        "cl0": (th["cl0"] * (wt / 70.0) ** 0.75 * (age / 45.0) ** cv["age_cl_exp"]
                * (cv["sex_cl_f"] if fem else 1.0)),
    }


def forecast(job):
    model, events, samples, cov_row, estimator, horizon = job
    p0 = typical_for(model, *resolve_covariates(cov_row))
    eta = np.zeros(len(E.PARAMS))
    if estimator == "map" and samples:
        samples = sorted(samples)
        t = [s[0] for s in samples]
        obs = np.array([s[1] for s in samples])
        cens = np.array([s[2] for s in samples], dtype=int)
        sd = np.array([model["iiv_sd"][k] for k in E.PARAMS])

        def res(e):
            p = {k: p0[k] * math.exp(v) for k, v in zip(E.PARAMS, e)}
            pred = E.predict_at(p, events, t, FIT_STEP)
            return np.concatenate([
                E.residuals(pred, obs, cens, model["lloq"], model["sigma_prop"], model["sigma_add"]),
                e / sd])

        eta = least_squares(res, eta, method="trf", x_scale=sd, max_nfev=80).x
    p = {k: p0[k] * math.exp(v) for k, v in zip(E.PARAMS, eta)}
    return E.window_exposures(p, events, horizon, OUT_STEP)


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--horizon-h", default="24.0")
    ap.add_argument("--estimator", default="map")
    args = ap.parse_args()
    try:
        horizon = parse_horizon(args.horizon_h)
        estimator = parse_estimator(args.estimator)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    with open(os.path.join(HERE, "pop_params.json")) as fh:
        model = json.load(fh)

    d = args.input_dir
    subjects = [r["subject_id"] for r in read(os.path.join(d, "subjects.csv"))]
    doses, samples = {}, {}
    for r in read(os.path.join(d, "doses.csv")):
        doses.setdefault(r["subject_id"], []).append(
            (float(r["dose_time_h"]), float(r["dose_mg"]), int(float(r["administered"]))))
    if estimator == "map":
        for r in read(os.path.join(d, "samples.csv")):
            samples.setdefault(r["subject_id"], []).append(
                (float(r["sample_time_h"]), float(r["concentration_ng_ml"]),
                 int(float(r["censored"]))))
    cov = {}
    for r in read(os.path.join(d, "covariates.csv")):
        cov.setdefault(r["subject_id"], r)

    jobs = [(model, doses[sid], samples.get(sid, []), cov.get(sid), estimator, horizon)
            for sid in subjects if doses.get(sid)]
    with Pool(min(4, max(1, len(jobs)))) as pool:
        results = iter(pool.map(forecast, jobs))

    rows = []
    for sid in subjects:
        vals = next(results) if doses.get(sid) else (FLOOR, FLOOR, FLOOR)
        rows.append([sid] + [f"{v:.10g}" for v in vals])
    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"])
        w.writerows(rows)


if __name__ == "__main__":
    main()
