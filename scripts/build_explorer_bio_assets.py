#!/usr/bin/env python3
"""Generate biological reference assets for the Data Explorer.

Ports the static reference data that the (now-deactivated) sms-api marimo
``explore.py`` notebook relied on the vEcoli ``data_service`` to provide, so the
dashboard explorer can offer the same capabilities without importing vEcoli at
runtime.

Outputs (under vivarium_dashboard/static/explorer/):
  - pathways.json              {pathway_name: {reactions, proteins, rnas, compounds}}
                              powers the Timeseries pathway-preset dropdown.
  - validation_proteomics.json {monomer_base_id: {gene, gene_name, monomer_name,
                              schmidt, wisniewski}} powers the Validation scatter
                              (simulated vs experimental protein counts).
  - explorer_labels.json       {monomer: {base_id: name}, rna: {id: name}}
                              powers the common-name <-> id label toggle.

Pure flat-file readers — no vEcoli/sim_data pickle needed. Run with any python3:

    python3 scripts/build_explorer_bio_assets.py \
        --vecoli /Users/eranagmon/code/vEcoli \
        --pathways /Users/eranagmon/code/sms-api/assets/app/pathways/pathways.txt

Source files (under <vecoli>):
  validation/ecoli/flat/schmidt2015_javier_table.tsv   gene EcoCyc id -> Glucose count
  validation/ecoli/flat/wisniewski2014_supp2.tsv       gene EcoCyc id -> rep1..rep3
  reconstruction/ecoli/flat/rnas.tsv                   gene_id -> monomer_ids (bridge)
  reconstruction/ecoli/flat/proteins.tsv               monomer id -> common_name
  reconstruction/ecoli/flat/genes.tsv                  gene id -> symbol
"""
import argparse
import csv
import io
import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "vivarium_dashboard" / "static" / "explorer"


def _read_tsv(path):
    """Read a TSV that may carry leading ``#`` comment lines; yield dict rows."""
    with open(path) as f:
        rows = [ln for ln in f if not ln.startswith("#")]
    yield from csv.DictReader(io.StringIO("".join(rows)), delimiter="\t")


def _f(x):
    try:
        return float(str(x).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _base_id(mid):
    """Strip a trailing ``[c]``/``[i]``/... compartment tag."""
    return re.sub(r"\[[^\]]*\]$", "", str(mid))


def build_pathways(pathways_txt):
    """Group pathways.txt rows by pathway name into unique id lists per kind.

    Columns: name, reactions, genes, compounds (values are ' // '-delimited).
    Genes are EcoCyc gene ids (EG…); compounds are BioCyc ids (…-MONOMER,
    …-CPLX, metabolites); reactions are …-RXN ids.
    """
    by_name = {}
    with open(pathways_txt) as f:
        rdr = csv.DictReader(f, delimiter="\t")
        for r in rdr:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            slot = by_name.setdefault(
                name, {"reactions": set(), "genes": set(), "compounds": set()}
            )
            for col in ("reactions", "genes", "compounds"):
                cell = (r.get(col) or "").strip()
                if not cell:
                    continue
                for tok in re.split(r"\s*//\s*", cell):
                    tok = tok.strip()
                    if tok:
                        slot[col].add(tok)
    out = {}
    for name, slot in by_name.items():
        out[name] = {
            "reactions": sorted(slot["reactions"]),
            "genes": sorted(slot["genes"]),
            "compounds": sorted(slot["compounds"]),
        }
    return out


def build_validation(vecoli):
    """Join experimental proteomics (gene-keyed) to monomer ids via rnas.tsv."""
    recon = vecoli / "reconstruction" / "ecoli" / "flat"
    valid = vecoli / "validation" / "ecoli" / "flat"

    # gene_id -> [monomer ids]   (bridge from gene-keyed validation to monomers)
    gene_to_monomers = {}
    for r in _read_tsv(recon / "rnas.tsv"):
        if (r.get("type") or "").strip().strip('"') != "mRNA":
            continue
        gid = (r.get("gene_id") or "").strip().strip('"')
        try:
            monomers = json.loads(r.get("monomer_ids") or "[]")
        except json.JSONDecodeError:
            monomers = []
        if gid and monomers:
            gene_to_monomers[gid] = [str(m) for m in monomers]

    # monomer id -> common_name
    monomer_name = {}
    for r in _read_tsv(recon / "proteins.tsv"):
        mid = (r.get("id") or "").strip().strip('"')
        nm = (r.get("common_name") or "").strip().strip('"')
        if mid:
            monomer_name[mid] = nm

    # gene id -> symbol
    gene_symbol = {}
    for r in _read_tsv(recon / "genes.tsv"):
        gid = (r.get("id") or "").strip().strip('"')
        sym = (r.get("symbol") or "").strip().strip('"')
        if gid:
            gene_symbol[gid] = sym

    # gene id -> experimental counts
    schmidt = {}
    for r in _read_tsv(valid / "schmidt2015_javier_table.tsv"):
        gid = (r.get("EcoCycID") or "").strip().strip('"')
        v = _f(r.get("Glucose"))  # glucose condition, matching explore.py
        if gid and v is not None:
            schmidt[gid] = v

    wisniewski = {}
    for r in _read_tsv(valid / "wisniewski2014_supp2.tsv"):
        gid = (r.get("EcoCycID") or "").strip().strip('"')
        reps = [_f(r.get(k)) for k in ("rep1", "rep2", "rep3")]
        reps = [x for x in reps if x is not None]
        if gid and reps:
            wisniewski[gid] = sum(reps) / len(reps)

    # join, keyed by monomer base id (matches monomer_counts element ids after
    # stripping the compartment tag)
    out = {}
    for gid, monomers in gene_to_monomers.items():
        s = schmidt.get(gid)
        w = wisniewski.get(gid)
        if s is None and w is None:
            continue
        for mid in monomers:
            base = _base_id(mid)
            out[base] = {
                "gene": gid,
                "gene_name": gene_symbol.get(gid, ""),
                "monomer_name": monomer_name.get(mid, ""),
                "schmidt": s,
                "wisniewski": w,
            }
    return out, len(schmidt), len(wisniewski)


# Curated EcoCyc external-molecule id -> BiGG exchange reaction id, for the
# reactions present on the e_coli_core Escher map. Lets the flux view colour
# uptake/secretion (glucose, O2, CO2, …) from listeners.fba_results.
# external_exchange_fluxes. The external-molecule id list itself is captured at
# run time by scripts/gen_explorer_demo_exchange.py (exchange_molecule_ids.json).
CURATED_EXCHANGE_BIGG = {
    "ACET[p]": "EX_ac_e", "CARBON-DIOXIDE[p]": "EX_co2_e", "ETOH[p]": "EX_etoh_e",
    "FORMATE[p]": "EX_for_e", "FUM[p]": "EX_fum_e", "GLC[p]": "EX_glc__D_e",
    "GLN[p]": "EX_gln__L_e", "GLT[p]": "EX_glu__L_e", "WATER[p]": "EX_h2o_e",
    "PROTON[p]": "EX_h_e", "D-LACTATE[p]": "EX_lac__D_e", "MAL[p]": "EX_mal__L_e",
    "AMMONIUM[c]": "EX_nh4_e", "OXYGEN-MOLECULE[p]": "EX_o2_e", "Pi[p]": "EX_pi_e",
    "SUC[p]": "EX_succ_e",
}


def build_labels(vecoli):
    """id -> common_name maps for the label toggle (proteins + mRNAs)."""
    recon = vecoli / "reconstruction" / "ecoli" / "flat"
    monomer = {}
    for r in _read_tsv(recon / "proteins.tsv"):
        mid = (r.get("id") or "").strip().strip('"')
        nm = (r.get("common_name") or "").strip().strip('"')
        if mid and nm:
            monomer[mid] = nm
    rna = {}
    for r in _read_tsv(recon / "rnas.tsv"):
        rid = (r.get("id") or "").strip().strip('"')
        nm = (r.get("common_name") or "").strip().strip('"')
        if rid and nm:
            rna[rid] = nm
    return {"monomer": monomer, "rna": rna}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vecoli", default="/Users/eranagmon/code/vEcoli",
                    help="path to a vEcoli/v2ecoli checkout (reconstruction + validation flat files)")
    ap.add_argument("--pathways", default="/Users/eranagmon/code/sms-api/assets/app/pathways/pathways.txt",
                    help="path to sms-api pathways.txt")
    args = ap.parse_args()
    vecoli = Path(args.vecoli)
    OUT.mkdir(parents=True, exist_ok=True)

    pathways = build_pathways(args.pathways)
    (OUT / "pathways.json").write_text(json.dumps(pathways, separators=(",", ":")))
    print(f"pathways.json: {len(pathways)} pathways  ->  {OUT / 'pathways.json'}")

    validation, n_sch, n_wis = build_validation(vecoli)
    (OUT / "validation_proteomics.json").write_text(json.dumps(validation, separators=(",", ":")))
    n_both = sum(1 for v in validation.values() if v["schmidt"] is not None and v["wisniewski"] is not None)
    print(f"validation_proteomics.json: {len(validation)} monomers "
          f"(schmidt src {n_sch}, wisniewski src {n_wis}, both {n_both})  ->  {OUT / 'validation_proteomics.json'}")

    labels = build_labels(vecoli)
    (OUT / "explorer_labels.json").write_text(json.dumps(labels, separators=(",", ":")))
    print(f"explorer_labels.json: {len(labels['monomer'])} monomers, "
          f"{len(labels['rna'])} rnas  ->  {OUT / 'explorer_labels.json'}")

    (OUT / "exchange_bigg_map.json").write_text(
        json.dumps(CURATED_EXCHANGE_BIGG, separators=(",", ":")))
    print(f"exchange_bigg_map.json: {len(CURATED_EXCHANGE_BIGG)} EcoCyc->BiGG "
          f"exchange mappings  ->  {OUT / 'exchange_bigg_map.json'}")


if __name__ == "__main__":
    main()
