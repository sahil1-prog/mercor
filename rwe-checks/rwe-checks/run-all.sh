#!/bin/bash
# Runs GitHub CI's 26 static checks on one task folder.
# Usage: bash rwe-checks/run-all.sh path/to/my-task
# Needs bash and Python 3.11 or newer. Prints a line per check and exits 1 if any fail.
set -u
[ $# -eq 1 ] && [ -f "$1/task.toml" ] || { echo "Usage: bash run-all.sh path/to/task-folder (it must hold task.toml)"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
TASK="$(cd "$1" && pwd)"
NAME="$(basename "$TASK")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tasks"
ln -s "$TASK" "$WORK/tasks/$NAME"
cd "$WORK" || exit 2
FAILED=0
for script in "$HERE"/scripts/checks/check-*.sh; do
    check="$(basename "$script" .sh)"
    if out="$(bash "$script" "tasks/$NAME" 2>&1)"; then
        echo "PASS $check"
    else
        echo "FAIL $check"
        echo "$out" | sed 's/^/     /'
        FAILED=$((FAILED + 1))
    fi
done
if [ "$FAILED" -eq 0 ]; then echo "All checks passed."; else echo "$FAILED check(s) failed."; exit 1; fi
