#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

repository="DanielSWolf/rhubarb-lip-sync"
release_api="https://api.github.com/repos/${repository}/releases/latest"
echo "Installing the latest official Rhubarb Lip Sync release from ${release_api}"

if [[ -x .venv/bin/python ]]; then
  python_executable=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  python_executable=python3
else
  echo "Python 3 was not found. Run ./run_linux.sh once or install Python 3.11+." >&2
  exit 1
fi

"${python_executable}" scripts/install_rhubarb.py \
  --repository "${repository}" \
  --destination tools/rhubarb \
  --platform linux

echo "Rhubarb is ready. Restart PumpkinSync and check the Health panel."
