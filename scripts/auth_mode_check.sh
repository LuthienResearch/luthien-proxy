#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Shared auth mode check with interactive countdown dialogue
# ABOUTME: Nudges users on proxy_key mode to consider passthrough/both

# Colors (caller may have already defined these)
_AMC_YELLOW='\033[1;33m'
_AMC_GREEN='\033[0;32m'
_AMC_NC='\033[0m'

# check_auth_mode_interactive <current_mode>
# Prompts if mode is proxy_key. Prints chosen mode to stdout.
# Returns 0 if mode changed, 1 if kept the same.
check_auth_mode_interactive() {
    local current_mode="$1"

    if [ "$current_mode" != "proxy_key" ]; then
        echo "$current_mode"
        return 1
    fi

    # Skip in non-interactive mode
    if [ ! -t 0 ]; then
        echo "$current_mode"
        return 1
    fi

    echo -e "${_AMC_YELLOW}" >&2
    echo -e "╔══════════════════════════════════════════════════════════════╗" >&2
    echo -e "║  ⚠  Auth mode is currently \"proxy_key\"                      ║" >&2
    echo -e "║                                                              ║" >&2
    echo -e "║  The proxy uses its own Anthropic API key for ALL requests,  ║" >&2
    echo -e "║  billed to whichever account owns that key. This is likely   ║" >&2
    echo -e "║  much more expensive than using a Claude Pro/Max sub.        ║" >&2
    echo -e "║                                                              ║" >&2
    echo -e "║  With OAuth passthrough, users authenticate with their own   ║" >&2
    echo -e "║  Claude accounts — no per-token API charges.                 ║" >&2
    echo -e "╚══════════════════════════════════════════════════════════════╝${_AMC_NC}" >&2
    echo "" >&2
    echo -e "   [1] Switch to \"both\"        (recommended — proxy key + OAuth)" >&2
    echo -e "   [2] Switch to \"passthrough\" (OAuth only)" >&2
    echo -e "   [3] Keep \"proxy_key\"        (all requests billed to server API key)" >&2
    echo "" >&2

    local choice=""
    local remaining=60

    # Read with countdown
    while [ "$remaining" -gt 0 ] && [ -z "$choice" ]; do
        printf "\r   Choice [3 in %2ds]: " "$remaining" >&2

        if read -r -t 1 -n 1 choice </dev/tty 2>/dev/null; then
            break
        fi
        remaining=$((remaining - 1))
    done
    echo "" >&2

    case "$choice" in
        1)
            echo -e "   ${_AMC_GREEN}✅ Switching to \"both\" mode${_AMC_NC}" >&2
            echo "both"
            return 0
            ;;
        2)
            echo -e "   ${_AMC_GREEN}✅ Switching to \"passthrough\" mode${_AMC_NC}" >&2
            echo "passthrough"
            return 0
            ;;
        *)
            echo -e "   Keeping \"proxy_key\" mode" >&2
            echo "proxy_key"
            return 1
            ;;
    esac
}

# update_auth_mode_env <new_mode> [env_file]
# Updates AUTH_MODE in the .env file.
update_auth_mode_env() {
    local new_mode="$1"
    local env_file="${2:-.env}"

    if [ ! -f "$env_file" ]; then
        echo "AUTH_MODE=${new_mode}" >> "$env_file"
        return
    fi

    if grep -q "^AUTH_MODE=" "$env_file" 2>/dev/null; then
        sed -i.bak "s/^AUTH_MODE=.*/AUTH_MODE=${new_mode}/" "$env_file" && rm -f "${env_file}.bak"
    elif grep -q "^#.*AUTH_MODE=" "$env_file" 2>/dev/null; then
        sed -i.bak "s/^#.*AUTH_MODE=.*/AUTH_MODE=${new_mode}/" "$env_file" && rm -f "${env_file}.bak"
    else
        echo "AUTH_MODE=${new_mode}" >> "$env_file"
    fi
}

# update_auth_mode_api <new_mode> <gateway_url> <admin_key>
# Updates auth mode via the admin API. Silent on failure.
update_auth_mode_api() {
    local new_mode="$1"
    local gateway_url="$2"
    local admin_key="$3"

    if [ -z "$admin_key" ]; then
        return 1
    fi

    curl -sf -X POST \
        -H "Authorization: Bearer ${admin_key}" \
        -H "Content-Type: application/json" \
        -d "{\"auth_mode\": \"${new_mode}\"}" \
        "${gateway_url}/api/admin/auth/config" > /dev/null 2>&1
}
