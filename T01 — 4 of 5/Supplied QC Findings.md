# T01: chemistry-and-life-science-3d4749ce5b-2

- **Seed source:** synthetic
- **Domain:** scicomp
- **Produced in:** v3 wave
- **Code QC v2:** pass (in loop, after adjudication)
- **RWE Implementation QC:** fail; excluding format-only criteria: fail; no major substantive defect: pass
- **Baseline model k=2 stopgap screen:** not screened

## Implementation QC: failing criteria
- **task_name** (major) [format]: Folder name is a pipeline slug, not the challenge
- **no_extraneous_files** (minor) [format]: Extraneous authoring data generator and model scripts retained in authoring/
- **ctrf_reporting** (minor) [format]: Verifier runs pytest without emitting CTRF report to /logs/verifier/ctrf.json
- **score_instruction_alignment** (minor): Runtime gate uses an undisclosed 180 s budget
- **environment_hygiene** (minor) [format]: Test-only dependency (pytest) baked into agent image
- **task_readme** (major) [format]: Reviewer-facing README.md is missing from the task repository
