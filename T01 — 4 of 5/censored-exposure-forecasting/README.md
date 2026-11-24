# censored-exposure-forecasting

**Category:** Science
**Subcategory:** Pharmacometrics and statistical modeling
**Difficulty:** Hard
**Estimated expert time:** 10 hours

Reviewer-facing summary. The agent never sees this file.

---

## The task

A clinical pharmacology group requires Day 14 exposure projections (AUC, Cmax, and Ctrough evaluated across `[t14, t14+H)`) for an incoming cohort in a once-daily multiple-ascending-dose protocol. The test compound stimulates its own elimination clearance, and approximately 70% of subjects escalate to twice their initial dose starting on Day 8. The incoming cohort undergoes bioanalytical sampling exclusively on Days 1, 4, and 7 — all occurring prior to the Day 8 titration step — meaning the Day 14 forecast constitutes an extrapolation governed entirely by the mathematical model rather than direct empirical observations.

**Deliverable:** `/app/submission/predict.py`, executed via:
`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`.
It outputs `subject_id,auc_ss,cmax_ss,ctrough_ss` for all cohort participants. Any dependencies required during evaluation (such as a calibrated population model file) must reside within `/app/submission`; `/app/data`, `/app/baseline`, and `/app/analysis` will be removed during grading.

**Agent-visible material:** a densely sampled cohort (`/app/data/rich`, 40 subjects with Day 1 and Day 14 profiles plus daily trough monitoring), a sparsely sampled cohort matching the target evaluation structure (`/app/data/sparse`, 60 subjects), a baseline naive predictor, and an internal analysis memo advocating incorrect assumptions (stationary clearance, linear dose scaling, omitting BLQ records).

## What makes it hard

The underlying generative model (`tests/.truth/truth_model.py`):

* Oral absorption delayed by a lag period (typical value 0.4 h) entering a central compartment;
* Apparent clearance defined as `CL0 * E(t)`, where enzyme turnover is stimulated by circulating drug concentrations: `dE/dt = kenz * (1 + Emax*C/(EC50+C)) - kenz*E`. Autoinduction kinetics consequently depend on dosing history and compliance — dose escalation drives further induction, while drug washout allows clearance to regress toward baseline;
* Demographic covariates: allometric body weight on CL0 and V, power-law age scaling on CL0, and biological sex effects on CL0 and V;
* Inter-individual variability (IIV) across all 7 parameters (30–40% CV on CL0, Emax, and EC50);
* Analytical quantification limit of 2.50 ng/mL: 45% of rich cohort samples and 25% of sparse cohort samples are censored, concentrated primarily in the informative post-dose troughs that capture autoinduction.

An effective solution must deduce this underlying kinetic behavior directly from data (contrasting the flawed memo), fit a population PK model under a censored likelihood, compute empirical-Bayes individual parameter estimates, and integrate forward through the titration step. Standard NLME platforms are unavailable; execution relies on base numpy, scipy, and pandas.

### Measured with the true generating parameters (calibration, default 24 h window)

To illustrate the sensitivity of modeling decisions, each row below evaluates a MAP estimator initialized with true population values except for the isolated structural deviation:

| Predictor | raw | reward | passes |
|---|---|---|---|
| correct structure | 0.120 | 0.760 | yes |
| induction that ignores concentration (time-only) | 0.181 | 0.638 | yes at 24 h; 0.511 at 47.95 h |
| no induction | 1.444 | 0.000 | no |
| population typical values only, no individual fit | 0.256 | 0.487 | no |

A purely time-dependent induction formulation fails to track the dose titration step and post-dose washout. While it marginally meets criteria at 24 h, it drops close to failing on the extended 47.95 h window, where the concentration-driven formulation maintains a reward of 0.658.

## Scoring

For every subject and parameter: `r = |pred - true| / true`;
`raw = 0.5*median(r_AUC) + 0.3*median(r_Cmax) + 0.2*median(r_Ctrough)`;
`reward = clip(1 - raw/0.5, 0, 1)`. Submissions pass when `reward >= 0.50` (`raw <= 0.25`).
The mathematical formula, weighing coefficients, and passing threshold remain fully preserved from the original specification.

The automated evaluation pipeline assigns a binary reward: 1 only when all 23 verification tests pass, including passing accuracy thresholds at default and all evaluated horizons (1.05 h, 6.05 h, 24.05 h, 47.95 h), and 0 otherwise.

### Where 1.0 comes from

1.0 is established via an explicit reference implementation: the solution within `solution/` (`solve.sh` calibrates `fit_population.py` against `/app/data`, then establishes `predict.py` + `pk_engine.py` + `pop_params.json`). It successfully passes all 23 grading checks. While the continuous reward is bounded by sparse data noise and censoring, the overall task grade is binary, yielding a final score of 1.0.

## Measured results (local Docker, both images built from this package)

Evaluations executed under `--network none --cpus 4 --memory 8g`, employing the solver image for task execution and the verifier image for grading, matching `task.toml` specifications.

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

Runtime: `solve.sh` executes in ~22 s; a full-cohort prediction takes 3.6 s against the 120 s contract budget.

## Verification

* The unseen held-out cohort (150 subjects) is constructed dynamically within the verifier from `tests/.truth/heldout.py` using a fixed random seed; it is never accessible in the solver container. Ground truth is generated across a 0.02 h integration grid with exact node placements at dose administrations and interval boundaries.
* Input datasets never supply observations within the Day 14 window (`assert_no_day14_samples`).
* The prediction process runs under the unprivileged agent uid against read-only root inputs; output files are verified with `O_NOFOLLOW`; directory contents and submission files are cryptographically hashed before and after evaluation.

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

The prior task release allowed a quick heuristic workaround: a 15-line linear regression mapping dose and baseline covariates to Day 14 exposure achieved a reward of 0.499 against the 0.50 threshold, and the memo provided exact population parameters. This revision robustly hardens the benchmark while keeping the scoring formula and pass criteria invariant:

* Generative truth model: incorporated concentration-driven enzyme-turnover kinetics (replacing a static Hill model), absorption lag time, age-dependent CL0 scaling, increased inter-individual variability, raised LLOQ from 1.00 to 2.50 ng/mL, and intra-study dose titration (2x increase from Day 8 for ~70% of subjects).
* Sampling schedule: observations shifted to Days 1, 4, and 7 (previously 1, 7, and 11), strictly preceding titration.
* Data regeneration: visible training sets reconstructed; `rich/targets.csv` deleted to prevent leakage.
* Analysis memo: updated with authentic estimates derived from a naive non-compartmental model on the updated cohort.
* Graded forecast windows: configured to 1.05, 6.05, 24.05, and 47.95 h (replacing 0.25, 6.05, 24.05 h, as 0.25 h fell entirely within the absorption lag).
* `--estimator population`: verified for strict invariance properties rather than prediction accuracy, as expanded variability places fixed-effect accuracy near 0.508.
* Fallback rules: explicit fallback handling for missing or unparseable `age_years` defaulting to 45 years.
* Reference pipeline: updated and validated against the finalized autoinduction dynamics.

Because visible datasets, instructions, and verification logic were overhauled, prior benchmark evaluations are superseded.

## Studio runs

No platform studio runs recorded on this version yet. Evaluation tracking: reference and empty run IDs (Claude Opus 4.6), writer and reviewer Astra (Codex) benchmarks against token thresholds (< 0.67 / 400k), and reviewer Opus 5.5 benchmark (< 0.75).

## AI assistance

Anthropic Claude assisted with revision authoring: refining truth model kinetics, running calibration tests, updating reference solution scripts, test harness implementation, and documentation. The author established task scope, clinical problem formulation, and evaluation privacy design.

## Environment

* Agent image: `python:3.13-slim-bookworm` (pinned digest), numpy 2.2.3, scipy 1.15.2, pandas 2.2.3; unprivileged uid 1000; no internet access.
* Verifier image: identical base configuration with pytest 8.4.1 and pytest-json-ctrf 0.6.1; outputs structured CTRF reports to `/logs/verifier/ctrf.json`.
* 4 CPUs, 8192 MB RAM, execution directory `/app`.
