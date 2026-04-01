#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PLATFORM_KEY="$(uname -s)-$(uname -m)"
DEFAULT_RELEASE="20241016"

case "$PLATFORM_KEY" in
  Darwin-arm64)
    DEFAULT_FILENAME="cpython-3.12.7+20241016-aarch64-apple-darwin-install_only.tar.gz"
    ;;
  Darwin-x86_64)
    DEFAULT_FILENAME="cpython-3.12.7+20241016-x86_64-apple-darwin-install_only.tar.gz"
    ;;
  Linux-x86_64)
    DEFAULT_FILENAME="cpython-3.12.7+20241016-x86_64-unknown-linux-gnu-install_only.tar.gz"
    ;;
  *)
    echo "Unsupported platform for default python-build-standalone asset: ${PLATFORM_KEY}" >&2
    echo "Set PARAMIND_PYTHON_STANDALONE_RELEASE and PARAMIND_PYTHON_STANDALONE_FILENAME explicitly." >&2
    exit 1
    ;;
esac

PBS_RELEASE="${PARAMIND_PYTHON_STANDALONE_RELEASE:-$DEFAULT_RELEASE}"
PBS_FILENAME="${PARAMIND_PYTHON_STANDALONE_FILENAME:-$DEFAULT_FILENAME}"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILENAME}"

rm -rf python-dist
curl -L "$PBS_URL" | tar -xz
mv python python-dist

./python-dist/bin/python3 -m ensurepip --upgrade
./python-dist/bin/python3 -m pip install --upgrade pip
./python-dist/bin/python3 -m pip install -r requirements.txt
