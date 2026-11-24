# censored-exposure-forecasting

**Category:** Science
**Subcategory:** Pharmacometrics and statistical modeling
**Difficulty:** Hard
**Estimated expert time:** 10 hours

Reviewer-facing summary. The agent never sees this file.

---

## The task

A clinical pharmacology working group needs day-14 steady-state exposure forecasts (AUC, Cmax, Ctrough over `[t14, t14+H)`) for an incoming cohort from a once-daily multiple-ascending-dose trial. The drug induces its own clearance, and approximately 70% of participants step up to twice their initial dose starting on day 8. However, the incoming cohort is only sampled on days 1, 4, and 7 — all on the starting dose before the titration step — so the day-14 forecast is an extrapolation the model must compute rather than something visible in the data.

**Deliverable:** `/app/submission/predict.py`, invoked as:
`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`.
It outputs `subject_id,auc_ss,cmax_ss,ctrough_ss` for each subject. Any dependencies needed at evaluation time (such as fitted population parameters) must be saved under `/app/submission`; `/app/data`, `/app/baseline`, and `/app/analysis` are removed during grading.

**Agent-visible material:** a densely sampled cohort (`/app/data/rich`, 40 subjects, day-1 and day-14 profiles plus daily trough samples), a sparse cohort matching the test schedule (`/app/data/sparse`, 60 subjects), a naive baseline script, and an interim memo whose conclusions are incorrect (time-invariant clearance, linear dose scaling, BLQ records discarded).

## What makes it hard

The underlying data-generating model (`tests/.truth/truth_model.py`):

* Oral absorption with an absorption lag (0.4 h typical) into one compartment;
* Apparent clearance `CL0 * E(t)`, where the enzyme pool follows turnover kinetics driven by drug concentration: `dE/dt = kenz * (1 + Emax*C/(EC50+C)) - kenz*E`. Autoinduction therefore depends on dosing and compliance — dose escalation drives further induction, and clearance regresses toward baseline during washout;
* Covariates: allometric weight scaling on CL0 and V, power scaling of age on CL0, and sex effects on CL0 and V;
* Inter-individual variability on all 7 parameters (30–40% on CL0, Emax, EC50);
* Assay LLOQ of 2.50 ng/mL: 45% of rich cohort samples and 25% of sparse cohort samples are censored, predominantly the trough measurements that carry the induction signal.

A valid solution must uncover this pharmacokinetic structure from data (despite the misleading memo), calibrate a population model using a censored likelihood, compute empirical-Bayes MAP estimates for each subject, and integrate forward through the titration step. Standard NLME software is unavailable; code runs using standard numpy, scipy, and pandas.

### Measured with the true generating parameters (calibration, default 24 h window)

To show how much each modeling choice affects accuracy, each row evaluates a MAP estimator with true population parameters except for the single noted difference:

| Predictor | raw | reward | passes |
|---|---|---|---|
| correct structure | 0.120 | 0.760 | yes |
| induction that ignores concentration (time-only) | 0.181 | 0.638 | yes at 24 h; 0.511 at 47.95 h |
| no induction | 1.444 | 0.000 | no |
| population typical values only, no individual fit | 0.256 | 0.487 | no |

A time-only induction formulation cannot properly track the dose escalation or subsequent washout. While it barely passes the 24 h window, it falls close to failing on the 47.95 h window, where the concentration-driven model maintains 0.658.

## Scoring

For each subject and metric: `r = |pred - true| / true`;
`raw = 0.5*median(r_AUC) + 0.3*median(r_Cmax) + 0.2*median(r_Ctrough)`;
`reward = clip(1 - raw/0.5, 0, 1)`. A submission passes when `reward >= 0.50` (`raw <= 0.25`).
The metric formula, weights, and pass threshold remain identical to the original specification.

The evaluation reward is binary: 1 only when all 23 verification checks pass, including the pass threshold at the default window and each graded window (1.05 h, 6.05 h, 24.05 h, 47.95 h), and 0 otherwise.

### Where 1.0 comes from

1.0 is reached by the reference implementation in `solution/` (`solve.sh` calibrates `fit_population.py` on `/app/data`, then installs `predict.py` + `pk_engine.py` + `pop_params.json`). It passes all 23 checks. The continuous reward behind the pass is not 1.0 due to sparse sampling noise and assay censoring, but the final task score is binary, yielding 1.0.

## Measured results (local Docker, both images built from this package)

Tested using `--network none --cpus 4 --memory 8g`, running the solver image for solutions and the verifier image for grading, matching `task.toml`.

| Run | Pipeline reward | Detail |
|---|---|---|
| oracle (reference), grade 1 | 1 | 23/23 passed, suite 132 s |
| oracle (reference), grade 2 | 1 | 23/23 passed, suite 134 s; fresh `solve.sh` into a new volume gave a byte-identical submission |
| nop (empty submission) | 0 | fails closed: no `/app/submission/predict.py` |
| naive baseline (`/app/baseline/predict.py`) | 0 | 12/23; fails the accuracy bar (raw 1.768 at 24 h) |
| constant output | 0 | 11/23; one value copied to every subject, fails the accuracy bar |
| dose-and-covariate regression shortcut | 0 | 13/23; fails the accuracy bar at raw 0.514 (it scored reward 0.499 before this revision) |

Reference continuous rewards on the held-out cohort (scored in the verifier image):

| Window | raw | reward | median rel. error AUC / Cmax / Ctrough |
|---|---|---|---|
| 24 h (default) | 0.109 | 0.783 | 0.083 / 0.057 / 0.250 |
| 1.05 h | 0.097 | 0.806 | 0.115 / 0.079 / 0.079 |
| 6.05 h | 0.064 | 0.872 | 0.060 / 0.057 / 0.085 |
| 24.05 h | 0.109 | 0.782 | 0.083 / 0.057 / 0.251 |
| 47.95 h | 0.166 | 0.667 | 0.089 / 0.057 / 0.524 |
| 24 h, `--estimator population` (not graded for accuracy) | 0.246 | 0.508 | 0.178 / 0.118 / 0.610 |

Runtime: `solve.sh` runs in ~22 s; full-cohort prediction takes 3.6 s against the 120 s budget.

## Verification

* The unseen cohort (150 subjects) is generated inside the verifier from `tests/.truth/heldout.py` using a fixed random seed; it is never accessible in the solver image. Ground truth is integrated on a 0.02 h node grid with exact nodes at each dose administration and window boundary.
* Input datasets never contain observations within the day-14 window (`assert_no_day14_samples`).
* The predictor runs as the agent uid against read-only root inputs; output files are read with `O_NOFOLLOW`; inputs and submission files are hashed before and after evaluation.

### Requirement-to-check map (`tests/test_outputs.py`, 23 checks)

| Instruction requirement | Checks |
|---|---|
| Runs the documented CLI and writes OUT.csv | `test_submission_predicts` |
| Columns, one row per subject, finite positive values | `test_output_format` |
| Finishes within the runtime budget | `test_runtime_budget` |
| Reward >= 0.50 on the held-out cohort | `test_accuracy_pass_bar` |
| Two runs agree | `test_deterministic`, `test_optional_modes_are_deterministic` |
| Writes only OUT.csv; inputs and submission untouched | `test_inputs_unmodified` |
| Covariate fallbacks (70 kg, 45 years, M), per field, others unchanged | `test_covariates_missing_row_falls_back`, `test_covariates_unusable_weight_falls_back`, `test_covariates_unusable_sex_falls_back`, `test_covariates_unusable_age_falls_back`, `test_covariates_fallback_matches_default_row`, `test_covariates_fallback_leaves_others_unchanged` |
| `--horizon-h`: default = 24, exact window, graded accuracy, invalid values rejected | `test_horizon_default_matches_explicit_24`, `test_horizon_accuracy_matches_truth`, `test_horizon_window_additivity`, `test_horizon_rejects_invalid_values` |
| `--estimator`: default = map, population ignores samples and ids, responds to dose and weight, differs from map, invalid values rejected | `test_estimator_default_matches_explicit_map`, `test_estimator_population_ignores_samples`, `test_estimator_population_ignores_subject_id`, `test_estimator_population_responds_to_dosing_and_covariates`, `test_estimator_population_differs_from_map`, `test_estimator_rejects_invalid_values` |

## Changes in this revision

The previous task version allowed an empirical workaround: a 15-line linear regression of dose and covariates reached reward 0.499 against the 0.50 bar, and the memo provided true typical parameters. This revision hardens the challenge while keeping the scoring formula and pass threshold invariant:

* Truth model: concentration-dependent enzyme-turnover autoinduction (replacing a static Hill model), absorption lag time, age effect on CL0, wider between-subject variability, increased LLOQ from 1.00 to 2.50 ng/mL, and intra-study dose titration (doubling from day 8 for ~70% of subjects).
* Sampling schedule: days 1, 4, and 7 (previously 1, 7, and 11), strictly before dose escalation.
* Data regeneration: visible datasets regenerated; `rich/targets.csv` removed.
* Memo updated: revised with realistic parameters from a naive fit on the updated cohort.
* Graded forecast windows: set to 1.05, 6.05, 24.05, and 47.95 h (replacing 0.25, 6.05, 24.05 h, as 0.25 h fell entirely within the absorption lag).
* `--estimator population`: tested for required sample-invariance properties rather than accuracy, as wider variance puts fixed-effect accuracy near 0.508.
* Fallback rules: added explicit fallback for missing or unparseable `age_years` defaulting to 45 years.
* Reference solution: updated and validated on the revised model.

Because visible datasets, instructions, and verification logic changed, prior evaluation results are superseded.

## Studio runs

No studio runs recorded on this version yet. Benchmark targets: reference and empty run IDs (Claude Opus 4.6), writer and reviewer Astra (Codex) runs against token budgets (< 0.67 / 400k), and reviewer Opus 5.5 run against the < 0.75 threshold.

## AI assistance

Anthropic Claude assisted with revision preparation: refining the truth model, running calibration benchmarks, updating reference solution scripts, test suite adjustments, and documentation. The author defined the clinical challenge, scoring design, and evaluation structure.

## Environment

* Agent image: `python:3.13-slim-bookworm` (pinned digest), numpy 2.2.3, scipy 1.15.2, pandas 2.2.3; unprivileged uid 1000; no internet access.
* Verifier image: identical base configuration with pytest 8.4.1 and pytest-json-ctrf 0.6.1; writes CTRF reports to `/logs/verifier/ctrf.json`.
* 4 CPUs, 8192 MB RAM, working directory `/app`.
