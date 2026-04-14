#!/usr/bin/env bash
# Verify every AGENTS.md has a sibling CLAUDE.md symlink pointing to it,
# and every CLAUDE.md is that symlink (git mode 120000, blob == "AGENTS.md").
# Checks the git index directly so Windows checkouts without symlink support
# don't produce false negatives.
set -euo pipefail

fail=0
declare -A entries

while IFS=$'\t' read -r meta path; do
  entries["$path"]="$meta"
done < <(git ls-files -s -- '*AGENTS.md' '*CLAUDE.md' ':!:.claude/**')

agents_blob_for_symlink=""

for path in "${!entries[@]}"; do
  meta="${entries[$path]}"
  mode="${meta%% *}"
  rest="${meta#* }"
  blob="${rest%% *}"
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
    if [[ -z "${entries[$sibling]+x}" ]]; then
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
    if [[ -z "${entries[$sibling]+x}" ]]; then
      echo "FAIL: $path points to AGENTS.md but no such file is tracked in ${prefix:-./}" >&2
      fail=1
    fi
  fi
done

if [[ $fail -eq 0 ]]; then
  echo "AGENTS.md / CLAUDE.md parity OK (${#entries[@]} files checked)"
fi
exit $fail
