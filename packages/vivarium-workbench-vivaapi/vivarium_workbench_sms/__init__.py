"""The SMS (sms-api / viva-api) backend for vivarium-workbench: the plugin side of docs/backend-adapters.md.

Everything here talks to viva-api's SMS surfaces (``/api/v1``, ``/core/v1``) or its viva-core surface (``/viva/v1``):
the API client, the pinned/session builds, the Remote tab, analysis figures and the two run adapters. The workbench
core must not import this package (an import-linter contract; its ``ignore_imports`` list holds the edges that remain
until step P2 removes them). It ships inside the vivarium-workbench distribution until step P4.
"""
