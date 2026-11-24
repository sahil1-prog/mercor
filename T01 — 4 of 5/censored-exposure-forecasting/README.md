# censored-exposure-forecasting

**Category:** Science
**Subcategory:** Pharmacometrics and statistical modeling
**Difficulty:** Hard
**Estimated expert time:** 10 hours

This summary is for reviewers. The agent never sees this file.

---

## The task

A clinical pharmacology group needs Day 14 exposure forecasts (AUC, Cmax and trough over `[t14, t14+H)`) for an incoming cohort of a once-daily oral multiple-ascending-dose trial. The compound induces its own clearance, and about 70% of subjects double their dose from Day 8. The incoming cohort is sampled only on Days 1, 4 and 7, all before the titration step. The Day 14 forecast is therefore an extrapolation that rests on the model, not on observations.

**Deliverable:** `/app/submission/predict.py`, run as
`python3 /app/submission/predict.py --input-dir DIR --output OUT.csv [--horizon-h H] [--estimator E]`.
It writes `subject_id,auc_ss,cmax_ss,ctrough_ss` for every subject. At evaluation time only the input directory and `/app/submission` exist; `/app/data`, `/app/baseline` and `/app/analysis` are absent.

**Agent-visible material:**
- A rich cohort (`/app/data/rich`, 40 subjects): Day 1 and Day 14 profiles plus daily troughs.
- A sparse cohort with the same design as the evaluation cohort (`/app/data/sparse`, 60 subjects).
- A naive baseline predictor.
- An internal memo that argues for the wrong model: stationary clearance, dose-linear scaling, and dropping BLQ samples.

## What success looks like

A successful submission is a self-contained forecasting program, not a table of numbers. It reads any cohort in the documented schema and returns one complete, finite and positive row per subject. On the 150-subject cohort it has never seen, its median relative errors keep `raw <= 0.25`, so `reward >= 0.50`. That must hold at the default 24 h window and at every graded window (1.05, 6.05, 24.05 and 47.95 h). It must also honour every behavioural contract in the instruction:
- deterministic output;
- the 120 s budget;
- inputs and the submission directory left untouched;
- per-field covariate fallbacks;
- exact `--horizon-h` windows and rejection of invalid values;
- a `population` estimator that ignores samples and ids but responds to dose and covariates.

The task's score is binary: 1 when all 23 checks pass, 0 otherwise.

How a solution gets there is open. It can use any structural model, estimation method or numerical scheme. In practice, clearing the bar at every window needs three things:
- a model whose clearance changes with drug exposure, so it carries through the titration and washout;
- a fit that uses the below-LLOQ samples rather than discarding them;
- per-subject individualisation from the sparse samples.

The measured anchors (details below) show where the bar sits:

| Anchor | raw at 24 h | reward | Outcome |
|---|---|---|---|
| Naive baseline | 1.768 | 0.000 | fails |
| Dose-and-covariate regression, no PK model | 0.514 | 0.000 | fails |
| Population typical values, no individual fit (true parameters) | 0.256 | 0.487 | fails |
| **Pass bar** | **0.250** | **0.500** | |
| Time-only induction, individualised (true parameters) | 0.181 | 0.638 | passes at 24 h, close to the bar at 47.95 h (0.511) |
| Correct structure, individualised (true parameters) | 0.120 | 0.760 | passes |
| Reference solution (fitted from visible data only) | 0.109 | 0.783 | passes all windows |

These attempts do not count, and each scores 0:
- an output that is missing, malformed, missing a subject, non-finite or non-reproducible;
- a predictor that ignores the flags or snaps windows to a grid;
- a `population` mode that still reads samples;
- one that only passes at 24 h but fails a graded window;
- one that writes extra files or modifies inputs.

Partial progress, such as passing the format checks without the accuracy bar, also scores 0.

### The 1.0 witness

The reference solution in `solution/` scores 1.0. `solve.sh` fits `fit_population.py` on `/app/data`, then installs `predict.py`, `pk_engine.py` and `pop_params.json`, and the result passes all 23 checks in local Docker runs of both images. It is one way to reach the outcome above, not the expected method. A continuous reward of 1.0 would require zero median error, which censored, noisy sparse data does not allow. The graded score is binary, so the witness scores 1.0.

## What makes it hard

The data-generating model (`tests/.truth/truth_model.py`) has these features:

* Oral absorption after a lag (typical 0.4 h) into one central compartment.
* Clearance `CL0 * E(t)`. Enzyme turnover is driven by drug concentration: `dE/dt = kenz * (1 + Emax*C/(EC50+C)) - kenz*E`. Induction therefore depends on dosing history: the titration step raises it further, and missed doses let it relax.
* Covariates: allometric weight on CL0 and V, a power of age on CL0, and sex on CL0 and V.
* Between-subject variability on all 7 parameters (30-40% CV on CL0, Emax and EC50).
* An LLOQ of 2.50 ng/mL. 45% of rich-cohort samples and 25% of sparse-cohort samples are censored, mostly the post-dose troughs that carry the induction signal.

A solution has to find this structure in the data, against the memo's advice. It then has to:
- fit a population PK model under a censored likelihood;
- produce empirical-Bayes individual estimates from 7 sparse samples per subject;
- integrate forward through a dose change that no input sample covers.

**Strongest public prior work, and why it is not enough.** The standard tools here are nonlinear mixed-effects (NLME) packages:
- NONMEM and Monolix (commercial);
- nlmixr2, saemix and mrgsolve in R;
- Pumas in Julia;
- Pharmpy in Python.

The Beal M3 method for BLQ data and enzyme-turnover autoinduction models (as published for artemisinin and rifampicin) are textbook material. None of these tools is installed, and the environment has no network, so they cannot be fetched. The image provides numpy, scipy and pandas only.

Even with an NLME engine, the hard part would remain. These tools fit whatever model the analyst writes, and they do not detect that clearance is concentration-driven and history-dependent. Doing so means reading the Day 14 rich-cohort profiles against Day 1, rejecting the memo, and choosing a structure that extrapolates through a titration step. The scoring cohort never observes that step. The calibration table below shows that the wrong but plausible choice (time-only induction) fits the visible data and still loses accuracy at the long window.

**Data fidelity.** All data is synthetic, generated from the model above with fixed seeds. The parameter values sit in a physiologically ordinary range for an oral small molecule:
- typical CL0 15 L/h, V 300 L, ka 1.1 /h;
- an enzyme half-life of 96 h;
- an LLOQ, proportional-plus-additive assay error (12% and 0.30 ng/mL), late doses (15%) and missed doses (6%) of the kind seen in early-phase trials.

The data is synthetic so that the ground truth for the held-out cohort is exact and private.

**Who does this work.** This is the job of a clinical pharmacometrician (clinical pharmacology or quantitative pharmacology group) in sponsor drug development. At the end of a multiple-ascending-dose study, that analyst builds the population PK model behind the Phase 2 dose-selection package. They forecast exposure at the proposed regimens for subjects with only sparse sampling, and the dose and safety-margin decision depends on those forecasts.

### Calibration with the true generating parameters (default 24 h window)

Each row is a MAP predictor given the true population values, with one structural change:

| Predictor | raw | reward | passes |
|---|---|---|---|
| correct structure | 0.120 | 0.760 | yes |
| induction that ignores concentration (time-only) | 0.181 | 0.638 | yes at 24 h; 0.511 at 47.95 h |
| no induction | 1.444 | 0.000 | no |
| population typical values only, no individual fit | 0.256 | 0.487 | no |

Time-only induction does not follow the titration step or the washout. It passes at 24 h but comes close to the bar at 47.95 h, where the concentration-driven model holds 0.658.

## Scoring

For each subject and quantity: `r = |pred - true| / true`.
`raw = 0.5*median(r_AUC) + 0.3*median(r_Cmax) + 0.2*median(r_Ctrough)`.
`reward = clip(1 - raw/0.5, 0, 1)`; a submission passes when `reward >= 0.50` (`raw <= 0.25`).
The formula, weights and pass bar are frozen from the original task specification and were not changed in this revision. The agent-facing instruction gives the `raw` metric only. The reward transform and the pass bar appear only here, in `task.toml` and in `tests/test_outputs.py`.

The final reward is binary: 1 when all 23 checks pass, including the accuracy bar at the default window and at each graded window, and 0 otherwise.

### Why these constants

* **Weights 0.5 / 0.3 / 0.2.** AUC is the primary exposure metric for dose selection, so it carries half the weight. Cmax drives peak-related safety, so it is next. Ctrough gets the least weight because it is the noisiest target: it sits near the LLOQ, where the visible data is most often censored. Even the reference has its largest trough error there (median 0.250 at 24 h and 0.524 at 47.95 h, against 0.08-0.09 for AUC). A heavier trough weight would mostly reward luck at the assay floor.
* **Median, not mean.** A handful of subjects with extreme parameter draws would otherwise dominate the score. The median measures typical-subject accuracy, which is what a dose-selection forecast is used for.
* **Normaliser 0.5.** A 50% median error (`raw = 0.5`) maps to reward 0. The measured non-model shortcuts land at or beyond that point: the regression shortcut at 0.514 and the naive baseline at 1.768. Only predictors with a working PK model score above 0.
* **Pass bar `reward >= 0.50` (`raw <= 0.25`).** The bar falls between two groups of measured predictors:
  - Individualised predictors with an induction model pass at 24 h: 0.120 with true parameters, 0.109 for the reference and 0.181 for the time-only variant. At 47.95 h the reference measures raw 0.166 and the time-only variant comes close to the bar (reward 0.511).
  - Predictors that do not individualise fail even with exact population parameters (raw 0.256), as do models without induction (1.444).

  So the bar separates "fits the right kind of model to each subject" from "uses only population or covariate information". It does not demand accuracy beyond what the data supports.
* **Graded windows 1.05 / 6.05 / 24.05 / 47.95 h.** 1.05 h sits just past the absorption lag. The 0.05 offsets keep window ends off any whole integration step. 47.95 h runs nearly a full day past the time the next dose would be due, into washout, where the wrong induction structure shows up. The reference measures 0.806 / 0.872 / 0.782 / 0.667 on them.

## Measured results (local Docker, both images built from this package)

All runs used `--network none --cpus 4 --memory 8g`, the agent image for solving and the verifier image for grading, as in `task.toml`.

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

Runtime: `solve.sh` takes about 22 s, and a full-cohort forecast takes 3.6 s against the 120 s budget.

## Verification

* The verifier builds the 150-subject held-out cohort inside its own container from `tests/.truth/heldout.py`, using a fixed seed. The cohort never exists in the agent image. Truth is integrated on a 0.02 h grid with exact nodes at every dose entry and window boundary.
* No input sample falls inside the Day 14 window (`assert_no_day14_samples`).
* Predictions run as the unprivileged agent user against root-owned, read-only inputs. Output is opened with `O_NOFOLLOW`. Inputs and the submission tree are hashed before and after every run.

**Infrastructure failure.** `tests/test.sh` writes reward 0 before anything runs and replaces it only on a clean pytest exit. It fails closed, keeping the reward at 0 with a message on stderr, in these cases:
- the reward directory cannot be locked;
- the agent user is missing or is root;
- `setpriv` cannot drop privileges;
- agent processes cannot be stopped;
- `/app/submission/predict.py` is missing.

A predictor that crashes, times out (per-run limit 180 s) or is killed by the host fails its checks and scores 0. A verifier-level timeout (3000 s; the measured suite takes 132-134 s) leaves the pre-written 0. An infrastructure fault therefore never yields a 1. It shows up as a 0 with a verifier message and should be rerun, not counted.

**Regrade tolerance (0.0).** These are the measured facts:
- The held-out cohort and its truth come from a fixed seed.
- The suite requires repeated runs to produce identical numbers (`test_deterministic`, `test_optional_modes_are_deterministic`).
- Two full grades of the reference both scored 1. The second came from a fresh `solve.sh` into a new volume and produced a byte-identical submission.

With a binary score and a deterministic predictor, the measured span is 0.0, so the tolerance is set to 0.0.

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

In the previous release, a 15-line regression of dose and covariates on Day 14 exposure scored reward 0.499 against the 0.50 bar, and the memo printed the exact population parameters. This revision hardens what is measured and leaves the scoring formula and pass bar unchanged:

* **Truth model:** concentration-driven enzyme-turnover induction (replacing a time-only Hill model), absorption lag, age on CL0, wider variability, LLOQ raised from 1.00 to 2.50 ng/mL, and a 2x titration from Day 8 for about 70% of subjects.
* **Sparse design:** Days 1, 4 and 7 (previously 1, 7 and 11), all before titration.
* **Data:** visible data regenerated; `rich/targets.csv` removed.
* **Memo:** rewritten with real naive-fit estimates on the new data.
* **Graded windows:** 1.05, 6.05, 24.05 and 47.95 h (previously 0.25, 6.05 and 24.05 h; 0.25 h fell inside the lag).
* **`--estimator population`:** checked for its invariance properties, not accuracy. The wider variability puts typical-value accuracy at about 0.508, too close to the bar to grade fairly.
* **Age fallback:** 45 years for missing or unusable `age_years`.
* **Reference solution:** rewritten for the new model.
* **Agent-facing instruction:**
  - no narrative preamble;
  - no compute allocation or package list;
  - no hint about persisting a model;
  - no reward transform or pass bar (only the `raw` metric is stated);
  - formulas in LaTeX math.
* **`[agent].user`:** the named account `"agent"` rather than a numeric uid.

The visible data, instruction and grader all changed, so runs on earlier revisions are void.

## Studio runs

No Studio runs on this upload yet. Still to run:
- oracle and nop (Claude Opus 4.6);
- the writer's Astra (Codex) run (target < 0.67 with more than 400k uncached tokens);
- the reviewer's Opus 5.5 run (target < 0.75).

## AI assistance

Anthropic Claude helped with this revision: the truth-model changes, calibration runs, the reference solution, the test suite and the documentation. The author set the task scope, the clinical framing and the evaluation privacy design.

## Environment

* **Agent image:** `python:3.13-slim-bookworm` (pinned digest) with numpy 2.2.3, scipy 1.15.2 and pandas 2.2.3. It runs as the non-root user `agent` (uid 1000), with no network.
* **Verifier image:** the same base plus pytest 8.4.1 and pytest-json-ctrf 0.6.1. It writes a CTRF report to `/logs/verifier/ctrf.json`.
* **Resources:** 4 CPUs, 8192 MB RAM; working directory `/app`.
