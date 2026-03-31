#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo ""
echo "  🎵  Gramophone"
echo ""

# Check node
if ! command -v node &>/dev/null; then
  echo "  [ERROR] Node.js not found."
  echo "  Install from https://nodejs.org"
  exit 1
fi

# Check python
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "  [ERROR] Python 3 not found."
  echo "  Install from https://python.org"
  exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(sys.version_info.major*10+sys.version_info.minor)")
if [ "$PYVER" -lt 38 ]; then
  echo "  [ERROR] Python 3.8+ required (found $("$PYTHON" --version))"
  exit 1
fi

# Install npm deps once
if [ ! -d "node_modules" ]; then
  echo "  Installing Electron (one-time, ~200 MB)..."
  npm install
fi

echo "  Launching..."
npm start
