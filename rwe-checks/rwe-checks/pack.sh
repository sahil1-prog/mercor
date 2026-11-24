#!/bin/bash
# Runs the static checks, then makes the zip to upload to Studio.
# Studio only runs a package whose files sit under filesystem/, so do not zip the folder by hand.
# Usage: bash rwe-checks/pack.sh path/to/my-task   (writes my-task-upload.zip in the current folder)
set -eu
[ $# -eq 1 ] && [ -f "$1/task.toml" ] || { echo "Usage: bash pack.sh path/to/task-folder (it must hold task.toml)"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/run-all.sh" "$1"
TASK="$(cd "$1" && pwd)"
OUT="$PWD/$(basename "$TASK")-upload.zip"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ln -s "$TASK" "$WORK/filesystem"
rm -f "$OUT"
(cd "$WORK" && zip -q -r "$OUT" filesystem/ -x '*/.git/*' '*/.DS_Store' '*/__pycache__/*')
echo "Upload this file: $OUT"
