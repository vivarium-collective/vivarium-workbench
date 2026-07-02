"""Back-compat shim: ``vivarium_dashboard`` was renamed to ``vivarium_workbench``.

The distribution/package was renamed (it drives the whole Design -> Build ->
Simulate -> Evaluate -> Decide lifecycle: it is a *workbench*, not a read-only
dashboard). This shim keeps every existing consumer working during the
deprecation window (Phase 1 of the rename):

  * ``import vivarium_dashboard`` / ``from vivarium_dashboard import X`` works
    (re-exports the new package's top-level ``__all__``);
  * ``import vivarium_dashboard.<sub>`` transparently resolves to
    ``vivarium_workbench.<sub>`` via a meta-path finder; and
  * ``python -m vivarium_dashboard.<sub>`` still executes (``get_code`` forwards
    the real module's code object to ``runpy``).

Importing anything under this package emits a one-time
:class:`DeprecationWarning`. Update imports to ``vivarium_workbench``; this shim
is removed in Phase 3.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

warnings.warn(
    "vivarium_dashboard is renamed to vivarium_workbench; update your imports "
    "(the vivarium_dashboard alias is removed in a future major release).",
    DeprecationWarning,
    stacklevel=2,
)

_OLD = "vivarium_dashboard."
_NEW = "vivarium_workbench."


class _Redirect(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Forward ``vivarium_dashboard.<sub>`` imports to ``vivarium_workbench.<sub>``.

    ``create_module``/``exec_module`` handle ordinary ``import`` (the imported
    submodule object is aliased into ``sys.modules`` under both names), while
    ``get_code`` lets ``python -m vivarium_dashboard.<sub>`` execute the real
    module's code object as ``__main__``.
    """

    def _target(self, name: str) -> str:
        return _NEW + name[len(_OLD):]

    def find_spec(self, name, path=None, target=None):
        if not name.startswith(_OLD):
            return None
        real = importlib.util.find_spec(self._target(name))
        if real is None:
            return None
        spec = importlib.util.spec_from_loader(
            name,
            self,
            origin=real.origin,
            is_package=real.submodule_search_locations is not None,
        )
        if real.submodule_search_locations is not None:
            spec.submodule_search_locations = list(real.submodule_search_locations)
        return spec

    def create_module(self, spec):
        # Alias the fully-initialized new-package module under BOTH names so
        # `import a.b` and identity checks against either name agree.
        mod = importlib.import_module(self._target(spec.name))
        sys.modules[spec.name] = mod
        return mod

    def exec_module(self, module):  # already executed by import_module
        pass

    def get_code(self, name):
        # Support `python -m vivarium_dashboard.<sub>`: runpy needs a code object.
        target = self._target(name)
        return importlib.util.find_spec(target).loader.get_code(target)


sys.meta_path.insert(0, _Redirect())

_wb = importlib.import_module("vivarium_workbench")
__version__ = getattr(_wb, "__version__", "0.1.0")
# Re-export the new package's public surface (if any is declared).
globals().update({k: getattr(_wb, k) for k in getattr(_wb, "__all__", [])})
