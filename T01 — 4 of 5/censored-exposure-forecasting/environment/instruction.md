Predict Day 14 steady-state exposure (AUC, Cmax and trough) for every subject in a new cohort of a once-daily oral multiple-ascending-dose trial that includes a within-study dose titration step. Your working directory is `/app`.

Historical trial data is located in `/app/data`: a densely sampled cohort in `/app/data/rich`, whose participants include rich blood draws across the Day 14 interval, and a sparsely sampled cohort in `/app/data/sparse` matching the collection schedule of the incoming cohort. A simple baseline script is provided at `/app/baseline/predict.py`, alongside an earlier team analysis memo at `/app/analysis/prior_memo.md`.

Deliver `/app/submission/predict.py`. It will be evaluated on an unseen cohort of approximately 150 subjects following the sparse sampling protocol of `/app/data/sparse` (a few plasma measurements across Days 1, 4, and 7, with no subsequent sampling), drawn from the same underlying population and dosing regimen as the visible data. Input files follow the schemas below. Evaluation invokes:

`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`

and assesses the resulting `OUT.csv`. During grading, only `DIR` and the contents of `/app/submission` remain accessible; `/app/data`, `/app/baseline`, and `/app/analysis` will not be present. The script must write `OUT.csv` and no other file into the target directory, leaving the input directory and `/app/submission` untouched (Python bytecode caches are exempt); intermediate scratch files should be placed under `/tmp`. Executions must complete within 120 seconds of wall-clock time from launch to exit (with a 60-second host load grace, establishing a firm 180-second upper ceiling), and repeated runs on identical inputs must yield identical results.

`--horizon-h H` configures the evaluation window to `[t14, t14+H)` rather than standard `[t14, t14+24)`; all three exposure metrics are evaluated over this window, with trough defined as the plasma concentration at `t14+H`. Without the flag, `H = 24.0`, and outputs must match an explicit `--horizon-h 24` run. `H` must be a finite numerical value with `0 < H <= 168.0`; any other argument (zero, negative, non-numeric, or greater than 168) must terminate with a non-zero exit status without writing an output file. Runs using `--horizon-h H` are scored against ground-truth values over `[t14, t14+H)`. Evaluation windows not matching whole multiples of an integration step are tested, requiring exact window boundary handling rather than grid rounding.

`--estimator E` specifies how predictions are formed, accepting exactly `map` or `population`. Any other value, including `MAP`, ` map `, or `Population`, must exit non-zero with no output. The default `map` option runs empirical-Bayes individualization. In contrast, `population` must not estimate subject-level random effects: forecasts must depend solely on dosing records and patient covariates, remaining strictly independent of observed samples. Two subjects sharing identical dosing histories and covariates but differing in concentration samples must receive identical exposures, and forecasts must not vary based on sample values, collection times, censoring flags, observation counts, or `subject_id`. Projections must still respond to dosing and covariates: subjects differing only in dose level or body weight must receive different values. The `population` setting is evaluated for adherence to these invariance rules rather than prediction accuracy.

Each input directory contains four CSV files with headers:

- `subjects.csv`: `subject_id,dose_mg`. `dose_mg` is the subject's starting dose amount.
- `doses.csv`: `subject_id,dose_time_h,dose_mg,administered`. One record per planned dose in scheduled sequence. `dose_mg` indicates the amount for that administration; subjects may step up to higher doses mid-study, so amounts vary across rows. For `administered=1`, `dose_time_h` represents the actual administration time in hours from the initial scheduled dose (accounting for dose delays); for `administered=0`, it marks scheduled time and contributes no active drug.
- `samples.csv`: `subject_id,sample_time_h,concentration_ng_ml,censored`. Measured plasma concentrations. `censored=1` denotes values below the assay lower limit of quantification (LLOQ = 2.50 ng/mL) where `concentration_ng_ml` is an uninformative placeholder; `censored=0` rows are quantified concentrations.
- `covariates.csv`: `subject_id,body_weight_kg,age_years,sex`.

Define `t14` as the largest `dose_time_h` across all rows of `doses.csv`, regardless of row index or `administered` indicator; this represents the Day 14 dose administration time. Targets describe the `[t14, t14+24)` hour interval: `auc_ss` is the area under the concentration-time curve (ng*h/mL), `cmax_ss` the maximum concentration within the interval (ng/mL), and `ctrough_ss` the concentration at `t14+24` (ng/mL). Records in input tables may appear in arbitrary order.

Write `OUT.csv` with exactly the columns `subject_id,auc_ss,cmax_ss,ctrough_ss`: one row per subject in `subjects.csv`, identical `subject_id` identifiers, with all values reported as finite positive numbers. Additional columns are ignored. Real study cohorts often have missing entries, so data anomalies must not halt execution: if a subject lacks a record in `covariates.csv` or provides a blank/invalid `body_weight_kg`, use 70 kg; if `age_years` is missing or invalid, use 45 years; and if `sex` is blank or not exactly `M` or `F` (so `f`, `m`, or ` F ` are invalid), use `M`. Apply fallbacks independently per field - valid fields must still be respected - write a complete row for that subject, and leave predictions for all other subjects unaffected.

Accuracy. For each subject and quantity the relative error is $ r = \lvert \hat{y} - y \rvert / y $, where $ \hat{y} $ is the predicted and $ y $ the true value. Over the cohort, accuracy is measured by

$$
\mathrm{raw} = 0.5\,\mathrm{median}(r_{\mathrm{AUC}}) + 0.3\,\mathrm{median}(r_{\mathrm{Cmax}}) + 0.2\,\mathrm{median}(r_{\mathrm{Ctrough}})
$$

and lower is better.
