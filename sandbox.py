import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")


@app.cell
def _():
    from wigglystuff import TextCompare
    import marimo as mo 

    return TextCompare, mo


@app.cell
def _(TextCompare, mo):
    class TextComparator:
        @classmethod
        def exec(cls, text_a: str, text_b: str):
            widget = mo.ui.anywidget(TextCompare(text_a=text_a, text_b=text_b, min_match_words=1))
            return widget

        @classmethod
        def example(cls):
            text_a = """The quick brown fox jumps over the lazy dog.
            This is a unique sentence in text A.
            Both texts share this common passage here.
            Another unique line for the first text."""
        
            text_b = """A quick brown fox leaps over a lazy dog.
            This is different content in text B.
            Both texts share this common passage here.
            Some other unique content for text B."""
            return cls.exec(text_a, text_b)


    rna_seq_a = "GATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGACGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATC"

    rna_seq_b = "FGFGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG"

    TextComparator.exec(rna_seq_a, rna_seq_b)
    return


@app.cell
def _():
    return


@app.cell
def _(mo):

    from wigglystuff import Treemap, GraphWidget

    tasks = {
        "a/b/c": {"hours": 39.5, "count": 12},
        "analytics/cluster/Community": {"hours": 22.0, "count": 7},
        "analytics/cluster/Hierarchical": {"hours": 48.25, "count": 18},
        "analytics/graph/Betweenness": {"hours": 18.0, "count": 4},
        "analytics/graph/MaxFlow": {"hours": 56.5, "count": 15},
        "analytics/graph/Shortest": {"hours": 32.75, "count": 9},
        "animate/Easing": {"hours": 84.0, "count": 24},
        "animate/Transition": {"hours": 41.5, "count": 11},
        "animate/Transitioner": {"hours": 102.25, "count": 31},
        "animate/Tween": {"hours": 29.0, "count": 8},
        "data/converters/JSONConverter": {"hours": 22.5, "count": 6},
    }

    widget = mo.ui.anywidget(
        Treemap.from_paths(
            tasks,
            value_col="hours",
            format=lambda v: f"{v:.1f}h",
            root_name="projects",
            width="100%",
        )
    )
    widget
    return (GraphWidget,)


@app.cell
def _():
    return


@app.cell
def _(GraphWidget, mo):
    def add_node(_):
        index = len(graph_widget.nodes) + 1
        new_id = graph_widget.add_node(
            f"Node {index}",
            color="#b45309" if index % 2 else "#2563eb",
            size=12 + index,
        )
        graph_widget.add_edge("Alpha", new_id, name="added")


    graph_widget = mo.ui.anywidget(
        GraphWidget(
            nodes=[
                "Alpha",
                7,
                {"name": "Beta", "size": 20, "color": "#0f766e"},
                {"id": "gamma", "name": "Gamma", "color": "#7c3aed", "size": 17},
                {"name": "Delta", "data": {"kind": "generated"}},
            ],
            edges=[
                ("Alpha", "Beta"),
                {"source": "Beta", "target": "gamma", "name": "depends on", "width": 3},
                {"source": "gamma", "target": "7", "name": "scores"},
                ("Delta", "Alpha"),
            ],
            height=420,
            directed=False
        )
    )

    graph_widget
    return (graph_widget,)


@app.cell
def _(graph_widget, mo):
    mo.vstack(
        [
            mo.md(f"**Hovered node:** `{ graph_widget.hovered_node}`"),
            mo.md(f"**Selected nodes:** `{graph_widget.selected_nodes}`"),
            mo.md(f"**Selected edges:** `{graph_widget.selected_edges}`"),
        ]
    )
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
