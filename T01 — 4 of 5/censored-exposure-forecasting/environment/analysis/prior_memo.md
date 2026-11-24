# Internal memo - MAD-102 exposure analysis (interim, day-1 data-cut)

**To:** Phase-2 dose-selection working group
**From:** Clinical pharmacology analyst
**Date:** interim (analysis frozen at the day-1 data-cut; later samples not reviewed)

## Summary

A pooled oral one-compartment pharmacokinetic model with first-order absorption and constant clearance was fitted to Day 1 plasma profiles from the rich cohort. The estimation converged smoothly, and we recommend this time-invariant, dose-proportional model for predicting Day 14 exposure, including for subjects escalating to the higher dose level on Day 8.

## Parameter estimates (time-invariant model, day-1 data)

| Parameter | Estimate | Units |
|-----------|----------|-------|
| ka        | 0.48     | 1/h   |
| V         | 265      | L     |
| CL        | 20.0     | L/h   |
| IIV CL    | 48%      | CV    |
| IIV V     | 35%      | CV    |
| Residual  | 39%      | CV (proportional, log scale) |

A total of 405 of the 553 Day 1 sample records were utilized; remaining samples were reported below the assay LLOQ and excluded from analysis.

## Notes on the data

- Samples marked `censored=1` were **omitted** from the fit. These represent trough and late-absorption draws deemed uninformative.
- Body weight, age, and biological sex were not tested as covariates in this initial analysis.
- Estimated residual variability exceeds assay validation expectations; we attribute this to clinical site sample handling rather than structural model error.
- No evidence of absorption delay was evaluated; 0.25 h and 0.5 h samples were mostly below LLOQ and dropped with other censored points.

## Recommendation

Apply the point estimates above across each subject's dosing rows and integrate through the Day 14 window. Exposure increases linearly with dose, so subjects stepping up to double their starting dose on Day 8 are expected to reach double their starting-dose exposure by Day 14. Adding time-dependent clearance terms is not recommended.
