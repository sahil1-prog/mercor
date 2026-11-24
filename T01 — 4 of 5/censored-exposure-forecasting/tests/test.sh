#!/bin/bash
# >>> verifier prelude: pipeline-owned, regenerated before every check >>>
# Root verifier: lock the reward dir, kill the agent user's processes, run agent code only via
# $AS_AGENT; the finale kills them again and replaces the reward. As the agent uid: no-ops.
set -u
umask 077
readonly REWARD_DIR=/logs/verifier REWARD_FILE=/logs/verifier/reward.txt AGENT_UID=1000
VERIFIER_UID=$(id -u)
VERIFIER_GID=$(id -g)
ISOLATE=0
[ "$VERIFIER_UID" = "$AGENT_UID" ] || ISOLATE=1
AGENT_GID=$AGENT_UID
AS_AGENT=
INIT_IS_AGENT=0
LOCKED=0

write_reward() {
    local tmp
    tmp=$(mktemp "$REWARD_DIR/.reward.XXXXXX") || return 1
    if printf '%s\n' "$1" >"$tmp" && chmod 0644 "$tmp" \
        && rm -rf "$REWARD_DIR/reward.json" "$REWARD_FILE" && mv -f "$tmp" "$REWARD_FILE" \
        && [ ! -e "$REWARD_DIR/reward.json" ] && [ "$(cat "$REWARD_FILE")" = "$1" ]; then
        return 0
    fi
    rm -f "$tmp"
    [ "${2:-}" = again ] || write_reward 0 again
    return 1
}

fail_closed() {
    trap - EXIT
    set +e
    echo "verifier: $1" >&2
    if ! write_reward 0 && [ "$LOCKED" = 1 ]; then
        rm -rf "$REWARD_DIR/reward.json" "$REWARD_FILE"
    fi
    [ "$LOCKED" = 0 ] || chmod 0755 "$REWARD_DIR"
    exit 1
}

agent_gid() {
    local name pw uid gid rest
    while IFS=: read -r name pw uid gid rest; do
        [ "$uid" = "$AGENT_UID" ] && { echo "$gid"; return 0; }
    done </etc/passwd
    return 1
}

is_agent() {
    local key value rest uid
    while read -r key value rest; do
        [ "$key" = Uid: ] || continue
        for uid in $value $rest; do [ "$uid" = "$AGENT_UID" ] && return 0; done
        return 1
    done 2>/dev/null <"$1/status"
    return 1
}

agent_pids() {
    local proc stat state
    for proc in /proc/[0-9]*; do
        [ "$proc" = /proc/1 ] && continue
        is_agent "$proc" || continue
        read -r stat 2>/dev/null <"$proc/stat" || continue
        set -- ${stat##*) }
        [ "$1" = Z ] && continue
        [ "$INIT_IS_AGENT" = 1 ] && [ "$4" = 1 ] && continue
        echo "${proc#/proc/}"
    done
}

stop_agent() {
    [ "$ISOLATE" = 1 ] || return 0
    local round pid pids
    [ "$INIT_IS_AGENT" = 1 ] || setpriv --reuid="$AGENT_UID" --regid="$AGENT_GID" \
        --clear-groups -- bash -c 'kill -KILL -1' 2>/dev/null || :
    for round in 1 2 3 4 5 6 7 8 9 10; do
        pids=$(agent_pids)
        [ -z "$pids" ] && return 0
        for pid in $pids; do kill -KILL "$pid" 2>/dev/null; done
        sleep 0.2
    done
    [ -z "$(agent_pids)" ]
}

verifier_finale() {
    local rc=$? final=0
    trap - EXIT
    set +e
    stop_agent || fail_closed 'cannot stop agent-user processes'
    [ "${status:-$rc}" = 0 ] && final=1
    write_reward "$final" || fail_closed 'cannot finalize reward'
    [ "$ISOLATE" = 0 ] || chmod 0755 "$REWARD_DIR"
    exit "${status:-$rc}"
}

if [ "$ISOLATE" = 1 ]; then
    case "$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)/" in
        /tests/*) ;;
        *) echo 'verifier: refusing to lock or sweep outside the grading sandbox (/tests)' >&2
           exit 1 ;;
    esac
    [ "$AGENT_UID" != 0 ] || fail_closed 'the agent user is root'
    AGENT_GID=$(agent_gid) || fail_closed "agent uid $AGENT_UID has no /etc/passwd entry"
    ! is_agent /proc/1 || INIT_IS_AGENT=1
    mkdir -p "$REWARD_DIR" && [ ! -L "$REWARD_DIR" ] \
        && chown "$VERIFIER_UID:$VERIFIER_GID" "$REWARD_DIR" && chmod 0700 "$REWARD_DIR" \
        || fail_closed 'cannot lock the reward directory'
    LOCKED=1
    if [ -O /var/lib/slo/state.json ] && [ ! -L /var/lib/slo ]; then
        chown -hR "$VERIFIER_UID:$VERIFIER_GID" /tests && chmod -R go-rwx /tests             || fail_closed 'cannot lock /tests'
        SLO_PID_RE='"recorder_pid": ([0-9]+)'
        SLO_WAIT=0
        while [ ! -e /var/lib/slo/verdict.json ] && [ "$SLO_WAIT" -lt 1800 ]; do
            [[ "$(cat /var/lib/slo/state.json 2>/dev/null)" =~ $SLO_PID_RE ]]                 && kill -0 "${BASH_REMATCH[1]}" 2>/dev/null || break
            sleep 0.2
            SLO_WAIT=$((SLO_WAIT + 1))
        done
    fi
    stop_agent || fail_closed 'cannot stop agent-user processes'
    chown -hR "$VERIFIER_UID:$VERIFIER_GID" /tests && chmod -R go-rwx /tests \
        || fail_closed 'cannot lock /tests'
    AS_AGENT="setpriv --reuid=$AGENT_UID --regid=$AGENT_GID --init-groups --no-new-privs --"
    $AS_AGENT true || fail_closed 'cannot drop to the agent user (setpriv, from util-linux)'
else
    mkdir -p "$REWARD_DIR" || { echo 'verifier: no reward dir' >&2; exit 1; }
fi
AGENT_SWEEP="$(declare -f is_agent agent_pids stop_agent)
AGENT_UID=$AGENT_UID AGENT_GID=$AGENT_GID ISOLATE=$ISOLATE INIT_IS_AGENT=$INIT_IS_AGENT
stop_agent"
readonly -f write_reward fail_closed agent_gid is_agent agent_pids stop_agent verifier_finale
readonly VERIFIER_UID VERIFIER_GID ISOLATE AGENT_GID AS_AGENT AGENT_SWEEP INIT_IS_AGENT LOCKED
trap verifier_finale EXIT
write_reward 0 || fail_closed 'cannot initialize reward'
# <<< verifier prelude <<<

# Prerequisite: the agent must have produced the documented entrypoint. A missing
# one is the agent doing nothing, so it names the path and fails closed (reward 0).
if [ ! -f /app/submission/predict.py ]; then
    echo "verifier: missing /app/submission/predict.py" >&2
    exit 1
fi

env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    AS_AGENT="$AS_AGENT" AGENT_UID="$AGENT_UID" AGENT_SWEEP="$AGENT_SWEEP" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    python3 -I -S -B -m pytest -q -p no:cacheprovider -p ctrf --rootdir=/tests --noconftest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py
status=$?

# >>> verifier finale: pipeline-owned >>>
verifier_finale
# <<< verifier finale <<<
