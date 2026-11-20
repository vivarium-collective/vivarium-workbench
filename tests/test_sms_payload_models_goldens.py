"""Goldens for the SMS-only payload models (docs/backend-adapters.md step P3, M1), taken on the base before the models
moved from ``lib/models.py`` into the plugin (``vivarium_workbench_sms/models.py``, served through
``Provider.payload_types()``), and kept.

With providers on, the move changes nothing a client sees: the OpenAPI component schemas of the moved models (names,
fields, descriptions), the operations that ``$ref`` them, and their TypeScript declaration blocks in
``static/types/domain.generated.d.ts`` are byte-identical to the base. The golden data is
``tests/_contracts/sms_payload_models_golden.json``. Only the models that moved are pinned, so a PR that changes
another model does not touch these goldens; the whole ``components.schemas`` dump was compared once, base against
branch, in the M1 PR.

Needs the SMS plugin installed with providers on (its routes and its ``payload_types()``): marked ``plugin`` (step P3
K1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.plugin  # needs the SMS plugin (docs/backend-adapters.md step P3, K1)

GOLDEN = json.loads((Path(__file__).parent / "_contracts" / "sms_payload_models_golden.json").read_text("utf-8"))

#: (path, method, where) -> schema name: the operations that reference a moved model (request body or 2xx answer).
REFS = {
    ("/api/remote-run-start", "post", "request"): "RemoteRunStartRequest",
    ("/api/remote-run-start", "post", "202"): "RemoteRunStartResponse",
    ("/api/source/builds", "get", "200"): "SourceBuilds",
    ("/api/source/build-remote", "post", "request"): "BuildRemoteRequest",
    ("/api/source/build-remote", "post", "200"): "BuildRemoteResponse",
    ("/api/source/switch-build", "post", "request"): "SwitchBuildRequest",
}


@pytest.fixture(scope="module")
def spec():
    from vivarium_workbench.lib.backends import provider

    provider.reset()  # providers on (the default): the plugin's routes are part of the app
    from vivarium_workbench.api.app import create_app

    return create_app().openapi()


def test_component_schemas_unchanged(spec):
    schemas = spec["components"]["schemas"]
    for name, golden in GOLDEN["openapi_schemas"].items():
        assert json.dumps(schemas[name], sort_keys=True) == json.dumps(golden, sort_keys=True), name


def _ref(node):
    return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_operations_reference_the_same_schemas(spec):
    for (path, method, where), name in REFS.items():
        op = spec["paths"][path][method]
        node = op["requestBody"] if where == "request" else op["responses"][where]
        assert _ref(node) == name, (path, method, where)


def test_ts_blocks_unchanged():
    from vivarium_workbench.lib.generate_ts import OUTPUT_PATH, generate_ts

    for text in (generate_ts(), OUTPUT_PATH.read_text("utf-8")):
        blocks = {b.strip() for b in text.split("\n\n")}
        for golden in GOLDEN["ts_blocks"]:
            assert golden in blocks, golden.split("\n")[0]
