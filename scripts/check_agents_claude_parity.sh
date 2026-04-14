#!/usr/bin/env bash
# Verify every AGENTS.md has a sibling CLAUDE.md symlink pointing to it,
# and every CLAUDE.md is that symlink (git mode 120000, blob == "AGENTS.md").
# Checks the git index directly so Windows checkouts without symlink support
# don't produce false negatives. Portable to bash 3.2 (macOS default).
set -euo pipefail

fail=0
count=0
# Parallel arrays keyed by index — portable to bash 3.2 (no associative arrays).
paths=()
modes=()
blobs=()

while IFS= read -r line; do
  # Format: "<mode> <blob> <stage>\t<path>"
  meta="${line%%$'\t'*}"
  path="${line#*$'\t'}"
  mode="${meta%% *}"
  rest="${meta#* }"
  blob="${rest%% *}"
  paths+=("$path")
  modes+=("$mode")
  blobs+=("$blob")
  count=$((count + 1))
done < <(git ls-files -s -- '*AGENTS.md' '*CLAUDE.md' ':!:.claude/**')

if [[ $count -eq 0 ]]; then
  echo "FAIL: no AGENTS.md / CLAUDE.md files tracked — check has become a no-op" >&2
  exit 1
fi

path_exists() {
  local needle="$1"
  local p
  for p in "${paths[@]}"; do
    if [[ "$p" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

for i in "${!paths[@]}"; do
  path="${paths[$i]}"
  mode="${modes[$i]}"
  blob="${blobs[$i]}"
  dir=$(dirname "$path")
  base=$(basename "$path")
  if [[ "$dir" == "." ]]; then
    prefix=""
  else
    prefix="$dir/"
  fi

  if [[ "$base" == "AGENTS.md" ]]; then
    if [[ "$mode" != "100644" ]]; then
      echo "FAIL: $path should be a regular file (mode 100644), got $mode" >&2
      fail=1
    fi
    sibling="${prefix}CLAUDE.md"
    if ! path_exists "$sibling"; then
      echo "FAIL: $path has no sibling CLAUDE.md symlink" >&2
      fail=1
    fi
  elif [[ "$base" == "CLAUDE.md" ]]; then
    if [[ "$mode" != "120000" ]]; then
      echo "FAIL: $path must be a symlink to AGENTS.md (mode 120000), got $mode" >&2
      fail=1
      continue
    fi
    target=$(git cat-file blob "$blob")
    if [[ "$target" != "AGENTS.md" ]]; then
      echo "FAIL: $path symlink target is '$target', expected 'AGENTS.md'" >&2
      fail=1
    fi
    sibling="${prefix}AGENTS.md"
    if ! path_exists "$sibling"; then
      echo "FAIL: $path points to AGENTS.md but no such file is tracked in ${prefix:-./}" >&2
      fail=1
    fi
  fi
done

if [[ $fail -eq 0 ]]; then
  echo "AGENTS.md / CLAUDE.md parity OK ($count files checked)"
fi
exit $fail
