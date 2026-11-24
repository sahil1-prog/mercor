# censored-exposure-forecasting

**Category:** Science
**Subcategory:** Pharmacometrics and statistical modeling
**Difficulty:** Hard
**Estimated expert time:** 10 hours

This summary is prepared for reviewers; it is never visible to the agent.

---

## The task

A clinical pharmacology group requires Day 14 exposure projections (AUC, Cmax, and trough over `[t14, t14+H)`) for an incoming cohort in a once-daily oral multiple-ascending-dose trial. The drug stimulates its own elimination clearance, and approximately 70% of participants step up to twice their initial dose starting on Day 8. Pharmacokinetic sampling in the new cohort occurs exclusively on Days 1, 4, and 7—entirely before that dose escalation. The Day 14 forecast is therefore a true model extrapolation across dose and time rather than an interpolation from observed concentrations.

**Deliverable:** `/app/submission/predict.py`, run as:
`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`.
It emits `subject_id,auc_ss,cmax_ss,ctrough_ss` for each subject. During evaluation, only the input directory and `/app/submission` remain accessible; `/app/data`, `/app/baseline`, and `/app/analysis` are not present.

**Agent-visible material:**
- A dense cohort (`/app/data/rich`, 40 subjects): rich Day 1 and Day 14 profiles with daily trough monitoring.
- A sparse cohort matching the evaluation cohort's sampling protocol (`/app/data/sparse`, 60 subjects).
- A naive baseline predictor script.
- An internal team memo arguing for an incorrect model: stationary clearance, dose linearity, and omitting BLQ samples.

## What success looks like

A successful submission is a standalone prediction pipeline rather than a static table of values. It ingests any cohort conforming to the documented schema and outputs one complete, finite, and positive row per subject. On the unseen 150-subject evaluation cohort, median relative errors must satisfy `raw <= 0.25`, corresponding to `reward >= 0.50`. This threshold must hold across the default 24 h window and all graded windows (1.05, 6.05, 24.05, and 47.95 h). Submissions must also fulfill all behavioral requirements outlined in the instructions:
- reproducible, deterministic outputs;
- adherence to the 120 s runtime limit;
- leaving input directories and the submission directory unmodified;
- independent per-field covariate fallback handling;
- exact `--horizon-h` window integration while rejecting invalid arguments;
- a `population` estimator that ignores observed samples and subject IDs while responding to dose and covariates.

The overall task score is binary: 1 when all 23 verification tests pass, 0 otherwise.

Methodological choices remain unconstrained: candidates may employ any structural model, parameter estimation technique, or numerical solver. In practice, satisfying all criteria across every window requires three components:
- a pharmacokinetic model whose clearance evolves with drug exposure, carrying forward through titration and washout;
- likelihood estimation accounting for below-LLOQ samples rather than discarding them;
- empirical-Bayes individualization calibrated from sparse sampling records.

Measured reference anchors indicate where the performance boundaries lie:

| Anchor | raw at 24 h | reward | Outcome |
|---|---|---|---|
| Naive baseline | 1.768 | 0.000 | fails |
| Dose-and-covariate regression, no PK model | 0.514 | 0.000 | fails |
| Population typical values, no individual fit (true parameters) | 0.256 | 0.487 | fails |
| **Pass bar** | **0.250** | **0.500** | |
| Time-only induction, individualised (true parameters) | 0.181 | 0.638 | passes at 24 h, close to the bar at 47.95 h (0.511) |
| Correct structure, individualised (true parameters) | 0.120 | 0.760 | passes |
| Reference solution (fitted from visible data only) | 0.109 | 0.783 | passes all windows |

The following attempts fail closed and score 0:
- outputs that are missing, malformed, omit subjects, contain non-finite numbers, or fail determinism;
- predictors ignoring CLI flags or rounding window ends onto fixed integration grids;
- a `population` mode that accesses sample concentrations or censoring flags;
- models passing at 24 h but failing on any graded window;
- scripts writing outside the designated output path or modifying input directories.

Partial success, such as satisfying formatting checks without meeting accuracy thresholds, scores 0.

### The 1.0 witness

The reference implementation in `solution/` achieves a score of 1.0. Running `solve.sh` fits `fit_population.py` on `/app/data` and populates `/app/submission` with `predict.py`, `pk_engine.py`, and `pop_params.json`; this submission passes all 23 checks across local Docker runs of both container images. It provides an explicit proof of feasibility rather than a prescribed single approach. A continuous reward of 1.0 would require zero relative error across all metrics—unachievable under assay noise and censoring—but because task grading is binary, the passing witness receives 1.0.

## What makes it hard

The underlying data-generating model (`tests/.truth/truth_model.py`) incorporates:

* First-order oral absorption with an absorption delay (typical 0.4 h) entering a central compartment.
* Systemic clearance `CL0 * E(t)`, where enzyme turnover is driven by circulating drug concentration: `dE/dt = kenz * (1 + Emax*C/(EC50+C)) - kenz*E`. Autoinduction kinetics therefore reflect cumulative dosing history: dose escalation further elevates enzyme levels, while skipped doses permit clearance to decay back toward baseline.
* Patient covariates: allometric body weight scaling on CL0 and V, power scaling of age on CL0, and biological sex effects on CL0 and V.
* Inter-individual variability (IIV) across all 7 parameters (30–40% CV on CL0, Emax, and EC50).
* An analytical assay LLOQ of 2.50 ng/mL: 45% of rich-cohort observations and 25% of sparse-cohort observations are censored, predominantly the trough measurements that contain the autoinduction signal.

A successful solution must identify this structural behavior from data despite the contradictory memo. It must subsequently:
- fit a population PK model under a censored-data likelihood;
- estimate empirical-Bayes individual random effects from just 7 sparse samples per subject;
- integrate exposure forward through an intra-study dose change that unobserved test samples never cover.

**Strongest public prior work, and why it is not enough.** Standard industry packages for these problems include nonlinear mixed-effects (NLME) suites:
- NONMEM and Monolix (commercial);
- nlmixr2, saemix, and mrgsolve in R;
- Pumas in Julia;
- Pharmpy in Python.

Open-source Python NLME engines now exist as well: PKPy2 (github.com/Gumgo91/PKPy2, MIT; joint Laplace and importance-sampling NLME with M3/M4 censoring and user-defined ODEs) and OpenPKPD (github.com/breisfeld/OpenPKPD; FO/FOCE/Laplace/SAEM with M3/M4). PKPDmap (github.com/InsightRX/PKPDmap, MIT) and mapbayr (github.com/FelicienLL/mapbayr) are reference implementations of MAP Bayesian individualisation. The closest public benchmark task is terminal-bench-science proposal #121 (discussion #208, approved): a 2-compartment population fit to 24 sparse Phase 1 subjects with BLQ samples, graded on the recovered population parameters. It has no induction, no dose change and no forecast, and its agent may install packages from the internet. This task differs on each of those points: it is offline, it requires a forecast, and the forecast must extrapolate through a concentration-driven, history-dependent clearance change.

Beal's M3 method for censored observations and turnover-based autoinduction models (such as those described for rifampicin and carbamazepine) are standard literature. However, none of these libraries are installed, and network access is disabled in the container environment. The image provides standard numpy, scipy, and pandas only.

Even with an NLME suite present, core engineering challenges remain. Tooling fits only the structural equations specified by the user and cannot automatically infer that clearance is concentration-driven and history-dependent. Recognizing that behavior requires contrasting Day 14 rich-cohort data against Day 1 profiles, discarding the memo's conclusions, and formulating a model that extrapolates through titration. The test cohort never observes that dose change. As demonstrated in calibration experiments, the plausible shortcut of time-only induction fits the training data yet drops to the edge of the pass bar on the long washout window (reward 0.511 at 47.95 h, against 0.658 for the concentration-driven model).

**Data fidelity.** All trial data is synthetic, simulated from the model described above under fixed seeds. Parameter values reflect realistic clinical ranges for an orally administered small molecule:
- baseline CL0 15 L/h, V 300 L, ka 1.1 /h;
- enzyme turnover half-life of 96 h;
- assay LLOQ with proportional-plus-additive residual error (12% and 0.30 ng/mL), delayed doses (15%), and missed doses (6%) typical of Phase 1 trials.

Synthetic data generation ensures ground truth for the evaluation cohort is mathematically exact and strictly private.

**Who does this work.** This task mirrors the responsibilities of a quantitative clinical pharmacologist or pharmacometrician in sponsor drug development. Following a multiple-ascending-dose trial, the analyst constructs the population PK framework supporting the Phase 2 dose-selection package. They project steady-state exposure for proposed dosing schedules in sparsely sampled populations, directly informing efficacy margins and safety boundaries.

### Calibration with the true generating parameters (default 24 h window)

Each configuration below evaluates a MAP predictor initialized with true population values, isolating one specific structural deviation:

| Predictor | raw | reward | passes |
|---|---|---|---|
| correct structure | 0.120 | 0.760 | yes |
| induction that ignores concentration (time-only) | 0.181 | 0.638 | yes at 24 h; 0.511 at 47.95 h |
| no induction | 1.444 | 0.000 | no |
| population typical values only, no individual fit | 0.256 | 0.487 | no |

Time-only induction fails to capture the dose escalation step and subsequent clearance decay during washout. While it passes at 24 h, it nears the failure threshold at 47.95 h, where the concentration-driven formulation maintains 0.658.

## Scoring

For each subject and metric: `r = |pred - true| / true`.
`raw = 0.5*median(r_AUC) + 0.3*median(r_Cmax) + 0.2*median(r_Ctrough)`.
`reward = clip(1 - raw/0.5, 0, 1)`; a submission passes when `reward >= 0.50` (`raw <= 0.25`).
The mathematical formula, weighing coefficients, and passing bar are preserved from the original task design and remain unchanged. The candidate-facing instructions state only the `raw` loss metric; the reward transformation and pass criteria appear solely in `task.toml`, `tests/test_outputs.py`, and this document.

The final evaluation grade is binary: 1 when all 23 checks pass (including accuracy at default and all test windows), 0 otherwise.

### Why these constants

* **Weights 0.5 / 0.3 / 0.2.** AUC serves as the primary metric for systemic exposure in dose selection, receiving half the weight. Cmax governs peak-related toxicity and carries the next largest weight. Ctrough receives lower weight because it represents the noisiest endpoint, sitting near the assay LLOQ where measurements are frequently censored. Even the reference model exhibits its largest relative error on troughs (median 0.250 at 24 h and 0.524 at 47.95 h, compared to 0.08–0.09 for AUC). Placing higher weight on Ctrough would primarily reward noise near the quantification limit.
* **Median, not mean.** Taking medians prevents outlier subjects with atypical parameter draws from distorting the score, measuring accuracy across typical subjects as required for clinical dose selection.
* **Normaliser 0.5.** A 50% median relative error (`raw = 0.5`) maps directly to reward 0. Measured non-PK shortcuts fall at or beyond this boundary: the multivariable regression shortcut scores 0.514 and the naive baseline scores 1.768. Only predictors with a viable PK model achieve positive reward.
* **Pass bar `reward >= 0.50` (`raw <= 0.25`).** This boundary cleanly separates two classes of predictors:
  - Individualized models with autoinduction pass at 24 h: raw 0.120 using true parameters, 0.109 for the reference solution, and 0.181 for time-only induction. At 47.95 h, the reference solution maintains raw 0.166, while time-only induction nears the passing boundary (reward 0.511).
  - Predictors lacking individualization fail even with exact population parameters (raw 0.256), as do models omitting induction entirely (raw 1.444).
  The threshold ensures models fit appropriate subject-level dynamics without demanding precision beyond what sparse data can support.
* **Graded windows 1.05 / 6.05 / 24.05 / 47.95 h.** The 1.05 h window evaluates uptake just past the absorption delay. The 0.05 h offsets place window boundaries off whole integration intervals. The 47.95 h window extends nearly a full day into washout beyond the next scheduled dose, revealing discrepancies in clearance dynamics. The reference solution scores 0.806 / 0.872 / 0.782 / 0.667 on these windows.

## Measured results (local Docker, both images built from this package)

All runs executed under `--network none --cpus 4 --memory 8g`, using the agent image for solving and the verifier image for evaluation as specified in `task.toml`.

| Run | Pipeline reward | Detail |
|---|---|---|
| oracle (reference), grade 1 | 1 | 23/23 passed, suite 132 s |
| oracle (reference), grade 2 | 1 | 23/23 passed, suite 134 s; fresh `solve.sh` into a new volume gave a byte-identical submission |
| nop (empty submission) | 0 | fails closed: no `/app/submission/predict.py` |
| naive baseline (`/app/baseline/predict.py`) | 0 | 12/23; fails the accuracy bar (raw 1.768 at 24 h) |
| constant output | 0 | 11/23; one value copied to every subject, fails the accuracy bar |
| dose-and-covariate regression shortcut | 0 | 13/23; fails the accuracy bar at raw 0.514 (it scored reward 0.499 before this revision) |

Reference continuous rewards on the held-out cohort, scored in the verifier image:

| Window | raw | reward | median rel. error AUC / Cmax / Ctrough |
|---|---|---|---|
| 24 h (default) | 0.109 | 0.783 | 0.083 / 0.057 / 0.250 |
| 1.05 h | 0.097 | 0.806 | 0.115 / 0.079 / 0.079 |
| 6.05 h | 0.064 | 0.872 | 0.060 / 0.057 / 0.085 |
| 24.05 h | 0.109 | 0.782 | 0.083 / 0.057 / 0.251 |
| 47.95 h | 0.166 | 0.667 | 0.089 / 0.057 / 0.524 |
| 24 h, `--estimator population` (not graded for accuracy) | 0.246 | 0.508 | 0.178 / 0.118 / 0.610 |

Runtime: `solve.sh` takes roughly 22 s, and full-cohort prediction takes 3.6 s against the 120 s budget.

## Verification

* The verifier synthesizes the 150-subject held-out cohort within its container via `tests/.truth/heldout.py` using a fixed seed; this cohort is never present in the solver image. Ground-truth values are integrated across a 0.02 h node grid with exact node placement at every dose event and window boundary.
* No input samples occur within the Day 14 evaluation window (`assert_no_day14_samples`).
* Predictors execute under the unprivileged agent account against root-owned, read-only inputs. Output files are read using `O_NOFOLLOW`. Input datasets and submission files are cryptographically hashed before and after evaluation.

**Infrastructure failure.** `tests/test.sh` writes reward 0 before execution begins, updating it only upon a clean pytest exit. It fails closed, retaining reward 0 and emitting messages to stderr, if:
- the reward directory cannot be locked;
- the agent user is absent or root;
- `setpriv` fails to drop privileges;
- agent processes cannot be terminated;
- `/app/submission/predict.py` is missing.

Submissions that crash, exceed runtime limits (180 s per-run ceiling), or are terminated by the host fail test assertions and score 0. Verifier-level timeouts (3000 s allocation; suite executes in 132–134 s) leave the default 0 reward intact. Infrastructure faults never yield a passing score and should be rerun rather than evaluated.

**Regrade tolerance (0.0).** Measured characteristics:
- The evaluation cohort and ground-truth values derive deterministically from a fixed seed.
- The test suite requires repeated evaluations to produce identical numeric results (`test_deterministic`, `test_optional_modes_are_deterministic`).
- Consecutive verification runs of the reference solution both scored 1.0, with a fresh `solve.sh` run producing byte-identical submission artifacts.

Given deterministic evaluation and binary scoring, observed variance is 0.0, establishing a regrade tolerance of 0.0.

### Requirement-to-check map (`tests/test_outputs.py`, 23 checks)

| Instruction requirement | Checks |
|---|---|
| Runs the documented CLI and writes OUT.csv | `test_submission_predicts` |
| Columns, one row per subject, finite positive values | `test_output_format` |
| Finishes within the runtime budget | `test_runtime_budget` |
| Accuracy on the held-out cohort (reward >= 0.50) | `test_accuracy_pass_bar` |
| Two runs agree | `test_deterministic`, `test_optional_modes_are_deterministic` |
| Writes only OUT.csv; inputs and submission untouched | `test_inputs_unmodified` |
| Covariate fallbacks (70 kg, 45 years, M), per field, others unchanged | `test_covariates_missing_row_falls_back`, `test_covariates_unusable_weight_falls_back`, `test_covariates_unusable_sex_falls_back`, `test_covariates_unusable_age_falls_back`, `test_covariates_fallback_matches_default_row`, `test_covariates_fallback_leaves_others_unchanged` |
| `--horizon-h`: default = 24, exact window, graded accuracy, invalid values rejected | `test_horizon_default_matches_explicit_24`, `test_horizon_accuracy_matches_truth`, `test_horizon_window_additivity`, `test_horizon_rejects_invalid_values` |
| `--estimator`: default = map, population ignores samples and ids, responds to dose and weight, differs from map, invalid values rejected | `test_estimator_default_matches_explicit_map`, `test_estimator_population_ignores_samples`, `test_estimator_population_ignores_subject_id`, `test_estimator_population_responds_to_dosing_and_covariates`, `test_estimator_population_differs_from_map`, `test_estimator_rejects_invalid_values` |

## Changes in this revision

In the previous release, a 15-line linear regression of dose and covariates scored reward 0.499 against the 0.50 threshold, and the memo provided exact population parameters. This revision strengthens evaluation rigor while preserving the scoring formula and pass criteria:

* **Truth model:** concentration-driven enzyme-turnover autoinduction (replacing a static Hill model), absorption lag, age scaling on CL0, wider parameter variance, LLOQ raised from 1.00 to 2.50 ng/mL, and intra-study dose titration (2x increase from Day 8 for ~70% of subjects).
* **Sparse design:** sampling on Days 1, 4, and 7 (previously 1, 7, and 11), strictly preceding dose titration.
* **Data:** visible cohorts regenerated; `rich/targets.csv` deleted.
* **Memo:** rewritten with authentic parameter estimates derived from a naive model fit on the updated data.
* **Graded windows:** updated to 1.05, 6.05, 24.05, and 47.95 h (previously 0.25, 6.05, and 24.05 h, as 0.25 h fell entirely within the absorption delay).
* **`--estimator population`:** evaluated for sample-invariance properties rather than prediction accuracy, since expanded variability places population-only forecasts near 0.508.
* **Age fallback:** explicit fallback to 45 years for missing or invalid `age_years`.
* **Reference solution:** updated and verified against the refined autoinduction dynamics.
* **Agent-facing instruction:**
  - no narrative preamble;
  - no compute allocation or package list;
  - no hint about persisting a model;
  - no reward transform or pass bar (only the `raw` metric is stated);
  - formulas in LaTeX math.
* **`[agent].user`:** designated user account `"agent"` rather than numeric uid.

Because visible data, candidate instructions, and test grading logic were updated, prior benchmark evaluations are superseded.

## Studio runs

No Studio runs recorded on this version yet. Evaluation targets:
- oracle and nop (Claude Opus 4.6);
- writer Astra (Codex) evaluation (target < 0.67 with > 400k uncached tokens);
- reviewer Opus 5.5 evaluation (target < 0.75).

## AI assistance

Anthropic Claude assisted with revision authoring: refining truth model dynamics, running calibration benchmarks, updating reference solution scripts, adjusting test assertions, and documentation. The author defined the clinical challenge, task scope, and private evaluation design.

## Environment

* **Agent image:** `python:3.13-slim-bookworm` (pinned digest) with numpy 2.2.3, scipy 1.15.2, and pandas 2.2.3. Executes under non-root user `agent` (uid 1000) with no network access.
* **Verifier image:** identical base configuration plus pytest 8.4.1 and pytest-json-ctrf 0.6.1. Emits CTRF reports to `/logs/verifier/ctrf.json`.
* **Resources:** 4 CPUs, 8192 MB RAM; working directory `/app`.
