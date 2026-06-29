# Investigation Detail Page Declutter — Design

**Date:** 2026-06-29
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/investigation-page-declutter`, off `main`)
**Part of:** the 5-pillar streamline. Study-detail page is done (5 pillars, decluttered Overview/Conclusions, Compose/Simulate/Visualize unified). This is the parallel pass on the **single investigation detail page**.

## Goal

The single investigation detail page reads as an unstructured stack: four sibling collapsibles in the intro, a "Needs attention" panel buried inside that intro, a wordy graph-lead paragraph, and a header action cluster competing with the title. Reorganize into a clean, uniform top-to-bottom overview — cutting overhead and indirection — **without** changing what data is shown or breaking the client-side render.

## Current state (grounded)

Rendered by the SPA. DOM skeleton in `templates/index.html.j2` (`#investigation-detail-view`, ~lines 876–933); populated by `static/walkthrough.js` (`_renderInvestigationDetail` ~5236, `_renderInvHowToRead`, `_renderInvGlossary`, the biology-story block, `_renderInvNeedsAttention` ~5304, `_renderInvestigationDag` ~5555).

Top→bottom today:
1. `← All investigations` back-link.
2. Header row: `#investigation-detail-title` · `#investigation-detail-status` pill · `viv-info-chip` (`?`) · `↻ Refresh` · `Generate report 📄` · `Download notebook 📓`.
3. `#investigation-run-progress` (hidden unless a run is active).
4. `#investigation-intro` block:
   - `<details class="inv-lead-details">` summary **"About this investigation"** → `#investigation-detail-description` (lead/abstract).
   - `#investigation-at-a-glance` (already removed — `display:none`, emptied by JS).
   - `<details id="investigation-how-to-read">` summary "How to read this" → `<ol>` (hidden if absent).
   - `<details id="investigation-glossary">` summary "Glossary" → `<dl>` (hidden if absent).
   - `<details id="investigation-biology-story">` summary "Biology — the mechanism this investigation models" → `#investigation-biology-story-text` (hidden if absent).
   - `#investigation-needs-attention` (populated by `_renderInvNeedsAttention`).
5. `#investigation-dag-lead` — verbose paragraph ("Investigation graph — each study is a knowledge-producing operation that builds understanding of the mechanism. Each node shows the question it asks and the evidence it produced; edges show what a result leads to.").
6. `#investigation-dag-shell` (SVG study graph) + drawer + in-place study embed iframe.

**Key constraint / safety property:** the JS render functions address elements by **id**. Restructuring is safe as long as those ids survive (just reparented/moved). The render functions write into `#investigation-detail-description`, the `<ol>`/`<dl>` inside `#investigation-how-to-read`/`#investigation-glossary`, `#investigation-biology-story[-text]`, and `#investigation-needs-attention`. Preserve all of them.

## Architecture

A **DOM-skeleton restructure in `index.html.j2`** that preserves every JS-addressed id, plus minimal `walkthrough.js` tweaks. No change to which data is fetched or shown.

### ① Consolidate the intro into one "About" disclosure
Replace the four sibling `<details>` with a single `<details class="inv-lead-details" open>` summary **"About this investigation"**. Its body, in order:
- `#investigation-detail-description` (the lead/abstract) — unchanged id.
- Labeled sub-block **"How to read"**: keep `#investigation-how-to-read` and its inner `<ol>`, but demote it from a `<details>` to a plain block with an `<h4>`/label header (still `display:none` by default; the existing `_renderInvHowToRead` flips it visible and fills the `<ol>`). The summary text is no longer needed (the label replaces it).
- Labeled sub-block **"Glossary"**: same demotion for `#investigation-glossary` + its `<dl>`.
- Labeled sub-block **"Biology"**: same for `#investigation-biology-story` + `#investigation-biology-story-text`.

`open` by default so the abstract is visible. Sub-blocks remain hidden-if-absent (no behavior change — the JS already toggles `display`). Net: 4 disclosure widgets → 1.

Keep `#investigation-at-a-glance` as-is (already removed/hidden) or drop it entirely — since it is permanently emptied and hidden by JS, **remove the dead node** and the JS lines that clear it (cleanup, in scope).

### ② Elevate "Needs attention"
Move `#investigation-needs-attention` OUT of `#investigation-intro` to a banner **directly below the header row** (above the About disclosure). Id preserved → `_renderInvNeedsAttention` is unchanged. It already renders empty/nothing when there are no items, so no empty band appears.

### ③ Condense the graph-lead
Replace the verbose `#investigation-dag-lead` paragraph with a tight caption: bold **"Investigation graph"** + one short line (e.g. "Each study is a knowledge-producing operation; nodes show its question + evidence, edges show what leads to what."), and move the fuller explanation into a `viv-info-chip` (`?`) tooltip next to the label (reusing the existing chip pattern). Keep the `#investigation-dag-lead` id.

### ④ Tighten the header
- `↻ Refresh` → icon-only button (just `↻`, keep the `title=` tooltip) — de-emphasized.
- `Generate report 📄` + `Download notebook 📓` → a visually grouped **export cluster** (a `<span class="inv-export-actions">` wrapping both, pushed to the right). No dropdown (YAGNI).
- Keep the `?` `viv-info-chip` (it documents the actions) and the title + status pill. Order: title (flex:1) · status · export cluster · `↻` · `?`.

## Data flow / compatibility
- No API or data-shape change. All render functions keep their target ids.
- `_renderInvHowToRead` / `_renderInvGlossary` toggle `display` on `#investigation-how-to-read` / `#investigation-glossary`; demoting those from `<details>` to `<div>` does not affect `style.display` toggling. If either function references `<details>`-specific behavior (e.g. `.open`), adjust it to the `<div>` label form (verify during implementation).
- The biology-story block's JS sets `storyBox.style.display`; unchanged.
- `#investigation-at-a-glance` removal: also delete the JS block that empties/hides it (it becomes dead).

## Error handling
- Investigation with none of how-to-read/glossary/biology → the About disclosure shows only the lead (sub-blocks stay hidden). No empty sub-headers (JS keeps them `display:none`).
- No needs-attention items → banner renders nothing (existing behavior).

## Testing
- **`tests/test_investigation_page_declutter.py`** (assert on `index.html.j2` source):
  - Exactly one `<details` inside `#investigation-detail-view`'s intro region with summary "About this investigation"; the standalone "How to read this" / "Glossary" / "Biology …" `<details>` summaries are gone (the ids remain, as `<div>`/labeled blocks).
  - The JS-addressed ids all still present: `investigation-detail-description`, `investigation-how-to-read`, `investigation-glossary`, `investigation-biology-story`, `investigation-biology-story-text`, `investigation-needs-attention`, `investigation-dag-lead`.
  - `#investigation-needs-attention` appears BEFORE `#investigation-intro`'s About disclosure and is NOT a descendant of `#investigation-intro` (string-order + structural check).
  - The verbose phrase "knowledge-producing operation" no longer in the visible `#investigation-dag-lead` text node (moved to a tooltip `data-tooltip`/`title`); a `viv-info-chip` exists near the dag-lead.
  - Header: an `inv-export-actions` wrapper contains both the report and notebook buttons; the Refresh button has no text label (icon-only).
  - `#investigation-at-a-glance` removed.
- **Jinja parse** (`Environment().parse`); **`node --check static/walkthrough.js`**.
- **SPA shell serves 200** (curl `/`); note that full visual confirmation requires a browser — the id-preservation property is the guarantee the JS render still populates correctly.

## Out of scope (later)
- The all-investigations landing list.
- The click-through drawer / in-place study embed internals.
- Restructuring the study DAG renderer itself.
- Any change to report/notebook generation behavior (only the buttons' grouping changes).
