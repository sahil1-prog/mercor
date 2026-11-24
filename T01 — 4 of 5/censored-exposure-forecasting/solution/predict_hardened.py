#!/usr/bin/env python3
"""Hardened reference submission.

Extends the induction-aware MAP predictor (``predict_reference.py``, shipped unmodified
beside this file) with:

  * graceful degradation on incomplete covariate data - a subject with no covariate row,
    or with an unusable ``body_weight_kg`` / ``sex`` value, falls back per field to the
    documented defaults (70 kg, sex M) instead of aborting the whole run;
  * an exact-string ``--estimator`` contract (only ``map`` and ``population`` are
    accepted; no case or whitespace normalisation) and an exact window semantics for
    ``--horizon-h`` (the trough is the concentration at exactly ``t14 + H``);
  * a ``population`` strategy that fits no individual random effects, so a subject's
    forecast is a function of its dose rows and covariates only.

Reads only --input-dir and its own directory; writes only --output.
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from predict_reference import (  # noqa: E402  (numeric engine, shipped unmodified)
    SEX_CL, SEX_V, WEIGHT_CL_EXP, exposures, read, simulate,
)

DEFAULT_WEIGHT_KG = 70.0
DEFAULT_SEX = "M"
DEFAULT_HORIZON_H = 24.0
#: Widest forecast horizon the option accepts (the study runs 14 daily doses).
MAX_HORIZON_H = 168.0
DEFAULT_ESTIMATOR = "map"
ESTIMATORS = ("map", "population")
POSITIVE_FLOOR = 1e-9


def parse_estimator(raw):
    """Validate --estimator by exact string equality; raise ValueError otherwise.

    The contract names two literal strings. ``MAP``, `` map `` and ``Population`` are
    not those strings, so they are rejected - no stripping, no case folding.
    """
    if raw not in ESTIMATORS:
        raise ValueError(f"estimator must be exactly one of {ESTIMATORS}, got {raw!r}")
    return raw


def population_etas():
    """Random effects for the population strategy: no individual adjustment (all zero).

    In population mode a subject's forecast depends only on its dose events and covariates,
    never on its measured samples, so two subjects with identical dosing and covariates but
    different observations receive identical forecasts.
    """
    return np.zeros(5)


def parse_horizon(raw):
    """Validate --horizon-h: a finite, positive number of hours in (0, MAX_HORIZON_H].

    Raises ValueError for anything outside that range (including NaN/inf) so main() can
    fail cleanly rather than emit a silently wrong window.
    """
    try:
        h = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"horizon must be a number, got {raw!r}")
    if not math.isfinite(h) or h <= 0.0:
        raise ValueError(f"horizon must be a finite positive number, got {raw!r}")
    if h > MAX_HORIZON_H:
        raise ValueError(f"horizon must be <= {MAX_HORIZON_H} h, got {h}")
    return h


def resolve_covariates(row):
    """(body_weight_kg, sex) with documented per-field fallbacks for unusable values.

    A missing row, a blank or non-numeric weight, or a sex that is not literally ``M`` or
    ``F`` falls back to the default for THAT field only; the other field is still honoured
    when it is usable. The sex comparison is literal: ``f`` is not ``F`` and falls back.
    """
    weight = DEFAULT_WEIGHT_KG
    sex = DEFAULT_SEX
    if row is not None:
        raw_w = (row.get("body_weight_kg") or "").strip()
        try:
            w = float(raw_w)
        except (TypeError, ValueError):
            w = None
        if w is not None and math.isfinite(w) and w > 0.0:
            weight = w
        if row.get("sex") in ("M", "F"):
            sex = row["sex"]
    return weight, sex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--horizon-h", default=str(DEFAULT_HORIZON_H),
                    help="forecast window length in hours from t14; default 24")
    ap.add_argument("--estimator", default=DEFAULT_ESTIMATOR,
                    help="per-subject estimation strategy: map (default) or population")
    args = ap.parse_args()
    try:
        horizon_h = parse_horizon(args.horizon_h)
        estimator = parse_estimator(args.estimator)
    except ValueError as exc:
        # Invalid option value: fail before writing anything (no output file is produced).
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

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
            # No dosing information: the concentration is identically zero over any window.
            rows.append([sid, f"{POSITIVE_FLOOR:g}", f"{POSITIVE_FLOOR:g}",
                         f"{POSITIVE_FLOOR:g}"])
            continue
        w, sex = resolve_covariates(cov.get(sid))
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

        if estimator == "population":
            eta = population_etas()
        else:
            eta = least_squares(resid, np.zeros(5), method="trf", max_nfev=40).x
        A, CM, CT = exposures(typ["ka"] * math.exp(eta[0]), Vt * math.exp(eta[2]),
                              CLt * math.exp(eta[1]), typ["emax"] * math.exp(eta[3]),
                              typ["et50"] * math.exp(eta[4]), hill, ev,
                              horizon=horizon_h)
        rows.append([sid, f"{A:.6f}", f"{CM:.6f}", f"{CT:.6f}"])

    with open(args.output, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"])
        writer.writerows(rows)


if __name__ == "__main__":
    main()
