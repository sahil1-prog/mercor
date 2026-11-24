Predict Day 14 steady-state exposure (AUC, Cmax and trough) for every subject in a new cohort of a once-daily oral multiple-ascending-dose trial that includes a within-study dose titration step. Your working directory is `/app`.

Historical trial data is in `/app/data`: a densely sampled cohort in `/app/data/rich`, whose subjects also have dense sampling across the Day 14 interval, and a sparsely sampled cohort in `/app/data/sparse` that follows the sampling protocol of the new cohort. There is also a naive predictor at `/app/baseline/predict.py` and an earlier team analysis memo at `/app/analysis/prior_memo.md`.

Deliver `/app/submission/predict.py`. It will be evaluated on a new cohort of about 150 subjects with the same sparse sampling schedule as `/app/data/sparse` (a few plasma samples on Days 1, 4 and 7, nothing later), drawn from the same population and dosing design as the visible data. Input files follow the schemas below. Evaluation runs:

`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`

and reads the resulting `OUT.csv`. At evaluation time only `DIR` and the contents of `/app/submission` are available; `/app/data`, `/app/baseline` and `/app/analysis` do not exist. The script must write `OUT.csv` and no other file into the output directory, and must leave the input directory and `/app/submission` unchanged (Python bytecode caches excepted); put any scratch files under `/tmp`. A run must finish within 120 seconds of wall-clock time from launch to exit (a 60-second host-load grace is allowed on top of this, so 180 seconds is the hard limit), and repeated runs on the same input must produce identical numbers.

`--horizon-h H` sets the forecast window to `[t14, t14+H)` instead of `[t14, t14+24)`; all three quantities are computed over that window, with the trough taken as the concentration at `t14+H`. Without the flag `H = 24.0`, and the result must equal an explicit `--horizon-h 24`. `H` must be a finite number with `0 < H <= 168.0`; anything else (zero, negative, non-numeric, above 168) must exit with a non-zero status and write no output file. Runs with `--horizon-h H` are measured with the same accuracy metric against the true values over `[t14, t14+H)`. Window lengths that are not whole multiples of any integration step are used, so the window end must be handled exactly rather than snapped to a grid.

`--estimator E` selects how each subject's prediction is formed and accepts exactly `map` or `population`. Any other token, including `MAP`, ` map ` or `Population`, must exit with a non-zero status and write no output. The default `map` uses full empirical-Bayes individualisation. `population` must not estimate subject-level random effects: predictions depend only on the dosing records and covariates and are independent of the observed samples. Two subjects with identical dosing rows and covariates but different samples must get identical exposures, and predictions must not change with sample values, times, censoring flags, sample counts or `subject_id`. It must still respond to regimen and covariates: subjects that differ only in dose or weight must get different exposures. A `population` run is checked for these properties, not measured for accuracy.

Each input directory contains four CSV files with headers:

- `subjects.csv`: `subject_id,dose_mg`. `dose_mg` is the subject's starting dose.
- `doses.csv`: `subject_id,dose_time_h,dose_mg,administered`. One row per planned dose, in schedule order. `dose_mg` is the amount for that dose; subjects may step up to a higher dose during the study, so it can change between rows. For `administered=1`, `dose_time_h` is the actual dosing time in hours from the first planned dose (late doses included); for `administered=0` it is the planned time and no drug was given.
- `samples.csv`: `subject_id,sample_time_h,concentration_ng_ml,censored`. Plasma concentrations. `censored=1` means the sample was below the assay lower limit of quantification (LLOQ = 2.50 ng/mL) and `concentration_ng_ml` is a placeholder with no information; `censored=0` rows are measured concentrations.
- `covariates.csv`: `subject_id,body_weight_kg,age_years,sex`.

`t14` is the largest `dose_time_h` over all rows of `doses.csv`, regardless of row order or `administered` flag; it is the Day 14 dose time. The targets cover `[t14, t14+24)` hours: `auc_ss` is the area under the concentration-time curve (ng*h/mL), `cmax_ss` the highest concentration in the interval (ng/mL), and `ctrough_ss` the concentration at `t14+24` (ng/mL). Rows in the input tables are not guaranteed to be sorted by time.

Write `OUT.csv` with exactly the columns `subject_id,auc_ss,cmax_ss,ctrough_ss`: one row per subject in `subjects.csv`, same `subject_id` strings, every value a finite positive number. Extra columns are ignored. Real cohorts are incomplete, so do not abort the whole run on a bad subject: when a subject has no `covariates.csv` row, or its `body_weight_kg` is blank or not a usable positive number, use 70 kg for that field; when its `age_years` is blank or not a usable positive number, use 45 years; when its `sex` is blank or not `M`/`F`, use `M`. Fall back per field - usable fields are still honoured beside an unusable one - still emit a complete row for that subject, and leave every other subject's prediction unchanged.

Accuracy. For each subject and quantity the relative error is $ r = \lvert \hat{y} - y \rvert / y $, where $ \hat{y} $ is the predicted and $ y $ the true value. Over the cohort, accuracy is measured by

$$
\mathrm{raw} = 0.5\,\mathrm{median}(r_{\mathrm{AUC}}) + 0.3\,\mathrm{median}(r_{\mathrm{Cmax}}) + 0.2\,\mathrm{median}(r_{\mathrm{Ctrough}})
$$

and lower is better.
