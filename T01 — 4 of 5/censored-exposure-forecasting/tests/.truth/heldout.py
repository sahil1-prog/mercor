"""Deterministically regenerate the graded held-out PK cohort.

This module is the verifier's copy of the generative process. It is root-only
under ``tests/`` and is never shipped to the solver image. It mirrors the
authoring simulator byte for byte (``truth_model.py``) so the truth the grader
computes is the same process that produced the visible cohorts - but it uses a
DIFFERENT secret seed and the sparse (no day-14 profile) sampling design.

The cohort is written into a fresh directory as four label-stripped input CSVs
plus ``truth.csv`` (the held-out answers, which never leave this root-only dir).
"""

from __future__ import annotations

import csv
import math
import os
import random

import numpy as np

import truth_model as m

#: Secret seed for the graded cohort. Fixed, never derived from anything the
#: agent controls, and never present in the solver image.
SECRET_SEED = 8675309
#: Unseen subjects in the graded cohort.
N_HELDOUT = 150

INPUT_FILES = ("subjects.csv", "doses.csv", "samples.csv", "covariates.csv")
TRUTH_FILE = "truth.csv"
#: Forecast windows the grader asks for besides the default 24 h. Each gets its own
#: root-only truth file so a submission's values are compared with the truth of the exact
#: window it was asked for. None is a multiple of a plausible integration step, so a
#: predictor that rounds the window onto a grid (the cheap bug at 0.25 h, where rounding to
#: 0.1 h moves the window by 20 %), drops the partial final interval, or scales the 24 h
#: answer by H/24 lands outside the accuracy bar.
EXTRA_HORIZONS = (0.25, 6.05, 24.05)


def truth_name(horizon):
    """File name of the truth CSV for a forecast window of ``horizon`` hours."""
    if float(horizon) == 24.0:
        return TRUTH_FILE
    return "truth_h%s.csv" % ("%g" % float(horizon)).replace(".", "p")


def _write(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def build(out_dir, horizons=(24.0,) + EXTRA_HORIZONS):
    """Write the cohort inputs and one truth CSV per forecast window; return subject ids."""
    rng = np.random.default_rng(SECRET_SEED)
    os.makedirs(out_dir, exist_ok=True)

    subjects, doses, samples, covars = [], [], [], []
    truth = {float(h): [] for h in horizons}
    for i in range(N_HELDOUT):
        sid = f"H{i + 1:03d}"
        dose_mg = float(rng.choice(m.DOSE_LEVELS))
        subj = m.draw_subject(rng, sid, dose_mg)
        events = m.make_dose_events(rng, dose_mg)
        # The graded design must never reveal a day-14 sample.
        m.assert_no_day14_samples(events)

        subjects.append([sid, f"{dose_mg:g}"])
        covars.append([sid, f"{subj.weight_kg:.2f}", f"{subj.age_years:.1f}", subj.sex])
        for ev in events:
            t = ev.actual_h if ev.administered else ev.planned_h
            doses.append([sid, f"{t:.4f}", f"{ev.dose_mg:g}", ev.administered])

        rows = m.simulate_subject(rng, subj, events, m.heldout_schedule)
        # instruction.md promises "rows need not be sorted by time"; emit them shuffled (seeded
        # per subject so the cohort stays byte-deterministic) so a predictor that silently
        # assumes time-ordered rows is actually exercised. Truth is integrated independently.
        random.Random(SECRET_SEED + i).shuffle(rows)
        for t_s, obs, cens, _kind in rows:
            samples.append([sid, f"{t_s:.4f}", f"{obs:.5f}", cens])

        for h in truth:
            tr = m.true_exposures(subj, events, h)
            truth[h].append([sid, f"{tr['auc_ss']:.6f}", f"{tr['cmax_ss']:.6f}",
                             f"{tr['ctrough_ss']:.6f}"])

    _write(os.path.join(out_dir, "subjects.csv"), ["subject_id", "dose_mg"], subjects)
    _write(os.path.join(out_dir, "doses.csv"),
           ["subject_id", "dose_time_h", "dose_mg", "administered"], doses)
    _write(os.path.join(out_dir, "samples.csv"),
           ["subject_id", "sample_time_h", "concentration_ng_ml", "censored"], samples)
    _write(os.path.join(out_dir, "covariates.csv"),
           ["subject_id", "body_weight_kg", "age_years", "sex"], covars)
    for h, rows in truth.items():
        _write(os.path.join(out_dir, truth_name(h)),
               ["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"], rows)
    return [r[0] for r in truth[24.0]]
