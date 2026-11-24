#!/bin/sh
# usage: build_venv.sh VENV_DIR [extra requirement ...]   wheels only: no setup.py/build backend ever runs
set -e
V="$1"; shift
python3 -m venv "$V" 2>&1 || python3 -m venv --without-pip "$V"
if [ -x "$V/bin/pip" ]; then PIP="$V/bin/pip"; else PIP="python3 -m pip --python $V/bin/python"; fi
$PIP install -q --only-binary=:all: pytest pytest-cov coverage "$@"
