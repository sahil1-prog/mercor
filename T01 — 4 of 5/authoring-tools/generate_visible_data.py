#!/usr/bin/env python3
"""Synthetic trial cohort generator using the evaluation ground-truth model.

Maintained outside the distributed task bundle: imports tests/.truth/truth_model.py
and references distinct training cohort seeds, isolating evaluation ground truth from
the agent environment.

    python generate_visible_data.py <task_dir>

Generates <task_dir>/environment/data/rich (dense Day 1 profiles, pre-dose troughs,
Day 14 kinetics) and <task_dir>/environment/data/sparse (sparse multi-day sampling
matching the unseen evaluation design). Seeds differ from SECRET_SEED so no visible
subject recurs in the evaluation cohort.
"""
import csv
import os
import random
import sys

import numpy as np

TASK = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(TASK, "tests", ".truth"))
import heldout  # noqa: E402
import truth_model as m  # noqa: E402

COHORTS = {
    # name: (seed, subjects, id prefix, sampling design)
    "rich": (20261110, 40, "R", m.rich_schedule),
    "sparse": (20261111, 60, "S", m.sparse_schedule),
}


def write(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def build(name, seed, n, prefix, schedule):
    assert seed != heldout.SECRET_SEED
    rng = np.random.default_rng(seed)
    subjects, doses, samples, covars = [], [], [], []
    for i in range(n):
        sid = f"{prefix}{i + 1:03d}"
        dose_mg = float(rng.choice(m.DOSE_LEVELS))
        subj = m.draw_subject(rng, sid, dose_mg)
        events = m.make_dose_events(rng, dose_mg)
        subjects.append([sid, f"{dose_mg:g}"])
        covars.append([sid, f"{subj.weight_kg:.2f}", f"{subj.age_years:.1f}", subj.sex])
        for ev in events:
            t = ev.actual_h if ev.administered else ev.planned_h
            doses.append([sid, f"{t:.4f}", f"{ev.dose_mg:g}", ev.administered])
        rows = m.simulate_subject(rng, subj, events, schedule)
        random.Random(seed + i).shuffle(rows)
        for t_s, obs, cens, _kind in rows:
            samples.append([sid, f"{t_s:.4f}", f"{obs:.5f}", cens])

    out = os.path.join(TASK, "environment", "data", name)
    os.makedirs(out, exist_ok=True)
    for stale in os.listdir(out):
        os.remove(os.path.join(out, stale))
    write(os.path.join(out, "subjects.csv"), ["subject_id", "dose_mg"], subjects)
    write(os.path.join(out, "doses.csv"),
          ["subject_id", "dose_time_h", "dose_mg", "administered"], doses)
    write(os.path.join(out, "samples.csv"),
          ["subject_id", "sample_time_h", "concentration_ng_ml", "censored"], samples)
    write(os.path.join(out, "covariates.csv"),
          ["subject_id", "body_weight_kg", "age_years", "sex"], covars)
    blq = sum(r[3] for r in samples) / len(samples)
    print(f"{name}: {n} subjects, {len(samples)} samples, {blq:.0%} below LLOQ")


if __name__ == "__main__":
    for name, spec in COHORTS.items():
        build(name, *spec)
