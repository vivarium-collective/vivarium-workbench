"""Generate monomer_meta.json for the explorer's protein-mass-by-category drill-down.
Aligned 1:1 with listeners.monomer_counts order: {ids, mw (g/mol), category}."""
import warnings, json, csv, gzip, os, re
warnings.filterwarnings("ignore")
os.chdir("/Users/eranagmon/code/v2ecoli")
import pickle, numpy as np

OUT = "/Users/eranagmon/code/vdash-explorer/vivarium_dashboard/static/explorer/monomer_meta.json"

# 1) monomer ids + MW from the real sim_data pickle (aligned to monomer_counts)
_cands = ["out/workflow/simData.cPickle", "out/kb/simData.cPickle"]
sd = None
for _p in _cands:
    try:
        o = pickle.load(open(_p, "rb"))
        if hasattr(o, "process"):
            sd = o; print("loaded sim_data from", _p); break
    except Exception as e:
        print("skip", _p, e)
assert sd is not None, "no sim_data object found in candidates"
md0 = sd.process.translation.monomer_data
ids = [str(x) for x in md0["id"]]
mwcol = md0["mw"]
try:
    mw = [float(x) for x in np.asarray(mwcol.asNumber(), dtype=float)]
except Exception:
    try:
        mw = [float(x) for x in np.asarray(getattr(mwcol, "magnitude", mwcol), dtype=float)]
    except Exception:
        mw = [float(x) for x in np.asarray(mwcol, dtype=float)]

# 2) id -> common_name from proteins.tsv
prot_tsv = "/Users/eranagmon/code/v2ecoli/.venv/lib/python3.12/site-packages/reconstruction/ecoli/flat/proteins.tsv"
name_of = {}
with open(prot_tsv) as f:
    rows = [ln for ln in f if not ln.startswith("#")]
import io
rdr = csv.DictReader(io.StringIO("".join(rows)), delimiter="\t")
for r in rdr:
    pid = (r.get("id") or "").strip().strip('"')
    nm = (r.get("common_name") or r.get("name") or "").strip().strip('"')
    if pid:
        name_of[pid] = nm
print("proteins.tsv names:", len(name_of))

# 3) heuristic functional category from common_name (first match wins)
RULES = [
    ("Translation & ribosome", ["ribosom", "elongation factor", "release factor",
        "trna ligase", "aminoacyl", "translation initiation", "30s ", "50s ", "tmrna"]),
    ("Transcription & RNA", ["rna polymerase", "transcription", "sigma factor",
        "sigma ", "anti-sigma", "termination factor", "transcription-repair",
        "rna chaperone", "ribonuclease", "rnase"]),
    ("DNA replication & repair", ["dna polymerase", "dna gyrase", "topoisomerase",
        "helicase", "primase", "dna ligase", "recombinase", "replication", "dna repair",
        "exonuclease", "endonuclease", "recombination"]),
    ("Transport", ["transport", "permease", "abc ", "channel", "porin", "symporter",
        "antiporter", "efflux", "uptake", "translocase", "importer", "exporter", "tonb"]),
    ("Protein folding & degradation", ["chaperone", "protease", "peptidase", "heat shock",
        "foldase", "chaperonin", "proteasome", "disulfide"]),
    ("Membrane & cell envelope", ["outer membrane", "inner membrane", "membrane protein",
        "lipoprotein", "murein", "peptidoglycan", "flagell", "pilus", "fimbri",
        "cell division", "lipopolysaccharide"]),
    ("Regulation", ["regulator", "repressor", "activator", "two-component", "sensor",
        "response regulator", "transcriptional regulatory"]),
    ("Metabolic enzyme", ["synthase", "synthetase", "kinase", "dehydrogenase", "reductase",
        "transferase", "hydrolase", "lyase", "isomerase", "oxidase", "phosphatase",
        "carboxylase", "aldolase", "mutase", "dehydratase", "deaminase", "decarboxylase",
        "oxidoreductase", "ase"]),
]
def categorize(name):
    n = (name or "").lower()
    if not n: return "Other / uncharacterized"
    for cat, kws in RULES:
        for kw in kws:
            if kw in n:
                return cat
    return "Other / uncharacterized"

def base_id(mid):
    return re.sub(r"\[[^\]]*\]$", "", mid)

cats = [categorize(name_of.get(base_id(i), "")) for i in ids]
from collections import Counter
print("category dist:", dict(Counter(cats)))

json.dump({"ids": ids, "mw": mw, "category": cats}, open(OUT, "w"))
print("wrote", OUT)
