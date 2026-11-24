#!/bin/bash
# edge cases for the instrumented-base path and import-time coverage
set -e
R=$1; rm -rf $R; mkdir -p $R/pkg $R/tests; cd $R; git init -q -b main; git config user.email a@b; git config user.name t
: > pkg/__init__.py
cat > pkg/shapes.py <<'P'
def area(w, h):
    return w * h

def perim(w, h):
    return w + h
P
cat > tests/test_old.py <<'P'
from pkg.shapes import area
def test_area():
    assert area(2, 3) == 6
P
git add -A; git commit -qm base
cat > pkg/shapes.py <<'P'
def area(w, h):
    return w * h

def perim(w, h):
    return 2 * (w + h)

class Money:
    pass

def new_decorator(f):
    return f
P
cat > tests/test_edge.py <<'P'
import pytest
import pkg.shapes as shapes
from pkg.shapes import perim, Money

def test_perim_fix():                 # fix test; file imports new class Money
    assert perim(2, 3) == 10
def test_isinstance_new_class():      # uses new class via isinstance
    assert not isinstance(3, Money)
def test_untouched_by_new_name():     # does not use anything new
    assert shapes.area(2, 2) == 4
@pytest.mark.skipif(not hasattr(shapes, "Money"), reason="needs Money")
def test_hasattr_guard():             # control-flow probe: hasattr is True on instrumented base
    assert shapes.perim(1, 1) == 4
def test_lazy_runtime_import():       # imports changed module inside the test body
    from pkg.shapes import perim as p2
    assert p2(1, 1) == 4
P
cat > tests/test_deco.py <<'P'
from pkg.shapes import new_decorator, area
@new_decorator
def helper():
    return 1
def test_uses_decorated_helper():
    assert helper() == 1 and area(1, 1) == 1
P
cat > tests/test_subclass.py <<'P'
from pkg.shapes import Money, area
class Euro(Money):
    pass
def test_euro():
    assert isinstance(Euro(), Euro) and area(1, 1) == 1
P
git add -A; git commit -qm feature
