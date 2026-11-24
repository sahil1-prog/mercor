#!/bin/bash
# Reference solution: fit population priors on the visible rich cohort, then install the
# self-contained predictor plus its parameter artifact under /app/submission.
# Runs as the agent user inside the image built from environment/Dockerfile (numpy/scipy
# are on the stdlib path there), offline, and writes only into /app/submission.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUB=/app/submission
mkdir -p "$SUB"

# 1. Derive the population parameters from /app/data/rich by censored likelihood.
python3 "$HERE/fit_population.py" "$SUB/pop_params.json"

# 2. Install the numeric engine and the hardened predictor that consumes the artifact.
#    predict.py is the hardened entrypoint; it imports the engine shipped beside it.
cp "$HERE/predict_reference.py" "$SUB/predict_reference.py"
cp "$HERE/predict_hardened.py" "$SUB/predict.py"

echo "reference submission ready at $SUB"
