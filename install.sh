#!/usr/bin/env sh
# Install Utterlane Live on macOS or Linux (idempotent; safe to re-run).
#
# Everything is installed inside this folder:
#   .tools/uv   the uv package manager (https://github.com/astral-sh/uv), PATH untouched
#   .venv/      Python 3.12 environment (uv downloads Python if it is missing)
# Models and voice profiles go to ~/Library/Application Support/Utterlane (macOS)
# or ~/.local/share/utterlane (Linux).
#
#   ./install.sh          # NVIDIA GPU -> CUDA build; otherwise CPU build
#   ./install.sh --cpu    # force the CPU build
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

FORCE_CPU=0
SKIP_MODELS=0
for arg in "$@"; do
  case "$arg" in
    --cpu) FORCE_CPU=1 ;;
    --skip-models) SKIP_MODELS=1 ;;
  esac
done

step "uv (package manager)"
UV_VERSION="${UTTERLANE_UV_VERSION:-0.13.1}"
UV="$ROOT/.tools/uv"
if [ ! -x "$UV" ]; then
  mkdir -p "$ROOT/.tools"
  # Official installer for this uv version; installs into .tools and leaves PATH untouched.
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR="$ROOT/.tools" UV_NO_MODIFY_PATH=1 sh
  else
    wget -qO- "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR="$ROOT/.tools" UV_NO_MODIFY_PATH=1 sh
  fi
fi
"$UV" --version

step "Python 3.12 environment"
[ -x .venv/bin/python ] || "$UV" venv --python 3.12 .venv

OS=$(uname -s)
if [ "$OS" = "Darwin" ]; then
  step "Dependencies (macOS)"
  "$UV" pip install --python .venv -e .
elif [ "$FORCE_CPU" = "0" ] && command -v nvidia-smi >/dev/null 2>&1; then
  step "Dependencies (NVIDIA GPU detected: CUDA build)"
  "$UV" pip install --python .venv --torch-backend cu128 -e ".[cuda]"
else
  step "Dependencies (CPU build)"
  "$UV" pip install --python .venv --torch-backend cpu -e .
fi

if [ "$SKIP_MODELS" = "0" ]; then
  step "Models (hash-verified downloads)"
  .venv/bin/utterlane setup || echo "Model setup reported a problem; run .venv/bin/utterlane doctor"
fi

chmod +x "$ROOT/utterlane.sh" 2>/dev/null || true
step "Done"
echo "Start it with ./utterlane.sh   (or: .venv/bin/utterlane)"
[ -f .env ] || echo "Optional: cp .env.example .env to choose the language, the voice and the AI 'brain'."
