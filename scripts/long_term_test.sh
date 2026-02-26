#!/bin/bash

# ABOUTME: Long-term stress testing script for luthien-proxy stability validation
# ABOUTME: Makes repeated Claude API calls to test session continuity and resource behavior

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────

MAX_TIME=3600
MAX_CALLS=0  # 0 = unlimited
PROMPT="Write a short function and explain it"
PROXY_PORT=8000
START_PROXY=true
OUTPUT_DIR=""
COOLDOWN=5

# ── Colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ── State ─────────────────────────────────────────────────────────────────────

CALL_COUNT=0
ERROR_COUNT=0
CONSECUTIVE_ERRORS=0
MAX_CONSECUTIVE_ERRORS=5
START_EPOCH=0
SESSION_ID=""
WE_STARTED_PROXY=false
LOG_FILE=""
TOTAL_INPUT_TOKENS=0
TOTAL_OUTPUT_TOKENS=0
DURATIONS=()

# ── Usage ─────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Long-term stress test for luthien-proxy. Makes repeated Claude API calls
through the proxy to validate stability over extended periods.

Options:
  --max-time <seconds>    Maximum wall-clock time to run (default: 3600)
  --max-calls <n>         Maximum number of API calls (default: unlimited)
  --prompt <text>         Prompt to send to Claude (default: built-in)
  --proxy-port <port>     Proxy port (default: 8000)
  --no-start-proxy        Skip starting the proxy (assume already running)
  --output-dir <dir>      Where to store logs/results (default: ./test_results/<timestamp>)
  --cooldown <seconds>    Delay between calls (default: 5)
  -h, --help              Show this help message

Examples:
  # Run for 30 minutes with 5-second cooldown
  $(basename "$0") --max-time 1800 --cooldown 5

  # Run exactly 100 calls against already-running proxy
  $(basename "$0") --max-calls 100 --no-start-proxy

  # Custom prompt for 10 minutes
  $(basename "$0") --max-time 600 --prompt "Explain a random CS concept"
EOF
    exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-time)    MAX_TIME="$2";    shift 2 ;;
        --max-calls)   MAX_CALLS="$2";   shift 2 ;;
        --prompt)      PROMPT="$2";      shift 2 ;;
        --proxy-port)  PROXY_PORT="$2";  shift 2 ;;
        --no-start-proxy) START_PROXY=false; shift ;;
        --output-dir)  OUTPUT_DIR="$2";  shift 2 ;;
        --cooldown)    COOLDOWN="$2";    shift 2 ;;
        -h|--help)     usage ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            usage
            ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

log() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo -e "$msg"
    if [[ -n "$LOG_FILE" ]]; then
        echo -e "$msg" | sed 's/\x1B\[[0-9;]*m//g' >> "$LOG_FILE"
    fi
}

elapsed_seconds() {
    echo $(( $(date +%s) - START_EPOCH ))
}

format_duration() {
    local secs=$1
    printf '%dh %dm %ds' $((secs/3600)) $(((secs%3600)/60)) $((secs%60))
}

health_url() {
    echo "http://localhost:${PROXY_PORT}/health"
}

wait_for_proxy() {
    log "${YELLOW}Waiting for proxy to be healthy...${NC}"
    for i in $(seq 1 30); do
        if curl -sf "$(health_url)" > /dev/null 2>&1; then
            log "${GREEN}Proxy is healthy${NC}"
            return 0
        fi
        sleep 1
    done
    log "${RED}Proxy did not become healthy within 30s${NC}"
    return 1
}

# ── Cleanup on exit ──────────────────────────────────────────────────────────

cleanup() {
    local exit_code=$?
    log ""
    log "${BOLD}── Shutting down ──${NC}"

    print_summary

    if [[ "$WE_STARTED_PROXY" == true ]]; then
        log "Stopping proxy (we started it)..."
        docker compose down --remove-orphans 2>/dev/null || true
    fi

    log "Results saved to: ${OUTPUT_DIR}"
    exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT TERM

# ── Summary ───────────────────────────────────────────────────────────────────

print_summary() {
    local wall_time
    wall_time=$(elapsed_seconds)

    local avg_duration="n/a"
    if [[ ${#DURATIONS[@]} -gt 0 ]]; then
        local sum=0
        for d in "${DURATIONS[@]}"; do
            sum=$((sum + d))
        done
        avg_duration="$((sum / ${#DURATIONS[@]}))s"
    fi

    local summary
    summary=$(cat <<EOF
══════════════════════════════════════════
  Long-Term Test Summary
══════════════════════════════════════════
  Total calls:        ${CALL_COUNT}
  Successful calls:   $((CALL_COUNT - ERROR_COUNT))
  Errors:             ${ERROR_COUNT}
  Wall time:          $(format_duration "$wall_time")
  Avg call duration:  ${avg_duration}
  Total input tokens: ${TOTAL_INPUT_TOKENS}
  Total output tokens: ${TOTAL_OUTPUT_TOKENS}
  Session ID:         ${SESSION_ID:-none}
══════════════════════════════════════════
EOF
)

    log "$summary"

    if [[ -n "$OUTPUT_DIR" ]]; then
        echo "$summary" | sed 's/\x1B\[[0-9;]*m//g' > "${OUTPUT_DIR}/summary.txt"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    START_EPOCH=$(date +%s)

    # Output directory
    if [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR="./test_results/$(date '+%Y%m%d_%H%M%S')"
    fi
    mkdir -p "$OUTPUT_DIR"
    LOG_FILE="${OUTPUT_DIR}/test.log"

    log "${BOLD}── Long-Term Stress Test ──${NC}"
    log "  Max time:    $(format_duration "$MAX_TIME")"
    log "  Max calls:   $( [[ $MAX_CALLS -eq 0 ]] && echo 'unlimited' || echo "$MAX_CALLS" )"
    log "  Cooldown:    ${COOLDOWN}s"
    log "  Proxy port:  ${PROXY_PORT}"
    log "  Output dir:  ${OUTPUT_DIR}"
    log ""

    # ── Check dependencies (fail fast before starting anything) ──

    if ! command -v claude &> /dev/null; then
        log "${RED}claude CLI not found. Install with: npm install -g @anthropic-ai/claude-cli${NC}"
        exit 1
    fi

    if ! command -v jq &> /dev/null; then
        log "${RED}jq not found. Install with: sudo apt install jq${NC}"
        exit 1
    fi

    # ── Start proxy if needed ──

    if [[ "$START_PROXY" == true ]]; then
        if curl -sf "$(health_url)" > /dev/null 2>&1; then
            log "${GREEN}Proxy already running on port ${PROXY_PORT}${NC}"
        else
            log "Starting proxy via docker compose..."
            docker compose up -d
            WE_STARTED_PROXY=true
            wait_for_proxy || exit 1
        fi
    else
        log "Skipping proxy startup (--no-start-proxy)"
        if ! curl -sf "$(health_url)" > /dev/null 2>&1; then
            log "${RED}Proxy not reachable at $(health_url). Start it or remove --no-start-proxy.${NC}"
            exit 1
        fi
        log "${GREEN}Proxy is healthy${NC}"
    fi

    # Configure environment so Claude routes through proxy
    export ANTHROPIC_BASE_URL="http://localhost:${PROXY_PORT}/"

    # Source proxy API key from .env if available
    if [[ -f .env ]]; then
        local proxy_key
        proxy_key=$(grep -E '^PROXY_API_KEY=' .env 2>/dev/null | cut -d '=' -f2- || true)
        if [[ -n "$proxy_key" ]]; then
            export ANTHROPIC_API_KEY="$proxy_key"
        fi
    fi

    log "${BLUE}Routing Claude through proxy at ${ANTHROPIC_BASE_URL}${NC}"
    log ""

    # ── Main loop ──

    while true; do
        # Check stopping conditions
        if [[ $(elapsed_seconds) -ge $MAX_TIME ]]; then
            log "${YELLOW}Max time reached ($(format_duration "$MAX_TIME"))${NC}"
            break
        fi

        if [[ $MAX_CALLS -gt 0 && $CALL_COUNT -ge $MAX_CALLS ]]; then
            log "${YELLOW}Max calls reached (${MAX_CALLS})${NC}"
            break
        fi

        if [[ $CONSECUTIVE_ERRORS -ge $MAX_CONSECUTIVE_ERRORS ]]; then
            log "${RED}Too many consecutive errors (${MAX_CONSECUTIVE_ERRORS}), aborting${NC}"
            break
        fi

        CALL_COUNT=$((CALL_COUNT + 1))
        local call_prompt="${PROMPT} (call #${CALL_COUNT}, $(date '+%H:%M:%S'))"

        log "${BLUE}── Call #${CALL_COUNT} ──${NC}"

        local call_start call_end duration exit_code
        local output_file="${OUTPUT_DIR}/call_${CALL_COUNT}.json"
        call_start=$(date +%s)

        # Build the claude command
        local claude_args=(-p "$call_prompt" --output-format json)
        if [[ -n "$SESSION_ID" ]]; then
            claude_args+=(--resume "$SESSION_ID")
        fi

        # Run claude, capturing output
        set +e
        claude "${claude_args[@]}" > "$output_file" 2>"${OUTPUT_DIR}/call_${CALL_COUNT}_stderr.txt"
        exit_code=$?
        set -e

        call_end=$(date +%s)
        duration=$((call_end - call_start))
        DURATIONS+=("$duration")

        if [[ $exit_code -eq 0 ]]; then
            CONSECUTIVE_ERRORS=0

            # Extract session_id from JSON output
            if [[ -z "$SESSION_ID" ]]; then
                SESSION_ID=$(jq -r '.session_id // empty' "$output_file" 2>/dev/null || true)
                if [[ -n "$SESSION_ID" ]]; then
                    log "  Session ID: ${SESSION_ID}"
                fi
            fi

            # Extract token counts if available
            local input_tokens output_tokens
            input_tokens=$(jq -r '.usage.input_tokens // 0' "$output_file" 2>/dev/null || echo 0)
            output_tokens=$(jq -r '.usage.output_tokens // 0' "$output_file" 2>/dev/null || echo 0)
            TOTAL_INPUT_TOKENS=$((TOTAL_INPUT_TOKENS + input_tokens))
            TOTAL_OUTPUT_TOKENS=$((TOTAL_OUTPUT_TOKENS + output_tokens))

            log "${GREEN}  OK${NC} | ${duration}s | tokens: ${input_tokens}in/${output_tokens}out | elapsed: $(format_duration "$(elapsed_seconds)")"
        else
            ERROR_COUNT=$((ERROR_COUNT + 1))
            CONSECUTIVE_ERRORS=$((CONSECUTIVE_ERRORS + 1))
            log "${RED}  FAIL (exit ${exit_code})${NC} | ${duration}s | see ${OUTPUT_DIR}/call_${CALL_COUNT}_stderr.txt"
        fi

        # Cooldown between calls (unless we're about to stop)
        if [[ $(elapsed_seconds) -lt $MAX_TIME ]]; then
            sleep "$COOLDOWN"
        fi
    done
}

main "$@"
