# Internal memo - MAD-102 exposure analysis (interim, day-1 data-cut)

**To:** Phase-2 dose-selection working group
**From:** Clinical pharmacology analyst
**Date:** interim (analysis frozen at the day-1 data-cut; later samples not reviewed)

## Summary

A pooled oral one-compartment pharmacokinetic model assuming first-order absorption and static systemic clearance was calibrated to Day 1 plasma concentration profiles from the rich cohort. Convergence was achieved cleanly, and we propose utilizing this dose-proportional, stationary model for projecting Day 14 exposures, including for cohorts stepping up to elevated dosing tiers on Day 8.

## Parameter estimates (time-invariant model, day-1 data)

| Parameter | Estimate | Units |
|-----------|----------|-------|
| ka        | 0.48     | 1/h   |
| V         | 265      | L     |
| CL        | 20.0     | L/h   |
| IIV CL    | 48%      | CV    |
| IIV V     | 35%      | CV    |
| Residual  | 39%      | CV (proportional, log scale) |

A total of 405 of the 553 Day 1 sample records were analyzed; remaining observations were reported below the analytical assay LLOQ and excluded.

## Notes on the data

- Measurements flagged with `censored=1` were **omitted** from model fitting. These represent trough and terminal-phase samples that were treated as non-informative.
- Patient body weight, age, and biological sex were not evaluated as predictive covariates in this preliminary analysis.
- The estimated residual error exceeds initial bioanalytical validation expectations; this dispersion is attributed to clinical site specimen-handling variability rather than structural misspecification.
- Potential absorption lag times were not explored; concentration measurements at 0.25 h and 0.5 h were largely below quantification limits and discarded along with other censored points.

## Recommendation

Propagate the parameter point estimates listed above across each subject's dosing records and integrate exposure through the Day 14 window. Because systemic exposure is modeled as strictly linear with dose, subjects titrated to twice their initial dose starting on Day 8 are projected to demonstrate a proportional two-fold exposure increase at Day 14. Introducing dynamic, time-varying clearance mechanisms is considered unnecessary.
