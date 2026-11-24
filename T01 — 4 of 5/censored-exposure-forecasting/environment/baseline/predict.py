#!/usr/bin/env python3
"""Naive baseline day-14 exposure predictor.

This is a working but deliberately simplistic starting point: it estimates
empirical exposure ratios (AUC/dose, Cmax/dose, Ctrough/dose) from the visible
rich cohort without any pharmacokinetic model, differential equations,
autoinduction mechanics, or BLQ censoring adjustments. It scales those
empirical ratios by each subject's planned dose level.

Known shortcuts (these are why it leaves accuracy on the table):
  * Completely non-compartmental: no absorption or clearance kinetics;
  * Samples flagged ``censored=1`` are ignored;
  * Autoinduction over the 14-day course is completely ignored;
  * Body weight and sex covariates are ignored.

Usage:
    python3 /app/baseline/predict.py --input-dir DIR --output OUT.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys

DATA_ROOT = "/app/data"


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def compute_empirical_ratios():
    """Derive naive dose-normalized exposure ratios from the rich training cohort."""
    ratios = {"auc_per_mg": 38.5, "cmax_per_mg": 2.8, "ctrough_per_mg": 0.85}
    rich_path = os.path.join(DATA_ROOT, "rich")
    if not os.path.isdir(rich_path):
        return ratios

    try:
        subs = {r["subject_id"]: float(r["dose_mg"]) for r in read_csv(os.path.join(rich_path, "subjects.csv"))}
        samples = read_csv(os.path.join(rich_path, "samples.csv"))

        day14_by_sub = {}
        for r in samples:
            if int(float(r.get("censored", 0))) == 0:
                t = float(r["sample_time_h"])
                if t >= 312.0:
                    day14_by_sub.setdefault(r["subject_id"], []).append((t, float(r["concentration_ng_ml"])))

        aucs, cmaxs, ctroughs = [], [], []
        for sid, pts in day14_by_sub.items():
            if len(pts) >= 2 and sid in subs and subs[sid] > 0:
                pts.sort()
                dose = subs[sid]
                auc = sum(0.5 * (pts[i][1] + pts[i + 1][1]) * (pts[i + 1][0] - pts[i][0]) for i in range(len(pts) - 1))
                cmax = max(p[1] for p in pts)
                ctrough = pts[-1][1]
                aucs.append(auc / dose)
                cmaxs.append(cmax / dose)
                ctroughs.append(ctrough / dose)

        if aucs and cmaxs and ctroughs:
            ratios["auc_per_mg"] = float(sum(aucs) / len(aucs))
            ratios["cmax_per_mg"] = float(sum(cmaxs) / len(cmaxs))
            ratios["ctrough_per_mg"] = float(sum(ctroughs) / len(ctroughs))
    except Exception:
        pass
    return ratios


def main():
    ap = argparse.ArgumentParser(description="Naive empirical day-14 exposure predictor")
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--horizon-h", type=float, default=24.0, help="Forecast horizon (hours)")
    ap.add_argument("--estimator", default="map", help="Estimator strategy")
    args = ap.parse_args()

    ratios = compute_empirical_ratios()

    subs = read_csv(os.path.join(args.input_dir, "subjects.csv"))
    out = []
    horizon_factor = max(0.01, min(1.0, args.horizon_h / 24.0))

    for s in subs:
        sid = s["subject_id"]
        dose = float(s.get("dose_mg", 100.0))
        auc = dose * ratios["auc_per_mg"] * horizon_factor
        cmax = dose * ratios["cmax_per_mg"]
        ctrough = dose * ratios["ctrough_per_mg"]
        out.append([sid, f"{auc:.6f}", f"{cmax:.6f}", f"{ctrough:.6f}"])

    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"])
        w.writerows(out)


if __name__ == "__main__":
    main()
