#!/usr/bin/env python3
"""Calibrate the population pharmacokinetic model on visible historical trial cohorts.

Formulates a one-compartment oral absorption structure with absorption delay and
concentration-mediated enzyme autoinduction: CL(t) = CL0 * E(t), where enzyme turnover
follows dE/dt = kenz * (1 + Emax * C / (EC50 + C)) - kenz * E. Fixed-effect covariates
incorporate standard allometric scaling for weight, power scaling for age, and categorical
factors for sex.

Concentrations falling below the quantification limit (LLOQ) are handled via Beal's M3
censored-likelihood formulation without data exclusion or ad-hoc replacement.

Estimation procedure:
  1. Pooled initialization across rich cohort profiles.
  2. Iterative two-stage refinement: calculates MAP subject estimates, then updates
     fixed-effect population parameters, inter-individual variance, and residual error.

Usage: fit_population.py OUT.json [DATA_DIR]
"""
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

DATA = sys.argv[2] if len(sys.argv) > 2 else "/app/data"
LLOQ = 2.50   # ng/mL, the assay limit stated in the task
FIT_STEP = 0.25
N_ITS = 4
#: Residual error assumed for the first pass, before it is estimated from the fits.
START_SIGMA = (0.15, 0.3)


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load(cohort):
    d = os.path.join(DATA, cohort)
    doses, pts = {}, {}
    for r in read(os.path.join(d, "doses.csv")):
        doses.setdefault(r["subject_id"], []).append(
            (float(r["dose_time_h"]), float(r["dose_mg"]), int(float(r["administered"]))))
    for r in read(os.path.join(d, "samples.csv")):
        pts.setdefault(r["subject_id"], []).append(
            (float(r["sample_time_h"]), float(r["concentration_ng_ml"]), int(float(r["censored"]))))
    cov = {r["subject_id"]: r for r in read(os.path.join(d, "covariates.csv"))}
    subjects = []
    for sid in sorted(doses):
        c = cov[sid]
        s = sorted(pts.get(sid, []))
        subjects.append({
            "sid": sid, "events": doses[sid], "wt": float(c["body_weight_kg"]),
            "age": float(c["age_years"]), "sex": c["sex"],
            "t": [x[0] for x in s], "obs": np.array([x[1] for x in s]),
            "cens": np.array([x[2] for x in s], dtype=int),
        })
    return subjects


def typical_for(model, wt, age, sex):
    """Covariate-adjusted typical parameters for one subject."""
    th, cv = model["typical"], model["covariates"]
    fem = sex == "F"
    return {
        "ka": th["ka"], "tlag": th["tlag"], "emax": th["emax"], "ec50": th["ec50"],
        "kenz": th["kenz"],
        "V": th["V"] * (wt / 70.0) * (cv["sex_v_f"] if fem else 1.0),
        "cl0": (th["cl0"] * (wt / 70.0) ** 0.75 * (age / 45.0) ** cv["age_cl_exp"]
                * (cv["sex_cl_f"] if fem else 1.0)),
    }


def with_eta(p, eta):
    return {k: p[k] * math.exp(e) for k, e in zip(E.PARAMS, eta)}


def map_fit(args):
    """Per-subject MAP random effects under the current population model."""
    subj, model = args
    sd = np.array([model["iiv_sd"][k] for k in E.PARAMS])
    sp, sa = model["sigma_prop"], model["sigma_add"]
    p0 = typical_for(model, subj["wt"], subj["age"], subj["sex"])

    def res(eta):
        pred = E.predict_at(with_eta(p0, eta), subj["events"], subj["t"], FIT_STEP)
        return np.concatenate([E.residuals(pred, subj["obs"], subj["cens"], model["lloq"], sp, sa),
                               eta / sd])

    fit = least_squares(res, np.zeros(7), method="trf", x_scale=sd, max_nfev=80)
    pred = E.predict_at(with_eta(p0, fit.x), subj["events"], subj["t"], FIT_STEP)
    m = subj["cens"] == 0
    return fit.x, pred[m], subj["obs"][m]


def pooled_fit(subjects, lloq):
    """Naive pooled censored fit: log typical values plus age exponent and sex factors."""
    names = list(E.PARAMS) + ["age_cl_exp", "log_sex_cl_f", "log_sex_v_f"]
    start = np.array([math.log(v) for v in (1.0, 0.3, 300.0, 15.0, 5.0, 20.0, 0.01)] + [0.0, 0.0, 0.0])

    def unpack(x):
        model = {"typical": {k: math.exp(v) for k, v in zip(E.PARAMS, x[:7])},
                 "covariates": {"age_cl_exp": x[7], "sex_cl_f": math.exp(x[8]),
                                "sex_v_f": math.exp(x[9])}}
        return model

    def res(x):
        model = unpack(x)
        out = []
        for s in subjects:
            p = typical_for(model, s["wt"], s["age"], s["sex"])
            pred = E.predict_at(p, s["events"], s["t"], 0.5)
            out.append(E.residuals(pred, s["obs"], s["cens"], lloq, *START_SIGMA))
        return np.concatenate(out)

    lo = np.array([math.log(v) for v in (0.05, 0.01, 30.0, 1.0, 0.05, 0.5, 1e-4)] + [-2.0, -1.0, -1.0])
    hi = np.array([math.log(v) for v in (10.0, 3.0, 3000.0, 150.0, 100.0, 1000.0, 0.5)] + [2.0, 1.0, 1.0])
    x = least_squares(res, start, bounds=(lo, hi), method="trf", max_nfev=60).x
    print("pooled:", dict(zip(names, np.round(x, 3))), file=sys.stderr)
    return unpack(x)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/app/submission/pop_params.json"
    rich, sparse = load("rich"), load("sparse")
    lloq = LLOQ

    model = pooled_fit(rich, lloq)
    model.update({"iiv_sd": {k: 0.5 for k in E.PARAMS},
                  "sigma_prop": START_SIGMA[0], "sigma_add": START_SIGMA[1], "lloq": lloq})

    everyone = rich + sparse
    for it in range(N_ITS):
        with Pool(4) as pool:
            fits = pool.map(map_fit, [(s, model) for s in everyone])
        etas = np.array([f[0] for f in fits])

        # Typical values move by the mean random effect; covariate effects on CL0 and V
        # are refreshed by regressing their random effects on log(age/45) and sex.
        th = dict(model["typical"])
        for j, k in enumerate(E.PARAMS):
            if k not in ("cl0", "V"):
                th[k] *= math.exp(float(etas[:, j].mean()))
        cv = dict(model["covariates"])
        fem = np.array([1.0 if s["sex"] == "F" else 0.0 for s in everyone])
        lage = np.array([math.log(s["age"] / 45.0) for s in everyone])
        j_cl, j_v = E.PARAMS.index("cl0"), E.PARAMS.index("V")
        X = np.column_stack([np.ones(len(everyone)), lage, fem])
        b_cl = np.linalg.lstsq(X, etas[:, j_cl], rcond=None)[0]
        th["cl0"] *= math.exp(b_cl[0])
        cv["age_cl_exp"] += b_cl[1]
        cv["sex_cl_f"] *= math.exp(b_cl[2])
        b_v = np.linalg.lstsq(X[:, [0, 2]], etas[:, j_v], rcond=None)[0]
        th["V"] *= math.exp(b_v[0])
        cv["sex_v_f"] *= math.exp(b_v[1])
        resid_eta = etas.copy()
        resid_eta[:, j_cl] -= X @ b_cl
        resid_eta[:, j_v] -= X[:, [0, 2]] @ b_v

        # Between-subject SDs from the rich fits only (dense data, little shrinkage), so
        # the priors are not pulled toward zero by the barely informed sparse subjects.
        n_rich = len(rich)
        sd = np.clip(resid_eta[:n_rich].std(axis=0) * 1.15, 0.08, 0.8)

        # Residual error: least-squares fit of the combined error model to the measured
        # samples' squared residuals.
        pred = np.concatenate([f[1] for f in fits])
        obs = np.concatenate([f[2] for f in fits])
        A = np.column_stack([pred ** 2, np.ones_like(pred)])
        coef = np.linalg.lstsq(A, (obs - pred) ** 2, rcond=None)[0]
        model.update({
            "typical": th, "covariates": cv,
            "iiv_sd": {k: float(v) for k, v in zip(E.PARAMS, sd)},
            "sigma_prop": float(np.clip(math.sqrt(max(coef[0], 1e-6)), 0.05, 0.4)),
            "sigma_add": float(np.clip(math.sqrt(max(coef[1], 1e-6)), 0.05, 1.5)),
        })
        print(f"iteration {it + 1}: " + json.dumps(
            {k: round(v, 4) for k, v in th.items()}), file=sys.stderr)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(model, fh, indent=2, sort_keys=True)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
