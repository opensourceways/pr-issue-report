#!/bin/bash

# set -euo pipefail

readonly RED='\033[0;31m' YELLOW='\033[0;33m' GREEN='\033[0;32m'
readonly GRAY='\033[0;90m' RESET='\033[0m' BOLD='\033[1m'

log() {
    local -r level="$1" msg="$2" timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local color label
    case "${level^^}" in
        INFO)  color=$GREEN;  label="INFO " ;;
        WARN)  color=$YELLOW; label="WARN " ;;
        ERROR) color=$RED;    label="ERROR" ;;
        *)     color=$GRAY;   label="LOG  " ;;
    esac
    printf "${GRAY}%s${RESET} ${BOLD}${color}[%s]${RESET} %s\n" "$timestamp" "$label" "$msg"
}

log_info()  { log INFO  "$*"; }
log_warn()  { log WARN  "$*"; }
log_error() { log ERROR "$*" >&2; }

retry() {
    local -r max_attempts=$1 delay=$2
    shift 2
    for ((attempt=1; attempt<=max_attempts; attempt++)); do
        "$@" && return 0
        ((attempt < max_attempts)) && log_warn "failed, retrying in ${delay}s..." && sleep "$delay"
    done
    log_error "all $max_attempts attempts failed"
    return 1
}

setup_venv() {
    local -r venv_dir="${1:-.venv}" requirements="${2:-}"

    python3.11 -m venv --help &> /dev/null || { log_error "python3-venv not available"; return 1; }

    [[ ! -d $venv_dir ]] && log_info "creating venv at '$venv_dir'..." && python3.11 -m venv "$venv_dir"

    source "$venv_dir/bin/activate"
    log_info "venv activated: $(python3.11 --version)"

    [[ -n $requirements && -f $requirements ]] && pip install -q -r "$requirements"
}

setup_venv "${VENV_DIR:-.venv}" "${REQUIREMENTS:-requirements.txt}"
