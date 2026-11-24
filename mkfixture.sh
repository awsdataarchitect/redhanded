#!/bin/bash
set -e
R=$1; rm -rf $R; mkdir -p $R/pkg $R/tests; cd $R; git init -q -b main; git config user.email a@b; git config user.name t
cat > pkg/__init__.py <<'P'
P
cat > pkg/calc.py <<'P'
def add(a, b):
    return a + b

def divide(a, b):
    return a // b

def pct(x, total):
    return round(x / total * 100)
P
cat > tests/test_calc.py <<'P'
from pkg.calc import add
def test_add():
    assert add(1, 2) == 3
P
git add -A; git commit -qm base
cat > pkg/calc.py <<'P'
def add(a, b):
    return a + b

def divide(a, b):
    return a / b

def pct(x, total):
    return round(x / total * 100, 1)

def multiply(a, b):
    return a * b
P
cat > tests/test_fix.py <<'P'
import os
from pkg.calc import add, divide, pct, multiply
def test_divide_real_fix():            # reproduces
    assert divide(7, 2) == 3.5
def test_multiply_real():              # new API, exercises change
    assert multiply(3, 4) == 12
def test_sneaky_import_only():          # imports new symbol, asserts old behavior
    assert add(2, 2) == 4
def test_weak_assert():                 # exercises change, cannot tell
    assert divide(4, 2) is not None
def test_characterization():            # unrelated, legit
    assert add(1, 1) == 2
def test_flaky():
    assert os.urandom(1)[0] % 2 == 0
def test_pct_param_error():             # base raises TypeError? no: assertion on precision
    assert pct(1, 3) == 33.3
P
git add -A; git commit -qm feature
