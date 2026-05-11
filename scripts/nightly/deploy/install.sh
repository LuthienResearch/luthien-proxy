#!/usr/bin/env bash
# Install the nightly job's scheduler entry. Detects macOS vs Linux and
# uses launchd or systemd accordingly. Idempotent.
#
# Usage:
#   deploy/install.sh                 # default 02:30 local time
#   HOUR=3 MINUTE=0 deploy/install.sh # override schedule
#
# Pre-reqs (the script checks):
#   - bash, git, python3
#   - For e2e_real: docker, ANTHROPIC_API_KEY in nightly.env
#   - For autofix: claude CLI, gh CLI authenticated
#
# What this does:
#   1. Renders the platform's scheduler template with absolute paths.
#   2. Installs/loads the unit.
#   3. Prints next-fire time.
#
# What this does NOT do:
#   - Configure your web server. Point Caddy/nginx at $NIGHTLY_PUBLIC_DIR
#     (default $HOME/.luthien/nightly/public).
#   - Create nightly.env. Copy nightly.env.example first.

set -euo pipefail

NIGHTLY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/config.sh
source "${NIGHTLY_DIR}/lib/config.sh"

HOUR="${HOUR:-2}"
MINUTE="${MINUTE:-30}"
LOG_DIR="${NIGHTLY_STATE_DIR}/logs"
mkdir -p "${LOG_DIR}"

NIGHTLY_SH="${NIGHTLY_DIR}/nightly.sh"
[[ -x "${NIGHTLY_SH}" ]] || chmod +x "${NIGHTLY_SH}"

PATH_FOR_UNIT="${PATH}"

case "$(uname -s)" in
    Darwin)
        PLATFORM="macos"
        AGENTS_DIR="${HOME}/Library/LaunchAgents"
        LABEL="com.luthien.nightly"
        PLIST="${AGENTS_DIR}/${LABEL}.plist"
        TMPL="${NIGHTLY_DIR}/deploy/launchd/com.luthien.nightly.plist.template"
        mkdir -p "${AGENTS_DIR}"
        sed \
            -e "s#__LABEL__#${LABEL}#g" \
            -e "s#__NIGHTLY_SH__#${NIGHTLY_SH}#g" \
            -e "s#__WORKING_DIR__#${NIGHTLY_DIR}#g" \
            -e "s#__LOG_DIR__#${LOG_DIR}#g" \
            -e "s#__PATH__#${PATH_FOR_UNIT}#g" \
            -e "s#__HOUR__#${HOUR}#g" \
            -e "s#__MINUTE__#${MINUTE}#g" \
            "${TMPL}" > "${PLIST}"
        launchctl unload "${PLIST}" 2>/dev/null || true
        launchctl load "${PLIST}"
        echo "Installed launchd agent at ${PLIST}"
        echo "Next fire: $(printf '%02d:%02d' "${HOUR}" "${MINUTE}") local time, daily"
        ;;
    Linux)
        PLATFORM="linux"
        # Prefer user-mode systemd (no sudo) when available; fall back to
        # printing instructions for the system-level install.
        UNIT_DIR="${HOME}/.config/systemd/user"
        mkdir -p "${UNIT_DIR}"
        TMPL_SVC="${NIGHTLY_DIR}/deploy/systemd/luthien-nightly.service.template"
        TMPL_TMR="${NIGHTLY_DIR}/deploy/systemd/luthien-nightly.timer.template"
        ON_CAL="$(printf '*-*-* %02d:%02d:00' "${HOUR}" "${MINUTE}")"
        sed \
            -e "s#__USER__#${USER}#g" \
            -e "s#__NIGHTLY_SH__#${NIGHTLY_SH}#g" \
            -e "s#__WORKING_DIR__#${NIGHTLY_DIR}#g" \
            -e "s#__LOG_DIR__#${LOG_DIR}#g" \
            -e "s#__PATH__#${PATH_FOR_UNIT}#g" \
            "${TMPL_SVC}" > "${UNIT_DIR}/luthien-nightly.service"
        sed \
            -e "s#__ONCALENDAR__#${ON_CAL}#g" \
            "${TMPL_TMR}" > "${UNIT_DIR}/luthien-nightly.timer"
        systemctl --user daemon-reload
        systemctl --user enable --now luthien-nightly.timer
        echo "Installed user systemd timer in ${UNIT_DIR}"
        echo "Next fire:"
        systemctl --user list-timers luthien-nightly.timer --no-pager || true
        echo ""
        echo "Note: user-mode timers stop when you log out unless lingering"
        echo "is enabled. Run: sudo loginctl enable-linger ${USER}"
        ;;
    *)
        echo "Unsupported platform: $(uname -s)" >&2
        echo "Render the templates manually from ${NIGHTLY_DIR}/deploy/." >&2
        exit 2
        ;;
esac

echo ""
echo "Platform: ${PLATFORM}"
echo "State dir: ${NIGHTLY_STATE_DIR}"
echo "Public dir: ${NIGHTLY_PUBLIC_DIR}"
echo "Logs: ${LOG_DIR}"
echo ""
echo "Smoke test the job by running it once:"
echo "  ${NIGHTLY_SH}"
