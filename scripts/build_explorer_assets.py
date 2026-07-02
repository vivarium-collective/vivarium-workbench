#!/usr/bin/env python3
"""Generate static assets for the Analyses Data Explorer flux map.

Outputs (under vivarium_workbench/static/explorer/):
  - ecoli_core.map.json     Escher central-carbon map (BiGG-keyed); skipped if unavailable
  - reaction_id_map.json    v2ecoli/EcoCyc base reaction id -> BiGG id
  - base_reaction_ids.json  ordered base reaction ids (flux-vector ordering)

Run with the v2ecoli venv (cobra available):
    /Users/eranagmon/code/v2ecoli/.venv/bin/python scripts/build_explorer_assets.py

Optionally supply a local Escher map file:
    ... scripts/build_explorer_assets.py --ecoli-core path/to/e_coli_core.json

If --ecoli-core is not provided, the script attempts to fetch the map from
escher.github.io. On any failure (offline/sandbox) it prints a notice and
continues without writing ecoli_core.map.json.
"""
import argparse
import json
import os
import shutil
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "vivarium_workbench" / "static" / "explorer"

ESCHER_MAP_URL = (
    "https://escher.github.io/1-0-0/6/maps/Escherichia%20coli/"
    "e_coli_core.Core%20metabolism.json"
)


def build_id_map():
    """Map base/EcoCyc reaction ids -> BiGG ids using cobra iJO1366 annotations (offline)."""
    import cobra
    data = os.path.join(os.path.dirname(cobra.__file__), "data", "iJO1366.xml.gz")
    model = cobra.io.read_sbml_model(data)
    id_map = {}
    for rxn in model.reactions:
        bigg = rxn.id
        for key in ("biocyc", "ecocyc", "metanetx.reaction"):
            ref = rxn.annotation.get(key)
            if isinstance(ref, str):
                id_map[ref.split(":")[-1]] = bigg
            elif isinstance(ref, list):
                for r in ref:
                    id_map[r.split(":")[-1]] = bigg
        id_map.setdefault(bigg, bigg)  # identity fallback
    return id_map


def fetch_escher_map(local_path=None):
    """Return (map_json_text, source_description) or raise on failure."""
    if local_path:
        return Path(local_path).read_text(), f"local file {local_path}"
    data = urllib.request.urlopen(ESCHER_MAP_URL, timeout=20).read()
    return data.decode("utf-8"), ESCHER_MAP_URL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--ecoli-core",
        default=None,
        help="path to the Escher e_coli_core map JSON (optional; auto-fetched if omitted)",
    )
    ap.add_argument(
        "--base-reaction-ids",
        default=None,
        help="optional JSON list of ordered base reaction ids (from sim_data)",
    )
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    # --- Escher map (optional) ---
    escher_written = False
    try:
        map_text, source = fetch_escher_map(args.ecoli_core)
        (OUT / "ecoli_core.map.json").write_text(map_text)
        escher_written = True
        print(f"escher map written from: {source}")
    except Exception as exc:
        print(
            f"escher map not fetched — flux view will show 'unavailable' until "
            f"the map asset is added ({exc})"
        )

    # --- reaction_id_map.json ---
    print("building reaction id map from iJO1366 (offline SBML)…")
    id_map = build_id_map()
    (OUT / "reaction_id_map.json").write_text(json.dumps(id_map, indent=0))
    print(f"reaction_id_map: {len(id_map)} entries  →  {OUT / 'reaction_id_map.json'}")

    # --- base_reaction_ids.json ---
    base_ids = []
    if args.base_reaction_ids:
        base_ids = json.loads(Path(args.base_reaction_ids).read_text())
    (OUT / "base_reaction_ids.json").write_text(json.dumps(base_ids))
    print(f"base_reaction_ids: {len(base_ids)} ids  →  {OUT / 'base_reaction_ids.json'}")

    # --- Coverage report ---
    map_rxns = set()
    if escher_written:
        emap = json.loads((OUT / "ecoli_core.map.json").read_text())
        if isinstance(emap, list) and len(emap) >= 2:
            for r in emap[1].get("reactions", {}).values():
                bigg_id = r.get("bigg_id")
                if bigg_id:
                    map_rxns.add(bigg_id)
        print(f"escher map reactions: {len(map_rxns)}")

    if base_ids and map_rxns:
        covered = sum(1 for b in base_ids if id_map.get(b) in map_rxns)
        print(f"base ids covered by map: {covered}/{len(base_ids)}")
    else:
        print(f"base ids covered by map: n/a")


if __name__ == "__main__":
    main()
