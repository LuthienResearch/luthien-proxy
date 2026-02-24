#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Find available ports for docker compose services at startup.
# ABOUTME: Source this script before `docker compose up` to auto-select free ports.
#
# For each port variable, if it's already set (from .env or environment), we
# respect the user's choice. If not set, we scan upward from the default until
# we find a free port on the host.
#
# Usage:
#   source scripts/find-available-ports.sh
#   docker compose up -d

# Port variables and their defaults (parallel arrays for bash 3 compatibility)
PORT_VARS="POSTGRES_PORT REDIS_PORT GATEWAY_PORT TEMPO_OTLP_PORT TEMPO_HTTP_PORT"
PORT_DEFAULTS="5433 6379 8000 4317 3200"

is_port_free() {
    local port="$1"
    # Validate port range (1024-65535 for unprivileged ports)
    if [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
        return 1
    fi
    if command -v ss &>/dev/null; then
        ! ss -tlnH "sport = :${port}" | grep -q .
    else
        ! (echo >/dev/tcp/localhost/"${port}") 2>/dev/null
    fi
}

find_free_port() {
    # Note: inherent TOCTOU race between checking port availability and Docker binding.
    # This is acceptable for dev tooling — collisions are rare and easily resolved by retry.
    local start="$1"
    local port="$start"
    local max_attempts=100
    local i=0

    while [ "$i" -lt "$max_attempts" ]; do
        if is_port_free "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
        i=$((i + 1))
    done

    echo "Error: could not find a free port starting from ${start} after ${max_attempts} attempts" >&2
    return 1
}

auto_selected=""

# Iterate over vars and defaults in parallel using positional indexing
set -- $PORT_DEFAULTS
for var in $PORT_VARS; do
    default="$1"
    shift

    eval "current_val=\${$var}"
    if [ -n "$current_val" ]; then
        # Already set by the user (from .env or environment) -- keep it
        continue
    fi

    if ! port=$(find_free_port "$default"); then
        echo "FATAL: ${var} could not find a free port" >&2
        exit 1
    fi

    export "$var=$port"
    auto_selected="${auto_selected}  ${var}=${port}\n"
done

if [ -n "$auto_selected" ]; then
    echo "Auto-selected ports:"
    printf "%b" "$auto_selected"
fi
