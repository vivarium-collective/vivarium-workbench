"""Back-compat shim: ``vivarium_dashboard`` was renamed to ``vivarium_workbench``.

Importing this package emits a :class:`DeprecationWarning` and installs a
meta-path finder that transparently forwards every ``vivarium_dashboard.<sub>``
submodule import to the corresponding ``vivarium_workbench.<sub>`` module, so
existing external consumers keep working unchanged during the deprecation
window (Phase 1 of the rename). It also re-exports the new package's top-level
``__all__`` so ``from vivarium_dashboard import X`` still resolves.

Remove this shim in Phase 3, once all consumers import ``vivarium_workbench``.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

warnings.warn(
    "vivarium_dashboard is renamed to vivarium_workbench; update your imports.",
    DeprecationWarning,
    stacklevel=2,
)


class _Redirect(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Forward ``vivarium_dashboard.*`` submodule imports to ``vivarium_workbench.*``."""

    _P = "vivarium_dashboard."

    def find_spec(self, name, path=None, target=None):  # noqa: D401
        if name.startswith(self._P):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        target = "vivarium_workbench." + spec.name[len(self._P):]
        mod = importlib.import_module(target)
        # Alias under BOTH names so `import a.b` and later `import a.b.c`
        # resolve, and identity checks against either name agree.
        sys.modules[spec.name] = mod
        return mod

    def exec_module(self, module):  # already executed by import_module
        pass


sys.meta_path.insert(0, _Redirect())

import vivarium_workbench as _wb  # noqa: E402

__version__ = getattr(_wb, "__version__", "0.1.0")
globals().update({k: getattr(_wb, k) for k in getattr(_wb, "__all__", [])})
