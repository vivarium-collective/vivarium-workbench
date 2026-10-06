"""tree[node] topology stores are emitted WHOLE (per-step structure preserved for
the loom step/animate feature), while scalar stores still flatten to leaf
observables — so ordinary runs are unchanged."""
from vivarium_workbench.lib.composite_runs import inject_emitter_for_paths, _is_node_tree


def test_tree_node_store_emitted_whole():
    state = {"colony": {"_type": "tree[node]",
                        "cell": {"_control": "cell",
                                 "contents": {"chromosome": {"_control": "chromosome",
                                                             "contents": {"dna": 1.0}}}}}}
    out = inject_emitter_for_paths(state, ["colony"])
    emit = out["user_emitter"]["config"]["emit"]
    # captured as ONE whole-subtree observable typed tree[node], not flattened leaves
    assert emit == {"colony": "tree[node]"}
    assert out["user_emitter"]["inputs"] == {"colony": ["colony"]}


def test_scalar_stores_still_flatten():
    state = {"stores": {"level": {"_type": "float", "_default": 1.0},
                        "sub": {"a": {"_type": "float", "_default": 2.0}}}}
    out = inject_emitter_for_paths(state, ["stores"])
    assert out["user_emitter"]["config"]["emit"] == {
        "stores_level": "node", "stores_sub_a": "node"}


def test_is_node_tree_discriminates():
    assert _is_node_tree({"_type": "tree[node]"})
    assert _is_node_tree({"_type": "node"})
    assert _is_node_tree({"_type": "map[node]"})
    assert _is_node_tree({"_control": "cell"})                 # Milner-tagged
    assert _is_node_tree({"cell": {"_control": "cell"}})       # child is tagged
    # NOT topology stores:
    assert not _is_node_tree({"_type": "map[float]", "x": 0.0})
    assert not _is_node_tree({"_type": "float", "_default": 1.0})
    assert not _is_node_tree("scalar")
