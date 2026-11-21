#!/usr/bin/env bash
# Run make targets as gate steps, one progress line each: "push-gate: test … ok 34.6s".
# Step output is captured and shown only on failure. pre-commit buffers hook output until
# the hook exits, so progress goes straight to the terminal when there is one.
set -uo pipefail

if { : >/dev/tty; } 2>/dev/null; then
  exec 3>/dev/tty
  # pre-commit has already written "make push-gate....." with no newline.
  [ -n "${PRE_COMMIT:-}" ] && printf '\n' >&3
else
  exec 3>&2
fi

elapsed() { local us=$(( ${EPOCHREALTIME/./} - $1 )); printf '%d.%ds' $((us / 1000000)) $((us % 1000000 / 100000)); }

total=${EPOCHREALTIME/./}
for step in "$@"; do
  printf 'push-gate: %-10s … ' "$step" >&3
  start=${EPOCHREALTIME/./}
  if out=$("${MAKE:-make}" --no-print-directory -s "$step" 2>&1); then
    printf 'ok %s\n' "$(elapsed "$start")" >&3
  else
    rc=$?
    printf 'FAIL %s\n' "$(elapsed "$start")" >&3
    printf '%s\n' "$out" >&2
    exit "$rc"
  fi
done
printf 'push-gate: all passed in %s\n' "$(elapsed "$total")" >&3
