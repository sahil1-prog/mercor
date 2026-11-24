#!/bin/bash
# Reference execution workflow: Calibrate population parameters from visible cohort data,
# then deploy the predictor, computational engine, and fitted model into /app/submission.
# Operates fully offline as the unprivileged agent user within the target container.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUB=/app/submission
mkdir -p "$SUB"

python3 "$HERE/fit_population.py" "$SUB/pop_params.json"
cp "$HERE/pk_engine.py" "$SUB/pk_engine.py"
cp "$HERE/predict.py" "$SUB/predict.py"

echo "reference submission ready at $SUB"
