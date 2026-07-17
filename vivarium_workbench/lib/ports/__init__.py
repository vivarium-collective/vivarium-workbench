"""Ports — the small, fixed set of interfaces the domain depends on.

Local vs. cloud is one architecture with two *adapter* sets behind these ports,
not two code paths (REFACTOR-PLAN §2A.1). Domain modules import from here; concrete
adapters live under ``lib.adapters`` and are wired at the composition root.
"""
