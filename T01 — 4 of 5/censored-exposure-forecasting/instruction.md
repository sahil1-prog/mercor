A clinical pharmacology group is choosing the phase-2 dose of an oral drug from a once-daily multiple-ascending-dose study, and needs day-14 steady-state exposure forecasts for a new cohort. Your working directory is `/app`.

You are given the historical study data under `/app/data` (a densely sampled cohort in `/app/data/rich` and a sparsely sampled cohort in `/app/data/sparse`, the latter with targets withheld), a working naive predictor at `/app/baseline/predict.py`, and the team's analysis memo at `/app/analysis/prior_memo.md`. Read them as you see fit. Everything runs offline; you have 4 CPUs and 8 GB of RAM.

Deliver `/app/submission/predict.py`. The grader builds a fresh cohort of about 150 unseen subjects with the same sparse sampling design as `/app/data/sparse` - a few day-1, day-7 and day-11 samples per subject, no dense day-14 profile - drawn from the same population model and regimen as the visible cohorts, so parameters you estimate from them apply to it. It uses the schemas below. The grader then runs

`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`

and scores `OUT.csv`. At grading time the only readable inputs are `DIR` and the files you ship under `/app/submission` - `/app/data`, `/app/baseline` and `/app/analysis` are not present then, so any population model you fit on them must be saved as an artifact under `/app/submission`. Your program may use the Python standard library and preinstalled packages. It must write `OUT.csv` and nothing else into the directory holding `--output`, and must not create, remove or modify files in the input directory or under `/app/submission` (Python bytecode caches are ignored); scratch files belong in a temporary directory under `/tmp`. It must finish within 120 seconds (wall-clock runtime budget; the verifier grants a 60-second host-load grace on top of this, for a total ceiling of 180 seconds), launch to exit, and two runs on the same cohort must agree.

The optional `--horizon-h H` sets the forecast window length in hours, so the targets describe `[t14, t14+H)` instead of `[t14, t14+24)`; all three quantities are recomputed over that window (trough is the concentration at `t14+H`). Omit it for the default `H = 24.0`, which must reproduce the 24-hour result exactly. `H` must be finite with `0 < H <= 168.0`; any other value (zero, negative, non-numeric, above 168) must exit non-zero and write no output. A run with `--horizon-h H` is scored with the same metric and pass bar against the true quantities of `[t14, t14+H)`. Windows that are not whole multiples of an integration step are scored too, so the window close must be honoured exactly rather than rounded onto a fixed grid.

The optional `--estimator E` selects how each subject's forecast is derived, with `E` one of `map` or `population`. The two valid values are exact strings: `MAP`, ` map ` and `Population` are not accepted and must exit non-zero and write no output. The default `map` is your normal per-subject fit. With `population` you must NOT fit individual random effects: each subject's forecast depends only on its dosing and covariates, never on its measured samples - two subjects with identical dose rows and covariates but different samples get identical values, and the forecast may not depend on the samples' values, timing, censor flags, row count, or the `subject_id`. It must still respond to dosing and covariates - subjects differing only in dose level or body weight get different values - and a `population` run is scored with the same metric and pass bar as the default run.

Each input directory holds four CSVs with a header row:

- `subjects.csv`: `subject_id,dose_mg`.
- `doses.csv`: `subject_id,dose_time_h,dose_mg,administered` - one row per planned dose, in planned order. On `administered=1` rows `dose_time_h` is the actual administration time in hours since the first planned dose (a late dose carries its actual, later time); on `administered=0` rows it is the planned time and the dose contributes no drug.
- `samples.csv`: `subject_id,sample_time_h,concentration_ng_ml,censored` - measured plasma concentrations. `censored=1` means the sample fell below the assay LLOQ of 1.00 ng/mL and `concentration_ng_ml` is a placeholder, not a measurement; `censored=0` rows are measurements.
- `covariates.csv`: `subject_id,body_weight_kg,age_years,sex`.

Let `t14` be the largest `dose_time_h` over all `doses.csv` rows, whatever row it is and whether `administered` is 0 or 1: the day-14 dose time. Targets describe `[t14, t14+24)` hours: `auc_ss` is its area under the concentration-time curve in ng·h/mL, `cmax_ss` the maximum concentration in it in ng/mL, `ctrough_ss` the concentration at `t14+24` in ng/mL. Rows need not be sorted by time.

Write `OUT.csv` with exactly the columns `subject_id,auc_ss,cmax_ss,ctrough_ss`: one row per subject in `subjects.csv`, same `subject_id` strings, every value a finite positive number. Extra columns are ignored. Real cohorts are incomplete, so do not abort the whole run on a bad subject: when a subject has no `covariates.csv` row, or its `body_weight_kg` is blank or not a usable positive number, use 70 kg for that field; when its `sex` is blank or not `M`/`F`, use `M`. Fall back per field - a usable weight is still honoured beside an unusable sex, and vice versa - still emit a complete row for that subject, and leave every other subject's prediction unchanged.

Scoring. For each subject and quantity let `r = |predicted - true| / true`. The raw metric is

`raw = 0.5*median(r_AUCss) + 0.3*median(r_Cmaxss) + 0.2*median(r_Ctroughss)`

and your reward is `clip(1 - raw / 0.5, 0, 1)`. Your submission passes - and is scored 1 - when that reward is at least 0.50, i.e. when `raw <= 0.25`; otherwise it is scored 0. A missing, malformed, incomplete, non-finite, or non-reproducible submission scores 0.
