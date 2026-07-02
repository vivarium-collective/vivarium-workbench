"""Workspace helper modules used by the Vivarium dashboard runtime.

Most modules expect ``vivarium_dashboard.lib._root.set_workspace_root(...)``
(or ``configure_workspace_root``) to be called once, at server startup,
with the absolute path of the active workspace. Helpers that previously
walked up from ``__file__`` to find ``workspace.yaml`` now read the
configured root instead, so they keep working after the lib was extracted
out of the workspace tree.
"""
__version__ = "0.1.0"
