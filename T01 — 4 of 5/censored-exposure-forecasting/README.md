# censored-exposure-forecasting

**Category:** Science
**Subcategory:** Pharmacometrics and statistical modeling
**Difficulty:** Hard
**Estimated expert time:** 10 hours

Reviewer-facing summary. The agent never sees this file.

---

## The task

A clinical pharmacology group needs day-14 exposure forecasts (AUC, Cmax, Ctrough over
`[t14, t14+H)`) for a new cohort from a once-daily multiple-ascending-dose study. The
drug induces its own clearance, and about 70% of subjects step up to twice their
starting dose from day 8. The new cohort was only sampled on days 1, 4 and 7 - all on
the starting dose, before the step - so the day-14 forecast is an extrapolation the
model has to carry, not something the samples show.

**Deliverable:** `/app/submission/predict.py`, run as
`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`.
It writes `subject_id,auc_ss,cmax_ss,ctrough_ss` for every subject. Anything it needs at
grading time (for example a fitted population model) must be shipped under
`/app/submission`; `/app/data`, `/app/baseline` and `/app/analysis` are not present then.

**Agent-visible material:** a dense cohort (`/app/data/rich`, 40 subjects, day-1 and
day-14 profiles plus daily pre-dose troughs), a sparse cohort with the graded design
(`/app/data/sparse`, 60 subjects), a naive predictor, and an interim memo whose
conclusions are wrong (time-invariant, dose-proportional, censored samples dropped).

## What makes it hard

The generating model (`tests/.truth/truth_model.py`):

* oral absorption with a lag time (0.4 h typical) into one compartment;
* clearance `CL0 * E(t)`, where the enzyme pool follows a turnover model driven by the
  drug's own concentration: `dE/dt = kenz * (1 + Emax*C/(EC50+C)) - kenz*E`. Induction
  therefore depends on dose and adherence - stepping up the dose induces further, and
  clearance falls back during washout after the last dose;
* covariates: allometric weight on CL0 and V, a power of age on CL0, sex on CL0 and V;
* between-subject variability on all seven parameters (30-40% on CL0, Emax, EC50);
* LLOQ 2.50 ng/mL: 45% of the rich cohort's samples and 25% of the sparse cohort's
  samples are censored, mostly the induced troughs that carry the induction signal.

A solver has to discover that structure from the data (the memo points the other way),
fit a population model under a censored likelihood, individualise each sparse subject
by empirical Bayes, and integrate the subject's own dose rows forward through the
titration step. No NLME package is installed; numpy, scipy and pandas are.

### Measured with the true generating parameters (calibration, default 24 h window)

These isolate how much each modelling decision matters. Each row is a MAP predictor
that knows the true population values except for the one deliberate change.

| Predictor | raw | reward | passes |
|---|---|---|---|
| correct structure | 0.120 | 0.760 | yes |
| induction that ignores concentration (time-only) | 0.181 | 0.638 | yes at 24 h; 0.511 at 47.95 h |
| no induction | 1.444 | 0.000 | no |
| population typical values only, no individual fit | 0.256 | 0.487 | no |

Time-only induction cannot follow the titration step or the washout. It survives the
24 h window but is close to the bar on the graded 47.95 h window, where the correct
structure keeps 0.658.

## Scoring

For each subject and quantity `r = |pred - true| / true`;
`raw = 0.5*median(r_AUC) + 0.3*median(r_Cmax) + 0.2*median(r_Ctrough)`;
`reward = clip(1 - raw/0.5, 0, 1)`. A run passes when `reward >= 0.50` (`raw <= 0.25`).
The formula, constants and pass bar are unchanged from the original package.

The pipeline reward is binary: 1 only when all 23 checks pass, including the pass bar at
the default window and at each graded window (1.05, 6.05, 24.05, 47.95 h), else 0.

### Where 1.0 comes from

1.0 is reached by an explicit witness: the reference in `solution/` (`solve.sh` fits
`fit_population.py` on `/app/data`, then installs `predict.py` + `pk_engine.py` +
`pop_params.json`). It passes all 23 checks. The continuous reward behind the pass is
not 1.0 - perfect forecasts are impossible with censored sparse data and assay noise -
but the graded score is binary, so the witness scores 1.0.

## Measured results (local Docker, both images built from this package)

Runs used `--network none --cpus 4 --memory 8g`, the agent image for the solution and
the verifier image for grading, exactly as `task.toml` configures them.

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

Runtime: `solve.sh` takes 22 s; one full-cohort forecast takes 3.6 s against the 120 s
budget.

## Verification

* The held-out cohort (150 subjects) is regenerated inside the verifier from
  `tests/.truth/heldout.py` with a fixed seed; it never exists in the agent image.
  Truth is integrated on a 0.02 h node grid with every dose entry and window boundary
  as an exact node.
* Held-out inputs never contain a sample inside the day-14 window
  (`assert_no_day14_samples`).
* The predictor runs as the agent uid on root-owned inputs; outputs are read with
  `O_NOFOLLOW`; inputs and the submission tree are hashed before and after.

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

The previous version was solved in about 30 minutes: a 15-line regression of day-14
exposure per mg on weight and sex, with no PK model, reached reward 0.499 against the
0.50 bar, and the memo listed the true typical values. This revision hardens what is
measured; the scoring formula and pass bar are unchanged.

* Truth model: concentration-driven enzyme-turnover induction (replaces a time-only
  Hill curve), absorption lag, age effect on CL0, wider between-subject variability,
  LLOQ 1.00 -> 2.50 ng/mL, in-study titration (x2 from day 8 for ~70% of subjects).
* Sparse/held-out sampling: days 1, 4 and 7 (was 1, 7 and 11), all before titration.
* Visible data regenerated; `rich/targets.csv` removed.
* Memo rewritten with numbers from an actual naive fit of the new data.
* Graded windows 1.05 / 6.05 / 24.05 / 47.95 h (was 0.25 / 6.05 / 24.05 h): 0.25 h lies
  inside the absorption lag, where all three quantities collapse to the trough.
* `--estimator population` is checked for its required properties but no longer graded
  for accuracy: with the wider variability the reference's population-only forecast sits
  at 0.508, which would make the check a coin flip for a correct solution.
* New instruction rule and check: unusable `age_years` falls back to 45 years.
* Reference solution rewritten for the new model.

Because the agent-visible data, instruction and grader all changed, every Studio run
and QC result on the previous upload is invalid for this one.

## Studio runs

None yet on this upload. Record here: oracle and nop run IDs (Claude Opus 4.6), Astra
(Codex) writer and reviewer run IDs with scores and trajectory token counts against the
< 0.67 / 400k bar, and the reviewer's Opus 5.5 run against the < 0.75 bar.

## AI assistance

Claude (Anthropic) helped with this revision: the truth-model changes, the calibration
experiments above, the reference solution rewrite, test updates and documentation. The
author chose the subject, scoring design and private-data design.

## Environment

* Agent image: `python:3.13-slim-bookworm` (pinned by digest), numpy 2.2.3, scipy
  1.15.2, pandas 2.2.3; non-root uid 1000; no network.
* Verifier image: same base and pins plus pytest 8.4.1 and pytest-json-ctrf 0.6.1;
  writes the CTRF report to `/logs/verifier/ctrf.json`.
* 4 CPUs, 8192 MB RAM, working directory `/app`.
