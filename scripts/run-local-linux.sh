#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv-linux"
DEFAULT_HOST="${DSA_LOCAL_HOST:-127.0.0.1}"
DEFAULT_PORT="${DSA_LOCAL_PORT:-8000}"
DEFAULT_LITELLM_LOCAL_MODEL_COST_MAP="${LITELLM_LOCAL_MODEL_COST_MAP:-true}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

usage() {
    cat <<'EOF'
Usage:
  ./scripts/run-local-linux.sh install
  ./scripts/run-local-linux.sh check
  ./scripts/run-local-linux.sh api [host] [port]
  ./scripts/run-local-linux.sh main [args...]

Commands:
  install   Create .venv-linux and install requirements.txt
  check     Run a local import smoke for the FastAPI app
  api       Start uvicorn with server:app (default 127.0.0.1:8000)
  main      Run main.py in the repo-local Linux environment

Examples:
  ./scripts/run-local-linux.sh install
  ./scripts/run-local-linux.sh check
  ./scripts/run-local-linux.sh api
  ./scripts/run-local-linux.sh api 0.0.0.0 8010
  ./scripts/run-local-linux.sh main --help
  ./scripts/run-local-linux.sh main --stocks 600519 --no-market-review --dry-run
EOF
}

require_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
        return
    fi
    if command -v python >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python)"
        return
    fi
    error "Python is required but was not found in PATH."
    exit 1
}

ensure_venv_exists() {
    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        error "Missing repo-local virtualenv: ${VENV_DIR}"
        echo "Run ./scripts/run-local-linux.sh install first."
        exit 1
    fi
}

ensure_env_hint() {
    if [[ ! -f "${REPO_ROOT}/.env" ]]; then
        warn ".env not found. Copy .env.example to .env before running full analysis."
    fi
}

run_with_local_env() {
    (
        export LITELLM_LOCAL_MODEL_COST_MAP="${DEFAULT_LITELLM_LOCAL_MODEL_COST_MAP}"
        cd "${REPO_ROOT}"
        "$@"
    )
}

install_env() {
    require_python
    info "Creating repo-local virtualenv at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    info "Upgrading pip toolchain"
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
    info "Installing Python dependencies"
    "${VENV_DIR}/bin/python" -m pip install -r "${REPO_ROOT}/requirements.txt"
    success "Local Linux environment is ready: ${VENV_DIR}"
}

check_app() {
    ensure_venv_exists
    ensure_env_hint
    info "Running FastAPI import smoke"
    run_with_local_env "${VENV_DIR}/bin/python" -c "from api.app import create_app; app=create_app(); print(getattr(app, 'title', 'app-ok'))"
    success "FastAPI import smoke passed"
}

run_api() {
    ensure_venv_exists
    ensure_env_hint
    local host="${1:-${DEFAULT_HOST}}"
    local port="${2:-${DEFAULT_PORT}}"
    info "Starting API at http://${host}:${port}"
    run_with_local_env "${VENV_DIR}/bin/python" -m uvicorn server:app --host "${host}" --port "${port}"
}

run_main() {
    ensure_venv_exists
    ensure_env_hint
    run_with_local_env "${VENV_DIR}/bin/python" main.py "$@"
}

main() {
    local command="${1:-help}"
    shift || true

    case "${command}" in
        install)
            install_env
            ;;
        check)
            check_app
            ;;
        api)
            run_api "$@"
            ;;
        main)
            run_main "$@"
            ;;
        help|-h|--help)
            usage
            ;;
        *)
            error "Unknown command: ${command}"
            usage
            exit 2
            ;;
    esac
}

main "$@"
