#!/bin/bash
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

# Port variables and their defaults
declare -A PORT_DEFAULTS=(
    [POSTGRES_PORT]=5433
    [REDIS_PORT]=6379
    [GATEWAY_PORT]=8000
    [TEMPO_OTLP_PORT]=4317
    [TEMPO_HTTP_PORT]=3200
)

is_port_free() {
    local port="$1"
    # Validate port range (1024-65535 for unprivileged ports)
    if [[ "$port" -lt 1024 || "$port" -gt 65535 ]]; then
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

    for (( i=0; i<max_attempts; i++ )); do
        if is_port_free "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done

    echo "Error: could not find a free port starting from ${start} after ${max_attempts} attempts" >&2
    return 1
}

auto_selected=()

for var in POSTGRES_PORT REDIS_PORT GATEWAY_PORT TEMPO_OTLP_PORT TEMPO_HTTP_PORT; do
    default="${PORT_DEFAULTS[$var]}"

    if [[ -n "${!var}" ]]; then
        # Already set by the user (from .env or environment) -- keep it
        continue
    fi

    port=$(find_free_port "$default")
    if [[ $? -ne 0 ]]; then
        echo "FATAL: ${var} — ${port}" >&2
        exit 1
    fi

    export "$var=$port"
    auto_selected+=("  ${var}=${port}")
done

if [[ ${#auto_selected[@]} -gt 0 ]]; then
    echo "Auto-selected ports:"
    for line in "${auto_selected[@]}"; do
        echo "$line"
    done
fi
