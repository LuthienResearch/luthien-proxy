#!/bin/bash
# ABOUTME: Helper script to manage observability stack (Grafana/Loki/Tempo)

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

print_usage() {
    cat <<EOF
${GREEN}Luthien Observability Stack${NC}

Manage Grafana + Loki + Tempo observability services.

${YELLOW}Usage:${NC}
  $0 <command> [options]

${YELLOW}Commands:${NC}
  up [options]      Start observability stack
  down [options]    Stop observability stack
  restart           Restart observability stack
  logs [options]    View logs from observability services
  status            Show status of observability services
  clean             Stop and remove all observability data
  url               Print Grafana URL
  help              Show this help message

${YELLOW}Examples:${NC}
  $0 up -d                    # Start in background
  $0 logs -f                  # Follow logs
  $0 logs -f grafana          # Follow logs for grafana only
  $0 down                     # Stop stack
  $0 clean                    # Remove all data and stop

${YELLOW}Access:${NC}
  Grafana UI: http://localhost:3000 (auto-login, no password)

${YELLOW}Documentation:${NC}
  - Quick start: observability/README.md
  - Full guide: dev/context/observability-guide.md

EOF
}

# Ensure we're in project root
cd "$PROJECT_ROOT"

# Main command router
case "${1:-help}" in
    up)
        shift
        echo -e "${GREEN}Starting observability stack...${NC}"
        docker compose --profile observability up "$@"
        if [[ "$*" == *"-d"* ]]; then
            echo -e "${GREEN}✓ Observability stack started${NC}"
            echo -e "  Grafana UI: ${YELLOW}http://localhost:3000${NC}"
        fi
        ;;

    down)
        shift
        echo -e "${YELLOW}Stopping observability stack...${NC}"
        docker compose --profile observability down "$@"
        echo -e "${GREEN}✓ Observability stack stopped${NC}"
        ;;

    restart)
        echo -e "${YELLOW}Restarting observability stack...${NC}"
        docker compose --profile observability restart
        echo -e "${GREEN}✓ Observability stack restarted${NC}"
        ;;

    logs)
        shift
        # If no service specified, show all observability services
        if [[ $# -eq 0 ]] || [[ "$1" == -* ]]; then
            docker compose logs "$@" tempo loki grafana
        else
            docker compose logs "$@"
        fi
        ;;

    status)
        echo -e "${GREEN}Observability Stack Status:${NC}"
        docker compose ps tempo loki grafana
        ;;

    clean)
        echo -e "${RED}WARNING: This will stop services and delete ALL observability data.${NC}"
        read -p "Are you sure? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo -e "${YELLOW}Stopping services...${NC}"
            docker compose --profile observability down -v
            echo -e "${YELLOW}Removing data directory...${NC}"
            rm -rf observability/data
            echo -e "${GREEN}✓ Observability data cleaned${NC}"
        else
            echo -e "${YELLOW}Cancelled.${NC}"
        fi
        ;;

    url)
        echo "http://localhost:3000"
        ;;

    help|--help|-h)
        print_usage
        ;;

    *)
        echo -e "${RED}Error: Unknown command '$1'${NC}"
        echo
        print_usage
        exit 1
        ;;
esac
