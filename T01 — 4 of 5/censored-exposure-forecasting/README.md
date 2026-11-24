# censored-exposure-forecasting

**Category:** Science  
**Subcategory:** Pharmacometrics and statistical modeling  
**Difficulty:** Hard  
**Estimated expert time:** 10 hours

---

## Difficulty explanation

### Real-World Practitioner Context
In pharmaceutical drug development, this task reflects the core workflow of **clinical pharmacologists** and **pharmacometricians** conducting Phase 1 Multiple Ascending Dose (MAD) clinical trials. Before advancing a drug candidate to Phase 2 trials in patients, the team must forecast steady-state exposure (AUC, $C_{\max}$, and $C_{\text{trough}}$) at day 14 to select safe and efficacious dose regimens.

Many candidate small molecules induce their own metabolizing enzymes (e.g., autoinduction of CYP3A4 by carbamazepine, rifampicin, or efavirenz). In such cases, apparent clearance ($CL/F$) increases over time from baseline clearance ($CL_0$) toward an asymptotic induced level ($CL_0 \cdot [1 + E_{\max}]$). The critical challenge is that the earliest evidence of induction appears in sparse trough concentrations collected during the first week of daily dosing, where drug concentrations frequently fall below the assay Lower Limit of Quantification (LLOQ = 1.00 ng/mL).

### Nature and Realism of Shipped Study Data
The visible study cohorts provided under `/app/data/rich` (~40 densely sampled subjects with day-1 and day-14 profiles) and `/app/data/sparse` (~60 sparsely sampled subjects with targets withheld) are synthetically simulated from an oral one-compartment autoinduction PK model. The simulation realistically reflects Phase 1 MAD trial data, incorporating physiological inter-individual variability (log-normal distributions on absorption rate $k_a$, distribution volume $V$, baseline clearance $CL_0$, maximum induction effect $E_{\max}$, and onset half-time $ET_{50}$), allometric body weight scaling, sex effects on clearance, proportional plus additive assay noise, and realistic LLOQ (1.00 ng/mL) left-censoring.

### Why Standard Heuristics Fail
* **Discarding BLQ samples (Beal's M1 method):** Excludes low concentration records, creating a severe upward survival bias in observed concentrations that masks the clearance increase and underpredicts induction.
* **LLOQ/2 or LLOQ substitution (Beal's M5/M6 methods):** Imputes arbitrary constant step values that distort individual parameter shrinkage, artificially flatten the concentration curve, and bias clearance and volume estimation.
* **Assuming constant (time-invariant) clearance:** Grossly overestimates day-14 steady-state exposure (overpredicting AUC by 40–80%), risking suboptimal, ineffective, or toxic dose selection.

### Public Prior Work & Remaining Engineering Challenge
In standard academic and industrial practice, pharmacometricians rely on specialized commercial or specialized non-linear mixed effects (NLME) software packages such as NONMEM, Monolix, or R's `saemix` and `nlme`. Standard literature provides foundational theory:
1. **Beal (2001) Ways to Treat BLQ Data:** Establishes the M3 censored-likelihood method, which treats BLQ records not as missing points or arbitrary numbers, but as censored data contributing $P(y < \text{LLOQ}) = \Phi\left(\frac{\text{LLOQ} - \hat{y}}{\sigma}\right)$ to the individual log-likelihood.
2. **Turnover-based enzyme autoinduction kinetics:** Describes time-varying clearance via enzyme turnover kinetics or empirical onset models: $CL(t) = CL_0 \cdot \left(1 + E_{\max} \cdot \frac{t}{ET_{50} + t}\right)$.

However, public tools and packages cannot be used directly by an agent:
* No dedicated NLME solver or R runtime is installed in the container; the agent must implement the entire simulation, censored likelihood, and parameter estimation engine from scratch in pure Python using NumPy and SciPy.
* The solution must execute offline without network access within a strict 120-second wall-clock budget (180s verifier host ceiling) for 150 subjects on 4 CPUs, ruling out computationally heavy MCMC or unvectorized numerical ODE integrators.
* The agent must build an identifiable population prior model from visible rich data, implement empirical-Bayes Maximum A Posteriori (MAP) estimation for sparse subjects (some of whom have all-censored samples), support exact continuous-time horizon scaling $[t_{14}, t_{14}+H)$, implement covariate fallbacks, and support both `map` and sample-invariant `population` estimation modes.

---

## What success looks like

### Successful Outcome
A successful submission delivers an executable, self-contained Python script at `/app/submission/predict.py`. Given an unseen cohort directory with `subjects.csv`, `doses.csv`, `samples.csv`, and `covariates.csv`, the script writes `OUT.csv` with columns `subject_id,auc_ss,cmax_ss,ctrough_ss`.

The submission succeeds by achieving a normalized evaluation reward $\ge 0.50$ (raw error metric $\le 0.25$) when scored against ground truth day-14 steady-state exposures on a sealed held-out cohort of 150 subjects.

### Key Properties of Strong Solutions
* **Censored-Likelihood PopPK:** Leverages Beal's M3 method with Gaussian cumulative distribution functions for BLQ samples rather than naive truncation or substitution.
* **Autoinduction Parameterization:** Models clearance as an increasing function of time or enzyme accumulation, properly identifying baseline clearance $CL_0$, maximum induction effect $E_{\max}$, and onset half-time $ET_{50}$.
* **Stable Empirical-Bayes MAP Inversion:** Combines population priors with individual sample likelihoods, using regularized shrinkage so subjects with sparse or BLQ measurements shrink toward population-mean parameters rather than diverging.
* **Arbitrary Forecast Horizon (`--horizon-h H`):** Computes exact exposure over $[t_{14}, t_{14}+H)$ using exact analytical superposition or fine-mesh integration, preserving window additivity ($dAUC \approx \Delta \cdot C_{\text{trough}}$) without fixed grid rounding.
* **Dual Estimator Modes (`--estimator {map, population}`):** Correctly switches between individualized empirical-Bayes MAP estimation and population-mean prediction (which depends strictly on dosing and covariates, remaining 100% invariant to sample values, censor flags, and row counts).
* **Robust Covariate Fallbacks:** Resiliently falls back to 70 kg for missing/invalid body weight and 'M' for missing/invalid sex, evaluating valid fields per-subject without failing or altering predictions for other subjects.
* **Deterministic & Fast:** Finishes within 120 seconds of wall-clock time on 4 CPUs (well inside the 180s ceiling) and produces bitwise deterministic output across replicate runs.

### What Does Not Count
* Submissions omitting autoinduction (constant clearance) or dropping BLQ records: these yield raw error $> 0.35$ (reward $< 0.30$), failing the 0.50 pass threshold.
* Submissions that snap `--horizon-h` to whole days or coarse grid steps: these fail calculus-based window additivity assertions.
* Population mode implementations that leak or read sample data: these fail invariance checks.
* Scripts that fail on malformed covariates or write extra files to input/submission directories.

### Measured Score Anchors
* **Floor:** 0.0
* **Baseline (Naive):** 0.0 (continuous reward: 0.000, raw error: 0.621; fails the $\ge 0.50$ pass bar)
* **Reference Solution:** 1.0 (continuous reward: 0.728, raw error: 0.136; passes all 23 verification checks)
* **Ceiling / Oracle:** 1.0
* **Regrade Tolerance:** 0.0 (fully deterministic across independent executions)

---

## Verification explanation

### Scoring Metric Derivation
For each subject $i$ and pharmacokinetic target $m \in \{AUC_{ss}, C_{\max,ss}, C_{\text{trough},ss}\}$, the relative forecast error is:
$$r_{i,m} = \frac{|\hat{y}_{i,m} - y_{i,m}|}{y_{i,m}}$$

The aggregate raw metric combines median relative errors across subjects with clinical importance weights:
$$\text{raw} = 0.5 \cdot \text{median}(r_{AUC}) + 0.3 \cdot \text{median}(r_{C\max}) + 0.2 \cdot \text{median}(r_{C\text{trough}})$$

The normalized reward is computed by scaling against a maximum acceptable error of 0.50:
$$\text{reward} = \text{clip}\left(1 - \frac{\text{raw}}{0.50}, 0, 1\right)$$

The task is scored as a binary pass/fail gate: the final pipeline score is **1.0** if $\text{reward} \ge 0.50$ ($\text{raw} \le 0.25$) and all requirement gates pass; otherwise **0.0**.

### Threshold & Normalization Calibration
* The normalizing constant 0.50 represents an unacceptable prediction error where clinical dose decisions would fail (e.g., $50\%$ error in AUC).
* The pass bar 0.50 ($\text{raw} \le 0.25$) requires median relative errors across all three steady-state parameters to remain below 25%.
* Empirical calibration shows:
  * The naive baseline (empirical non-compartmental summary) produces $\text{raw} = 0.621 \implies \text{reward} = 0.0$, cleanly failing below the 0.50 bar.
  * The hardened reference solution achieves $\text{raw} = 0.136 \implies \text{reward} = 0.728$, providing substantial and robust headroom above the 0.50 pass bar.

### Hidden Instance Generation & Seeding
* Ground truth is generated in `tests/.truth/heldout.py` using a secret fixed seed (`8675309`) completely isolated from the agent environment (root-owned, unreadable by agent uid 1000).
* The held-out cohort comprises **150 unseen subjects** simulated with log-normal inter-individual variability (IIV) on $CL_0$, $V$, $k_a$, $E_{\max}$, and $ET_{50}$, power-law body weight allometric scaling, sex effects on clearance, and proportional plus additive assay error with LLOQ censoring at 1.00 ng/mL.
* Graded forecast horizons include non-grid windows: $H \in \{0.25, 6.05, 24.0, 24.05\}$ hours.
* True steady-state parameters ($AUC_{ss}, C_{\max,ss}, C_{\text{trough},ss}$) are computed via adaptive Gauss-Kronrod numerical quadrature on the exact ground-truth concentration curve.

### Regrade Tolerance Measurement
Regrade tolerance is declared as `0.0`. In verification experiments, the reference solution was executed repeatedly in fresh separate containers; outputs agreed to within floating-point tolerance ($< 10^{-6}$ relative difference), resulting in identical rewards of 1.0.

### Score Instruction Alignment: Requirement-to-Test Mapping Table
Every material instruction requirement (R1 through R6) is directly enforced and gated by the test suite (`tests/test_outputs.py`, 23 checks):

| Requirement | Instruction Specification | Test Function(s) in `tests/test_outputs.py` | Verification Method |
|---|---|---|---|
| **R1: Metric & Objective Scoring** | Passes only when $\text{reward} \ge 0.50$ ($\text{raw} \le 0.25$) on unseen cohort | `test_accuracy_pass_bar` | Evaluates submission against 150 held-out subjects using ground-truth parameters |
| **R2: Runtime Budget & Determinism** | Finishes $\le 120$s (180s verifier host grace); identical repeated outputs | `test_runtime_budget`<br>`test_deterministic` | Measures wall-clock of predictor process; executes twice and asserts $|y_1 - y_2| \le 10^{-6}$ |
| **R3: Output Format Specifications** | Exactly `OUT.csv` with columns `subject_id,auc_ss,cmax_ss,ctrough_ss`; positive finite floats; write isolation | `test_submission_predicts`<br>`test_output_format`<br>`test_inputs_unmodified` | Validates file existence, schema, full subject set, numeric validity, and hashes inputs to ensure zero side-effects |
| **R4: Covariates Fallback Behavior** | Missing row or invalid weight defaults to 70 kg; invalid sex defaults to 'M'; other subjects unaffected | `test_covariates_missing_row_falls_back`<br>`test_covariates_unusable_weight_falls_back`<br>`test_covariates_unusable_sex_falls_back`<br>`test_covariates_fallback_matches_default_row`<br>`test_covariates_fallback_leaves_others_unchanged` | Runs multiple distinct synthetic fixtures (missing row, blank/non-numeric weight, unknown/lowercase sex) and tests exact numerical fallback and independence |
| **R5: Horizon Flag Validation** | Optional `--horizon-h H` recomputes over $[t_{14}, t_{14}+H)$; default 24h; invalid values exit non-zero | `test_horizon_default_matches_explicit_24`<br>`test_horizon_accuracy_matches_truth`<br>`test_horizon_window_additivity`<br>`test_horizon_rejects_invalid_values` | Tests default matches 24h; evaluates accuracy at 0.25h, 6.05h, 24.05h; verifies calculus additivity $dAUC \approx \Delta \cdot C_{\text{trough}}$; asserts 0, negative, non-numeric, >168h exit non-zero with no output |
| **R6: Estimator Flag Validation** | Optional `--estimator {map, population}`; default `map`; `population` invariant to samples; invalid strings exit non-zero | `test_estimator_default_matches_explicit_map`<br>`test_estimator_population_ignores_samples`<br>`test_estimator_population_ignores_subject_id`<br>`test_estimator_population_responds_to_dosing_and_covariates`<br>`test_estimator_population_differs_from_map`<br>`test_estimator_population_is_accuracy_graded`<br>`test_estimator_rejects_invalid_values` | Asserts default matches `map`; perturbs sample values, timings, censor flags, and row counts to assert population output remains invariant; tests dose/weight sensitivity; grades accuracy; asserts bad values exit non-zero |

---

## Environment & Execution
* **Base Image:** `python:3.13-slim-bookworm`
* **Preinstalled Dependencies:** `numpy==2.2.3`, `scipy==1.15.2`, `pandas==2.2.3` (installed on standard library path)
* **Verifier Image:** Built separately (`tests/Dockerfile`) with `pytest==8.4.1` and `pytest-json-ctrf==0.6.1` baked in
* **Hardware Allocation:** 4 CPUs, 8192 MB RAM, no network access
* **Working Directory:** `/app`
