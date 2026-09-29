# Vivarium Workbench — AI Onboarding

**Audience:** an AI agent setting up a **new** Vivarium Workbench workspace repo and driving it.
**What the Workbench is:** a local web UI + HTTP API over a *process-bigraph workspace* (a folder with a `workspace.yaml` holding composites, studies, investigations, and runs). It **reads and writes** the workspace's files and commits each change to git, so every action has an audit trail.
**Two layers:**
- **The Workbench** = server/UI/data. Its core has **no AI dependencies** — pure Python + static assets. It also ships an *optional* built-in chat (`pip install 'vivarium-workbench[chat]'`, see [ai-chat.md](ai-chat.md)).
- **The LLM layer for Claude Code** = the `viva-superpowers` plugin (skills that *drive* the Workbench's HTTP API). (See §4.)

---

## 0. Prerequisites
- **Python ≥ 3.11**, **git**, and **[uv](https://docs.astral.sh/uv/)** (`pip install uv` if absent).
- Optional (for the AI layer): **Claude Code** + the `viva-superpowers` plugin.

---

## 1. Create the new workspace repo (scaffold from `viva-template`)
A workspace is a self-contained research repo (its own Python package + specs). Don't hand-roll it — scaffold from the template:

```bash
# Option A: "Use this template" on github.com/vivarium-collective/viva-template, then clone your new repo.
# Option B: clone the template directly:
git clone https://github.com/vivarium-collective/viva-template my-workspace
cd my-workspace

bash use-this-template-init.sh     # prompts for a workspace name; renders the .j2 files
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"          # this pulls in vivarium-workbench as a dependency
python3 scripts/lint-workspace.py   # expect: "workspace lint: OK"
```

After this you have: `workspace.yaml`, a `pbg_<name>/` Python package, `composites/`, `studies/`, `investigations/`, and a `.pbg/` control dir. The template works standalone — **no plugin or Claude Code required** for the base tool.

> If you're adding the Workbench to an *existing* process-bigraph project instead of scaffolding, just ensure the repo root has a valid `workspace.yaml` and `uv pip install vivarium-workbench` into its venv. (Note: not on PyPI during beta — install editable from a clone of `vivarium-collective/vivarium-workbench` if the PyPI install fails.)

---

## 2. Launch the Workbench
From the workspace root (the dir containing `workspace.yaml`):

```bash
vivarium-workbench serve --workspace .            # picks a free port, renders once, then serves
# or:  vwb serve --workspace . --port 8000 --host 0.0.0.0
```

CLI subcommands (via `vivarium-workbench` or `vwb`): `serve · run · study · investigation · composite · rerun · runs · status · logs`.

On start it writes the live base URL to **`.pbg/server/server-info`** — that file is how *everything* (including agents) discovers the server.

---

## 3. Verify it's up (agent-friendly checks)
```bash
BASE=$(cat .pbg/server/server-info | tr -d '[:space:]')   # e.g. http://127.0.0.1:8765
curl -s "$BASE/api/workspace-manifest" | head              # one-call situational-awareness snapshot
```
Useful endpoints:
- **`GET /api/workspace-manifest`** — one call returns the workspace, composites, studies, registry, health, and skills. *Start here for orientation.*
- **`GET /api/linkage-index`** — the deterministic cross-reference/navigation graph (what links to what).
- **`GET /openapi.json`, `/docs`, `/redoc`** — the live, auto-generated API contract. Prefer `/openapi.json` over prose when you need exact request/response shapes.

---

## 4. The LLM layer — how an AI works with the Workbench

### 4.1 The principle
The Workbench **core is AI-free**; AI is either (a) the **`viva-superpowers`** Claude Code plugin — a set of `viva-*` **skills** that call the Workbench's HTTP API to author and run models — or (b) the Workbench's own **optional built-in chat** (the `[chat]` extra): a provider-agnostic assistant tab where you bring your own provider/model and **every non-GET action needs your approval** (see [ai-chat.md](ai-chat.md)). Both drive the same public HTTP API — neither has a private back door — so the tool stays auditable and the AI swappable. The skills remain the Claude Code path; the built-in chat is for users without it.

### 4.2 One-time agent setup
```
1. Install the `viva-superpowers` plugin in Claude Code.
2. Run  /viva-init   — symlinks every viva-* skill into ~/.claude/skills/ (once per machine).
3. Run  /viva-server start  — launches the Workbench and writes .pbg/server/server-info.
   (Skills read that file for the base URL; if it's absent they'll tell you to start the server.)
```

### 4.3 How an agent talks to the Workbench (the access contract)
- **Base URL:** always read from `.pbg/server/server-info`. Never hardcode a port.
- **No token / no auth to fight:** the CSRF guard is **same-origin**, and a request with **no `Origin` header is always allowed** — so `curl`/`httpx`/CLI calls just work. (`X-VW-Session` is workspace routing, not auth.)
- **Read the contract, not the prose:** `GET /openapi.json` is authoritative for shapes.
- **Orient in one call:** `GET /api/workspace-manifest` (state) + `GET /api/linkage-index` (graph) before you start editing.
- **Runs are asynchronous:** a run returns a `run_id`; poll its status endpoint until done; the durable artifact is `studies/<slug>/runs.db`. Check the run's *result field*, not just the HTTP status (a failed run can still return 200).
- **Writes land in the workspace files; not every write commits to git.** Some routes commit on the active branch, but many FastAPI write paths defer the commit (e.g. `POST /api/study-create` scaffolds the study and leaves it uncommitted) — check `git status` and commit through the Branch tab / `/api/branch/push`. Built-in-chat mutations are additionally recorded in `.pbg/ai-actions.jsonl`.

### 4.4 The skills (what each is for)
| Skill | Purpose |
|---|---|
| `viva-workspace` | scaffold / set up a workspace |
| `viva-server`, `viva-workbench` | start / serve the Workbench |
| `viva-catalog` | the registry / marketplace — install & list available processes and composites |
| `viva-expert` | **wrap a real simulator** as a process-bigraph `Step`/`Process` (the bridge skill) |
| `viva-study` | author a study (the heaviest authoring skill) |
| `viva-investigation` | author an investigation (a set of studies + a narrative spine) |
| `viva-run` | run a composite / study |
| `viva-explore`, `viva-navigate`, `viva-status` | inspect state-trees, navigate links, check health |
| `viva-viz`, `viva-report` | visualizations and HTML reports |
| `viva-suggest`, `viva-biology-forward`, `viva-cite-bands` | AI-assisted biology, next-step suggestions, citation bands |

### 4.5 The authoring flow (typical agent arc)
```
scaffold/serve workspace  →  viva-catalog (get processes)  →  viva-expert (wrap a new simulator, if needed)
   →  build a composite (compose processes)  →  viva-study (define a study over that composite)
   →  viva-run (execute; poll to completion)  →  viva-viz / viva-report (render)
   →  viva-investigation (organize studies + narrative into a rigorous investigation)
```

### 4.6 The mental model you're operating in
The framework collapses to **one object, one operation, one law**:
- **One object** — a *document*. A composite, a study, an investigation, and a template are the **same kind of thing** (a bigraph document).
- **One operation** — **fill**: substitute values/composites into a document's open **sites** (holes).
- **One law** — **groundness** (`is_ground`): a document runs iff it has no unfilled required sites.

Four features that look like separate machinery are that one idea in different places: optional members, gating on a failed prerequisite, pulling a cached result, and partial-graph triggering.
**Read `doc/architecture.md` in the `process-bigraph` repo** for the full map — especially **§7 (the 8 laws/invariants)** and **§8 (where the seams are)**: e.g. a process's `config` and wires are *values, not schemas*; `SimulationStep`/`CachedResults` are reached by **address string** (so a rename fails at resolution, not import); the legacy `${name}` spec format coexists on purpose.

---

## 5. Quick reference
```bash
# --- one-time, new repo ---
git clone https://github.com/vivarium-collective/viva-template my-workspace && cd my-workspace
bash use-this-template-init.sh
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"
python3 scripts/lint-workspace.py           # -> "workspace lint: OK"

# --- launch ---
vwb serve --workspace .                      # writes .pbg/server/server-info

# --- agent orientation ---
BASE=$(cat .pbg/server/server-info | tr -d '[:space:]')
curl -s "$BASE/api/workspace-manifest"       # state snapshot
curl -s "$BASE/api/linkage-index"            # navigation graph
curl -s "$BASE/openapi.json"                 # exact API shapes

# --- AI layer (Claude Code) ---
# /viva-init         (once per machine: install skills)
# /viva-server start (launch + write server-info)
# /viva-catalog /viva-study /viva-run /viva-expert /viva-investigation ...
```

**Key files:** `workspace.yaml` (workspace root marker) · `.pbg/server/server-info` (live base URL) · `studies/<slug>/runs.db` (durable run artifacts) · `composites/`, `studies/`, `investigations/` (the documents).

**Golden rules for an agent:** read `.pbg/server/server-info` for the URL; orient via `/api/workspace-manifest` first; trust `/openapi.json` for shapes; runs are async (poll, and check the *result*, not just HTTP 200); every write is a git commit.
