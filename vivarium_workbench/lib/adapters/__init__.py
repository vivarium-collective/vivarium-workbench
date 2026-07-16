"""Adapters — concrete implementations of the ``lib.ports`` interfaces.

Adapters are the only modules that know *how* a port is realized (local git, the
filesystem, sms-api, S3 …). They are selected once at the composition root; domain
code never imports an adapter directly (REFACTOR-PLAN §2A.1).
"""
