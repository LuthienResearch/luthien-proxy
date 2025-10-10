#!/usr/bin/env bash
# ABOUTME: Launch a long-running command in the background with log capture.
# ABOUTME: Writes stdout/stderr to a log file and records the PID for later polling.

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <log-file> <command> [args...]" >&2
  exit 1
fi

log_file=$1
shift

log_dir=$(dirname "$log_file")
mkdir -p "$log_dir"

timestamp=$(date +"%Y-%m-%d %H:%M:%S")
echo "[$timestamp] starting background task: $*" >>"$log_file"

"$@" >>"$log_file" 2>&1 &
pid=$!
echo "$pid" >"${log_file}.pid"

echo "started PID $pid, logging to $log_file"
echo "use:   tail -f $log_file"
echo "or:    ps -p $pid"
echo "to stop: kill $pid"
