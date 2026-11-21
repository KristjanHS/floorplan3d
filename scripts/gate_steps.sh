#!/usr/bin/env bash
# Run make targets as gate steps, one progress line each: "push-gate: lint … ok 7.0s".
# Step output is captured and shown only on failure. pre-commit buffers hook output until
# the hook exits, so progress goes straight to the terminal when there is one; there, pytest
# also streams per-test progress (tests/conftest.py) and the step re-states with a summary.
set -uo pipefail

state=$(mktemp)
trap 'rm -f "$state"' EXIT
# Auto-allocated fd (>=10): low fds may carry make's jobserver pipe.
if { : >/dev/tty; } 2>/dev/null; then
  exec {out}>/dev/tty
  export GATE_PROGRESS_TTY=/dev/tty GATE_PROGRESS_STATE=$state
  # pre-commit has already written "make push-gate....." with no newline.
  [ -n "${PRE_COMMIT:-}" ] && printf '\n' >&"$out"
else
  exec {out}>&2
fi

# Digits only: EPOCHREALTIME uses the locale's decimal separator (a comma in et_EE).
now() { printf '%s' "${EPOCHREALTIME//[!0-9]/}"; }
elapsed() { local us=$(( $(now) - $1 )); printf '%d.%ds' $((us / 1000000)) $((us % 1000000 / 100000)); }

# Close the step line; a step that streamed its own progress gets it re-stated with its summary.
finish() {  # step verdict start
  local summary
  summary=$(cat "$state")
  if [ -n "$summary" ]; then
    printf 'push-gate: %-10s … %s %s %s\n' "$1" "$2" "$summary" "$(elapsed "$3")" >&"$out"
  else
    printf '%s %s\n' "$2" "$(elapsed "$3")" >&"$out"
  fi
}

total=$(now)
for step in "$@"; do
  : >"$state"
  printf 'push-gate: %-10s … ' "$step" >&"$out"
  start=$(now)
  if output=$("${MAKE:-make}" --no-print-directory -s "$step" 2>&1); then
    finish "$step" ok "$start"
  else
    rc=$?
    finish "$step" FAIL "$start"
    printf '%s\n' "$output" >&2
    exit "$rc"
  fi
done
printf 'push-gate: all passed in %s\n' "$(elapsed "$total")" >&"$out"
