# Internal memo - MAD-102 exposure analysis (interim, day-1 data-cut)

**To:** Phase-2 dose-selection working group
**From:** Clinical pharmacology analyst
**Date:** interim (analysis frozen at the day-1 data-cut; later samples not reviewed)

## Summary

We fitted a pooled oral one-compartment model with first-order absorption and
constant clearance to the day-1 profiles of the rich cohort. The fit converged and
we recommend this time-invariant, dose-proportional model for forecasting day-14
exposure, including for subjects titrated to the higher dose from day 8.

## Parameter estimates (time-invariant model, day-1 data)

| Parameter | Estimate | Units |
|-----------|----------|-------|
| ka        | 0.48     | 1/h   |
| V         | 265      | L     |
| CL        | 20.0     | L/h   |
| IIV CL    | 48%      | CV    |
| IIV V     | 35%      | CV    |
| Residual  | 39%      | CV (proportional, log scale) |

405 of the 553 day-1 samples were used; the rest were reported below the assay
LLOQ and were excluded.

## Notes on the data

- Samples reported as `censored=1` were **excluded** from the fit. They are trough
  and late-absorption samples and were judged uninformative.
- Body weight, age and sex were not tested as covariates at this data-cut.
- The residual error is larger than the assay validation suggests; we attribute this
  to sample-handling variability at the clinical site rather than to the model.
- No evidence of an absorption delay was looked for; the 0.25 h and 0.5 h samples
  were mostly below the LLOQ and dropped with the rest.

## Recommendation

Apply the point estimates above to each subject's dose rows and integrate the day-14
interval. Exposure scales linearly with dose, so a subject stepped up to twice the
starting dose from day 8 is expected to reach twice the starting-dose exposure by
day 14. No time-dependent clearance term is warranted.
