# Internal memo — MAD-102 exposure analysis (interim, day-14 not yet reported)

**To:** Phase-2 dose-selection working group
**From:** Clinical pharmacology analyst
**Date:** interim (analysis frozen after day-7 data-cut)

## Summary

We fitted a pooled oral one-compartment model to the day-1 profiles from the rich
cohort. The fit is stable and the diagnostics are acceptable. **We recommend
using this time-invariant model for the phase-2 dose selection.** Apparent
clearance is constant over the dosing interval and over the study, and there is
no evidence of time-dependent pharmacokinetics in the day-1 data.

## Final parameter estimates (time-invariant model)

| Parameter | Estimate | Units |
|-----------|----------|-------|
| ka        | 1.10     | 1/h   |
| V         | 300.0    | L     |
| CL        | 15.0     | L/h   |
| IIV CL    | 24%      | CV    |
| IIV V     | 22%      | CV    |
| Residual  | 12%      | CV (proportional) |

Body weight and sex were tested as covariates and were **not** retained; the
apparent trends did not reach significance in the rich cohort and we recommend
not adjusting for them.

## Notes on the data

- Samples reported as `censored=1` (below the 1.00 ng/mL assay LLOQ) were
  **excluded** from the fit. Imputing or modelling them was judged unnecessary
  because the excluded samples are few and all occur at trough.
- Day-14 profiles were collected but the exposure summary was not computed at
  the time of this data-cut; the interim conclusion is that day-14 exposure
  will be close to day-1 exposure because clearance does not change.
- The onward phase-2 cohort need not be re-modelled; apply the point estimates
  above to each subject and integrate the day-14 interval.

## Recommendation

Use AUCss ≈ AUC after a single dose scaled by the number of doses, with Cmax
and Ctrough from the same time-invariant simulation. No induction or
autoinduction term is warranted.
