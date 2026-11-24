#!/bin/bash
# Reference solution: fit the population model on the visible cohorts, then install the
# forecaster, its numeric engine and the fitted parameters under /app/submission.
# Runs as the agent user inside the image built from environment/Dockerfile, offline,
# and writes only into /app/submission.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUB=/app/submission
mkdir -p "$SUB"

python3 "$HERE/fit_population.py" "$SUB/pop_params.json"
cp "$HERE/pk_engine.py" "$SUB/pk_engine.py"
cp "$HERE/predict.py" "$SUB/predict.py"

echo "reference submission ready at $SUB"
