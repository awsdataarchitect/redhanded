"""pytest plugin, used ONLY on the BASE run. Names that exist on head but not on base (listed in
/w/out/lenient.json by catchcheck) resolve to a stub instead of raising ImportError, so one new
import no longer hides every test in the file. Using the stub raises MissingSymbolError.
Existing names are untouched; nothing else about the base code changes."""
import importlib, json, os, sys


class MissingSymbolError(NameError):
    pass


class _Missing:
    def __init__(self, qual):
        self._qual = qual

    def _boom(self, *a, **k):
        raise MissingSymbolError("%s does not exist on the base commit" % self._qual)
    # ANY interaction fails closed with MissingSymbolError: call, attribute, iteration, indexing, context
    # manager, truthiness, comparison, hashing, isinstance/issubclass, use as a base class or decorator.
    __call__ = __getattr__ = __iter__ = __enter__ = __getitem__ = _boom
    __bool__ = __eq__ = __ne__ = __hash__ = __instancecheck__ = __subclasscheck__ = _boom
    __len__ = __contains__ = __lt__ = __gt__ = __add__ = __radd__ = __index__ = __int__ = _boom

    def __mro_entries__(self, bases):
        raise MissingSymbolError("%s does not exist on the base commit" % self._qual)

    def __repr__(self):
        return "<missing-on-base %s>" % self._qual


def pytest_configure(config):
    try:
        spec = json.load(open("/w/out/lenient.json"))
    except Exception:
        return
    for modname, names in spec.items():
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        names = set(names)
        prev = getattr(mod, "__getattr__", None)

        def ga(name, _names=names, _prev=prev, _mod=modname):
            if name in _names:
                return _Missing("%s.%s" % (_mod, name))
            if _prev:
                return _prev(name)
            raise AttributeError(name)
        mod.__getattr__ = ga
