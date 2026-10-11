#!/usr/bin/env bash
# Keep all build descendants within half of this process's allowed CPU set.
set -euo pipefail
fail() { printf 'resource-limit: %s\n' "$*" >&2; exit 1; }
jobs=""
memory=""
while (($#)); do
    case "$1" in
        --jobs) (($# >= 2)) || fail 'missing --jobs value'; jobs=$2; shift 2 ;;
        --memory-max) (($# >= 2)) || fail 'missing --memory-max value'; memory=$2; shift 2 ;;
        --) shift; break ;;
        *) fail "unknown option: $1" ;;
    esac
done
(($#)) || fail 'usage: resource-limit.sh [--jobs N] [--memory-max SIZE] -- command args...'
for executable in python3 taskset nice ionice; do
    command -v "$executable" >/dev/null || fail "required command unavailable: $executable"
done
read -r cap cpus < <(python3 - <<'PY'
import os
allowed = sorted(os.sched_getaffinity(0))
cap = max(1, len(allowed) // 2)
print(cap, ','.join(map(str, allowed[:cap])))
PY
)
jobs=${jobs:-$cap}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
((jobs <= cap)) || fail "--jobs exceeds half of allowed CPUs ($cap)"
export BUILD_JOBS=$jobs
printf 'resource-limit: CPUs=%s, jobs=%s, nice=10, I/O=idle\n' "$cpus" "$jobs" >&2
if [[ -n $memory ]]; then
    [[ $memory =~ ^[1-9][0-9]*([KMGTPE]([i]?[B])?)?$ ]] || fail 'invalid --memory-max size'
    command -v systemd-run >/dev/null || fail 'MemoryMax requires systemd-run and a delegated user cgroup'
    [[ -f /sys/fs/cgroup/cgroup.controllers ]] || fail 'MemoryMax requires cgroup v2'
    [[ " $(cat /sys/fs/cgroup/cgroup.controllers) " == *' memory '* ]] || fail 'memory cgroup controller unavailable'
    # systemd-run fails before executing the command if this scope cannot be created.
    # MemoryMax covers the entire descendant scope; swapping is disabled for it.
    exec systemd-run --user --scope --quiet --property="MemoryMax=$memory" \
        --property=MemorySwapMax=0 taskset -c "$cpus" nice -n 10 ionice -c 3 "$@"
fi
exec taskset -c "$cpus" nice -n 10 ionice -c 3 "$@"
