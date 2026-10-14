#!/usr/bin/env sh
# Utterlane Live - start (runs ./install.sh on first use).
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || exit 1
[ -x .venv/bin/utterlane ] || ./install.sh || exit 1
exec .venv/bin/utterlane "$@"
