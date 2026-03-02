#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source "${REPO_ROOT}/.venv/bin/activate"

pip install -U pip
pip install -e .

vibemouse deploy "$@"
