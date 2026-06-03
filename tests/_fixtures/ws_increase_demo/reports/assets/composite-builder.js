/* composite-builder.js — UI for authoring *.composite.yaml files.
 *
 * Layout: three panes (palette | canvas | inspector). Cytoscape.js renders
 * the canvas; the source-of-truth `state.model` is the YAML-shaped composite
 * document. Edits flow:
 *
 *   user gesture → mutate state.model
 *                → re-render the cytoscape view (apply diff)
 *                → re-render the inspector
 *                → debounced POST /api/composite/create (autosave)
 *
 * Save → POST /api/composite/draft/<id>/promote → publishes to
 *   <pkg>/composites/<name>.composite.yaml.
 * Commit → POST /api/composite/commit → stages + commits on workstream.
 *
 * Vendored Cytoscape v3.31.x is loaded as a global `cytoscape` by the
 * builder template's <script src="/static/vendor/cytoscape.min.js"> tag.
 * This file expects `window.cytoscape` to exist.
 */

(function () {
  'use strict';

  // ----- state -----
  const state = {
    model: {
      name: '',
      description: '',
      requires: { processes: [] },
      parameters: {},
      state: {},   // node-id -> node-doc
    },
    draftId: null,
    selected: null,
    validation: { ok: true, errors: [], warnings: [], skipped: true },
    softIssues: [],
    schemaCache: new Map(),  // address -> {inputs, outputs, config_schema}
    cy: null,
    autosaveTimer: null,
    palette: [],
  };

  // ----- DOM refs (resolved after DOMContentLoaded) -----
  const dom = {};

  // ----- port handle constants -----
  const PORT_H   = 18;   // vertical px between port handles
  const PORT_PAD = 12;   // px above first / below last handle
  const SNAP_R   = 14;   // snap-to-target radius in px

  // ----- edge drag state -----
  const _drag = {
    active: false,
    srcNode: null, srcDir: null, srcPort: null,
    x0: 0, y0: 0,
  };

  // ----- utilities -----
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v !== null && v !== undefined) {
        node.setAttribute(k, v);
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function uniqueNodeId(prefix) {
    let i = 1;
    while (state.model.state[`${prefix}_${i}`] !== undefined) i += 1;
    return `${prefix}_${i}`;
  }

  function toast(msg, kind) {
    const box = el('div', {
      class: `cb-toast cb-toast-${kind || 'info'}`,
    }, msg);
    document.body.appendChild(box);
    setTimeout(() => { box.style.opacity = '0'; }, 3000);
    setTimeout(() => { box.remove(); }, 3500);
  }

  // ----- HTTP helpers -----
  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let body = '';
      try { body = await r.text(); } catch (_) { /* ignore */ }
      throw new Error(`GET ${url} → ${r.status} ${body.slice(0, 200)}`);
    }
    return r.json();
  }
  async function jpost(url, body, method) {
    const r = await fetch(url, {
      method: method || 'POST',
      headers: { 'content-type': 'application/json' },
      body: body === undefined ? '' : JSON.stringify(body),
    });
    let json = null;
    try { json = await r.json(); } catch (_) { /* ignore */ }
    if (!r.ok) {
      const err = (json && (json.error || json.detail)) || `HTTP ${r.status}`;
      const e = new Error(err);
      e.status = r.status;
      e.body = json;
      throw e;
    }
    return json;
  }

  // ============================================================
  // Palette
  // ============================================================
  async function loadPalette(forceRefresh) {
    dom.paletteList.textContent = 'Loading…';
    const url = forceRefresh ? '/api/registry?refresh=1' : '/api/registry';
    try {
      const data = await jget(url);
      if (data.error) {
        dom.paletteList.textContent = '';
        dom.paletteList.appendChild(el('div', { class: 'cb-error' },
          `Registry error: ${data.error}`));
        return;
      }
      const procs = (data.processes || []).filter(
        (p) => p.kind === 'process' || p.kind === 'step',
      );
      state.palette = procs;
      renderPalette(procs, '');
    } catch (e) {
      dom.paletteList.textContent = '';
      dom.paletteList.appendChild(el('div', { class: 'cb-error' },
        `Registry unavailable: ${e.message}`));
    }
  }

  function renderPalette(procs, filter) {
    dom.paletteList.textContent = '';
    const groups = { in_workspace: [], framework: [], environment_only: [] };
    const needle = (filter || '').toLowerCase();
    for (const p of procs) {
      if (needle && !p.name.toLowerCase().includes(needle)
          && !(p.address || '').toLowerCase().includes(needle)) continue;
      const grp = groups[p.source] || groups.environment_only;
      grp.push(p);
    }
    const sectionTitles = {
      in_workspace: 'Workspace',
      framework: 'Framework',
      environment_only: 'Environment',
    };
    for (const [key, items] of Object.entries(groups)) {
      if (!items.length) continue;
      const section = el('div', { class: 'cb-palette-section' });
      section.appendChild(el('h4', { class: 'cb-palette-h' }, sectionTitles[key]));
      for (const p of items) {
        const card = el('div', {
          class: `cb-palette-card cb-palette-${p.kind}`,
          draggable: 'true',
          title: p.address,
          dataset: { address: p.address, name: p.name, kind: p.kind },
          ondblclick: () => addProcessFromPalette(p),
          ondragstart: (ev) => {
            ev.dataTransfer.setData(
              'application/x-cb-process',
              JSON.stringify({ address: p.address, name: p.name, kind: p.kind }),
            );
            ev.dataTransfer.effectAllowed = 'copy';
          },
        }, p.name);
        if ((p.aliases || []).length) {
          card.appendChild(el('span', { class: 'cb-palette-aliases' },
            ` (${p.aliases.slice(0, 2).join(', ')})`));
        }
        section.appendChild(card);
      }
      dom.paletteList.appendChild(section);
    }
    if (!dom.paletteList.children.length) {
      const msg = filter
        ? `No matches for "${filter}".`
        : 'No processes found in registry.';
      dom.paletteList.appendChild(el('div', { class: 'cb-empty' }, msg));
    }
  }

  async function addProcessFromPalette(palItem, position) {
    const nodeId = uniqueNodeId((palItem.kind === 'step' ? 'step' : 'proc'));
    const schema = await loadProcessSchema(palItem.address);
    const cfg = {};
    if (schema && schema.config_schema && typeof schema.config_schema === 'object') {
      for (const k of Object.keys(schema.config_schema)) {
        cfg[k] = schema.config_schema[k]?.default ?? null;
      }
    }
    state.model.state[nodeId] = {
      _type: palItem.kind === 'step' ? 'step' : 'process',
      address: palItem.address,
      config: cfg,
      inputs: {},
      outputs: {},
    };
    if (palItem.kind === 'process') {
      state.model.state[nodeId].interval = 1.0;
    }
    addProcessToRequires(palItem.name);
    syncCytoscapeFromModel();
    selectNode(nodeId, position);
    autosave();
  }

  function addProcessToRequires(name) {
    state.model.requires = state.model.requires || { processes: [] };
    state.model.requires.processes = state.model.requires.processes || [];
    if (!state.model.requires.processes.includes(name)) {
      state.model.requires.processes.push(name);
    }
  }

  async function loadProcessSchema(address) {
    if (state.schemaCache.has(address)) return state.schemaCache.get(address);
    const url = `/api/process/${encodeURIComponent(address)}/schema`;
    try {
      const data = await jget(url);
      state.schemaCache.set(address, data);
      return data;
    } catch (e) {
      console.warn('process schema fetch failed:', e.message);
      const fallback = { inputs: [], outputs: [], config_schema: null,
        error: e.message };
      state.schemaCache.set(address, fallback);
      return fallback;
    }
  }

  // ============================================================
  // Cytoscape view <-> model sync
  // ============================================================
  function buildCytoscape() {
    state.cy = cytoscape({
      container: dom.canvas,
      elements: [],
      layout: { name: 'preset' },
      style: [
        {
          selector: 'node',
          style: {
            'label': 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': 11,
            'color': '#e6e6e6',
            'text-outline-color': '#1d2129',
            'text-outline-width': 2,
            'background-color': '#3b8eea',
            'border-width': 1,
            'border-color': '#0a1a2b',
            'width': 'label',
            'height': 'data(nodeHeight)',
            'padding': '12px',
            'shape': 'round-rectangle',
          },
        },
        {
          selector: 'node[kind="store"]',
          style: {
            'background-color': '#6c8',
            'shape': 'ellipse',
            'width': 'label',
            'height': 36,
            'padding': '8px',
          },
        },
        {
          selector: 'node[kind="step"]',
          style: {
            'background-color': '#b387d1',
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-color': '#fff',
            'border-width': 3,
          },
        },
        {
          selector: 'edge',
          style: {
            'curve-style': 'bezier',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 1.2,
            'width': 1.6,
            'line-color': '#aab',
            'target-arrow-color': '#aab',
            'label': 'data(label)',
            'font-size': 9,
            'color': '#aab',
            'text-rotation': 'autorotate',
            'text-margin-y': -6,
          },
        },
        {
          selector: 'edge[direction="output"]',
          style: {
            'line-color': '#ffaa64',
            'target-arrow-color': '#ffaa64',
            'color': '#ffaa64',
          },
        },
      ],
      wheelSensitivity: 0.2,
    });

    state.cy.on('tap', 'node', (ev) => {
      const id = ev.target.id();
      if (_drag.active && nodeKind(state.model.state[id]) === 'store') {
        commitEdge(
          { node: _drag.srcNode, dir: _drag.srcDir, port: _drag.srcPort },
          { node: id, dir: 'store', port: '' },
        );
        cancelEdgeDrag();
        return;
      }
      selectNode(id);
    });
    state.cy.on('tap', (ev) => {
      if (ev.target === state.cy) {
        if (_drag.active) { cancelEdgeDrag(); return; }
        selectNode(null);
      }
    });
    state.cy.on('cxttap', 'node', (ev) => {
      // Right-click: delete node.
      const id = ev.target.id();
      if (window.confirm(`Delete node '${id}'?`)) {
        deleteNode(id);
      }
    });

    // Reposition port handles on pan / zoom / node move / graph change.
    state.cy.on('viewport', renderPortHandles);
    state.cy.on('position', 'node', renderPortHandles);
    state.cy.on('add remove', 'node', renderPortHandles);

    // Drag-drop onto canvas → spawn from palette payload.
    dom.canvas.addEventListener('dragover', (ev) => {
      if ((ev.dataTransfer.types || []).includes('application/x-cb-process')) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
      }
    });
    dom.canvas.addEventListener('drop', (ev) => {
      const raw = ev.dataTransfer.getData('application/x-cb-process');
      if (!raw) return;
      ev.preventDefault();
      const payload = JSON.parse(raw);
      const rect = dom.canvas.getBoundingClientRect();
      const pan = state.cy.pan();
      const zoom = state.cy.zoom();
      const pos = {
        x: (ev.clientX - rect.left - pan.x) / zoom,
        y: (ev.clientY - rect.top - pan.y) / zoom,
      };
      addProcessFromPalette(payload, pos);
    });
  }

  function syncCytoscapeFromModel() {
    if (!state.cy) return;
    const want = new Map(); // id -> {data, position?}
    const wantEdges = [];

    for (const [id, node] of Object.entries(state.model.state || {})) {
      const kind = nodeKind(node);
      const _sch  = (kind !== 'store') ? (state.schemaCache.get(node?.address) || {}) : {};
      const _nIn  = Math.max((_sch.inputs  || []).length, Object.keys(node?.inputs  || {}).length);
      const _nOut = Math.max((_sch.outputs || []).length, Object.keys(node?.outputs || {}).length);
      const _pc   = (kind === 'store') ? 0 : Math.max(_nIn, _nOut, 1);
      const _nh   = Math.max(36, _pc * PORT_H + 2 * PORT_PAD);
      want.set(id, {
        data: { id, label: id, kind, address: node?.address || '', nodeHeight: _nh },
      });
      if (kind === 'process' || kind === 'step') {
        for (const [port, target] of Object.entries(node.inputs || {})) {
          const targetId = ensureStoreFromPath(target);
          if (targetId) {
            wantEdges.push({
              data: {
                id: `e:${id}:in:${port}`,
                source: targetId,
                target: id,
                label: port,
                direction: 'input',
                port,
              },
            });
          }
        }
        for (const [port, target] of Object.entries(node.outputs || {})) {
          const targetId = ensureStoreFromPath(target);
          if (targetId) {
            wantEdges.push({
              data: {
                id: `e:${id}:out:${port}`,
                source: id,
                target: targetId,
                label: port,
                direction: 'output',
                port,
              },
            });
          }
        }
      }
    }

    // Add/update nodes.
    for (const [id, spec] of want.entries()) {
      const existing = state.cy.getElementById(id);
      if (existing && existing.nonempty()) {
        existing.data(spec.data);
      } else {
        const pos = spec.position || randomPosition();
        state.cy.add({ group: 'nodes', data: spec.data, position: pos });
      }
    }
    // Remove vanished nodes.
    state.cy.nodes().forEach((n) => {
      if (!want.has(n.id())) n.remove();
    });

    // Edges: clear all and re-add (simpler than diffing).
    state.cy.edges().remove();
    for (const e of wantEdges) {
      if (state.cy.getElementById(e.data.source).nonempty()
          && state.cy.getElementById(e.data.target).nonempty()) {
        state.cy.add({ group: 'edges', data: e.data });
      }
    }

    // Defer to next animation frame so Cytoscape has rendered the new node
    // positions before we query renderedBoundingBox().
    requestAnimationFrame(() => requestAnimationFrame(renderPortHandles));
  }

  // ============================================================
  // Port handles (ReactFlow-style interactive knobs)
  // ============================================================

  function uniqPorts(schemaPorts, wires) {
    const names = [], seen = new Set();
    for (const p of schemaPorts) {
      const n = typeof p === 'string' ? p : (p && p.name);
      if (n && !seen.has(n)) { seen.add(n); names.push(n); }
    }
    for (const n of Object.keys(wires)) {
      if (!seen.has(n)) { seen.add(n); names.push(n); }
    }
    return names;
  }

  function renderPortHandles() {
    if (!state.cy || !dom.portOverlay || _drag.active) return;
    dom.portOverlay.innerHTML = '';
    state.cy.nodes().forEach((cyNode) => {
      const id   = cyNode.id();
      const node = state.model.state[id];
      if (!node) return;
      const bb  = cyNode.renderedBoundingBox();
      const mcy = (bb.y1 + bb.y2) / 2;

      if (nodeKind(node) === 'store') {
        const h = el('div', {
          class: 'cb-port-handle cb-port-handle-store',
          dataset: { node: id, dir: 'store', port: '' },
          title: `store: ${id}`,
          style: `left:${bb.x2}px;top:${mcy}px`,
          onmousedown: (ev) => { ev.stopPropagation(); startEdgeDrag(ev, id, 'store', '', bb.x2, mcy); },
        });
        dom.portOverlay.appendChild(h);
        return;
      }

      const schema = state.schemaCache.get(node.address) || {};
      const ports = {
        inputs:  uniqPorts(schema.inputs  || [], node.inputs  || {}),
        outputs: uniqPorts(schema.outputs || [], node.outputs || {}),
      };
      for (const [dir, list] of Object.entries(ports)) {
        const xEdge = dir === 'inputs' ? bb.x1 : bb.x2;
        list.forEach((portName, i) => {
          const py = bb.y1 + PORT_PAD + i * PORT_H + PORT_H / 2;
          const h = el('div', {
            class: 'cb-port-handle',
            dataset: { node: id, dir, port: portName },
            title: `${dir === 'inputs' ? '→ in' : 'out →'}: ${portName}`,
            style: `left:${xEdge}px;top:${py}px`,
            onmousedown: (ev) => { ev.stopPropagation(); startEdgeDrag(ev, id, dir, portName, xEdge, py); },
          });
          const lbl = el('span', {
            class: `cb-port-label cb-port-label-${dir}`,
            style: dir === 'inputs'
              ? `left:${xEdge + 9}px;top:${py}px`
              : `right:${(dom.portOverlay.offsetWidth || dom.canvas.offsetWidth) - xEdge + 9}px;top:${py}px`,
          }, portName);
          dom.portOverlay.appendChild(h);
          dom.portOverlay.appendChild(lbl);
        });
      }
    });
  }

  function isValidTarget(dataset) {
    if (!_drag.active) return false;
    if (dataset.node === _drag.srcNode) return false;
    const srcIsStore = _drag.srcDir === 'store';
    const tgtIsStore = dataset.dir === 'store';
    if (srcIsStore && tgtIsStore) return false;
    if (!srcIsStore && !tgtIsStore && _drag.srcDir === dataset.dir) return false;
    return true;
  }

  function startEdgeDrag(ev, nodeId, dir, port, x0, y0) {
    _drag.active  = true;
    _drag.srcNode = nodeId; _drag.srcDir = dir; _drag.srcPort = port;
    _drag.x0 = x0; _drag.y0 = y0;
    const rect = dom.canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    dom.ghostLine.setAttribute('x1', x0); dom.ghostLine.setAttribute('y1', y0);
    dom.ghostLine.setAttribute('x2', mx);  dom.ghostLine.setAttribute('y2', my);
    dom.ghostLine.style.display = '';
    dom.portOverlay.querySelectorAll('.cb-port-handle').forEach((h) => {
      if (isValidTarget(h.dataset)) h.classList.add('cb-port-valid');
    });
    document.addEventListener('mousemove', _onDragMove);
    document.addEventListener('mouseup',   _onDragUp);
  }

  function _onDragMove(ev) {
    if (!_drag.active) return;
    const rect = dom.canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    let ex = mx, ey = my;
    dom.portOverlay.querySelectorAll('.cb-port-handle').forEach((h) => {
      if (!isValidTarget(h.dataset)) return;
      const hr = h.getBoundingClientRect();
      const hx = hr.left + hr.width  / 2 - rect.left;
      const hy = hr.top  + hr.height / 2 - rect.top;
      if (Math.hypot(mx - hx, my - hy) < SNAP_R) { ex = hx; ey = hy; }
    });
    dom.ghostLine.setAttribute('x2', ex);
    dom.ghostLine.setAttribute('y2', ey);
  }

  function _onDragUp(ev) {
    document.removeEventListener('mousemove', _onDragMove);
    document.removeEventListener('mouseup',   _onDragUp);
    if (!dom.portOverlay) { cancelEdgeDrag(); return; }
    const rect = dom.canvas.getBoundingClientRect();
    let best = null, bestDist = SNAP_R;
    dom.portOverlay.querySelectorAll('.cb-port-handle').forEach((h) => {
      if (!isValidTarget(h.dataset)) return;
      const hr = h.getBoundingClientRect();
      const d  = Math.hypot(ev.clientX - (hr.left + hr.width / 2),
                             ev.clientY - (hr.top  + hr.height / 2));
      if (d < bestDist) { bestDist = d; best = h; }
    });
    if (best) {
      commitEdge(
        { node: _drag.srcNode, dir: _drag.srcDir, port: _drag.srcPort },
        best.dataset,
      );
    }
    cancelEdgeDrag();
  }

  function cancelEdgeDrag() {
    _drag.active = false;
    _drag.srcNode = _drag.srcDir = _drag.srcPort = null;
    if (dom.ghostLine) dom.ghostLine.style.display = 'none';
    if (dom.portOverlay) {
      dom.portOverlay.querySelectorAll('.cb-port-valid')
        .forEach((h) => h.classList.remove('cb-port-valid'));
    }
  }

  function commitEdge(src, tgt) {
    if (src.node === tgt.node) return;
    const srcIsStore = src.dir === 'store';
    const tgtIsStore = tgt.dir === 'store';
    if (srcIsStore && tgtIsStore) return;
    if (!srcIsStore && !tgtIsStore) {
      if (src.dir === tgt.dir) return;
      const [outNode, outPort, inNode, inPort] = src.dir === 'outputs'
        ? [src.node, src.port, tgt.node, tgt.port]
        : [tgt.node, tgt.port, src.node, src.port];
      const storeId = uniqueNodeId('store');
      setWire(outNode, 'outputs', outPort, storeId);
      setWire(inNode,  'inputs',  inPort,  storeId);
    } else {
      const [procInfo, storeId] = srcIsStore
        ? [tgt, src.node]
        : [src, tgt.node];
      setWire(procInfo.node, procInfo.dir, procInfo.port, storeId);
    }
    renderInspector();
  }

  function nodeKind(node) {
    if (!node || typeof node !== 'object') return 'store';
    if (node._type === 'step') return 'step';
    if (node._type === 'process') return 'process';
    return 'store';
  }

  function randomPosition() {
    const w = dom.canvas.clientWidth || 600;
    const h = dom.canvas.clientHeight || 400;
    return { x: 80 + Math.random() * (w - 160), y: 80 + Math.random() * (h - 160) };
  }

  // ----- store path helpers -----
  function ensureStoreFromPath(target) {
    // Accepts ["stores", "level"] or "level" or a node-id string. Returns a
    // node id that lives in state.model.state. Creates the store entry on
    // the fly if missing — matches loom's "store-in-the-middle" convention.
    let id;
    if (Array.isArray(target)) {
      id = target.join('.');
    } else if (typeof target === 'string') {
      id = target;
    } else {
      return null;
    }
    if (state.model.state[id] === undefined) {
      // Auto-create a leaf store with a null default.
      state.model.state[id] = null;
    }
    return id;
  }

  function storePathFor(nodeId) {
    // Express a store node id as a dotted path. v1: id itself, sans 'stores.'
    // unless it's already qualified. The Composite framework accepts both.
    if (nodeId.includes('.')) return nodeId.split('.');
    return [nodeId];
  }

  // ============================================================
  // Inspector
  // ============================================================
  function selectNode(nodeId, position) {
    state.selected = nodeId;
    if (state.cy) {
      state.cy.nodes().unselect();
      if (nodeId) {
        const n = state.cy.getElementById(nodeId);
        if (n.nonempty()) {
          n.select();
          if (position) n.position(position);
        }
      }
    }
    renderInspector();
  }

  function renderInspector() {
    dom.inspector.textContent = '';
    if (!state.selected) {
      dom.inspector.appendChild(el('div', { class: 'cb-empty' },
        'Select a node to edit. Drag from the palette to add a process.'));
      renderTopMeta();
      return;
    }
    const id = state.selected;
    const node = state.model.state[id];
    const kind = nodeKind(node);
    const wrap = el('div', { class: 'cb-inspector-body' });
    wrap.appendChild(el('h3', { class: 'cb-inspector-h' }, `${kind}: ${id}`));

    // Rename row.
    const renameInput = el('input', {
      type: 'text', value: id, class: 'cb-input',
      onblur: (ev) => renameNode(id, ev.target.value),
    });
    wrap.appendChild(label('id', renameInput));

    if (kind === 'process' || kind === 'step') {
      wrap.appendChild(renderProcessInspector(id, node));
    } else {
      wrap.appendChild(renderStoreInspector(id, node));
    }

    // Delete button.
    wrap.appendChild(el('button', {
      class: 'cb-btn cb-btn-danger',
      onclick: () => deleteNode(id),
    }, 'Delete node'));

    dom.inspector.appendChild(wrap);
    renderTopMeta();
  }

  function label(text, control) {
    return el('div', { class: 'cb-field' },
      el('label', { class: 'cb-label' }, text),
      control,
    );
  }

  function renderProcessInspector(id, node) {
    const frag = document.createDocumentFragment();
    frag.appendChild(el('div', { class: 'cb-inspector-sub' },
      el('div', { class: 'cb-meta-row' },
        el('span', { class: 'cb-meta-k' }, 'address:'),
        el('span', { class: 'cb-meta-v' }, node?.address || '(missing)'))));

    if (node._type === 'process') {
      const intervalInput = el('input', {
        type: 'number', step: 'any',
        value: String(node.interval ?? 1.0),
        class: 'cb-input',
        onchange: (ev) => {
          const v = parseFloat(ev.target.value);
          if (Number.isFinite(v)) {
            node.interval = v;
            autosave();
          }
        },
      });
      frag.appendChild(label('interval', intervalInput));
    }

    // Schema-driven port lists.
    const schema = state.schemaCache.get(node.address) || { inputs: [], outputs: [] };
    if (schema.error) {
      frag.appendChild(el('div', { class: 'cb-warn' },
        `Ports could not be auto-detected: ${schema.error}`));
    }
    frag.appendChild(renderPorts('inputs', id, node, schema.inputs || []));
    frag.appendChild(renderPorts('outputs', id, node, schema.outputs || []));

    // Config.
    if (schema.config_schema && typeof schema.config_schema === 'object') {
      const cfgWrap = el('div', { class: 'cb-section' },
        el('h4', { class: 'cb-section-h' }, 'config'));
      for (const [k] of Object.entries(schema.config_schema)) {
        const v = node.config?.[k];
        const inp = el('input', {
          type: 'text',
          value: v === undefined || v === null ? '' : String(v),
          class: 'cb-input',
          onchange: (ev) => {
            node.config = node.config || {};
            const raw = ev.target.value;
            node.config[k] = coerceScalar(raw);
            autosave();
          },
        });
        cfgWrap.appendChild(label(k, inp));
      }
      frag.appendChild(cfgWrap);
    }
    return frag;
  }

  function renderPorts(direction, id, node, ports) {
    const wrap = el('div', { class: 'cb-section' },
      el('h4', { class: 'cb-section-h' }, direction));
    const wires = node[direction] || {};
    const knownNames = new Set(ports.map((p) => p.name));
    const all = [...ports.map((p) => p.name), ...Object.keys(wires).filter((n) => !knownNames.has(n))];
    if (!all.length) {
      wrap.appendChild(el('div', { class: 'cb-empty cb-empty-sm' },
        '(no ports detected — manual wiring only)'));
    }
    for (const portName of all) {
      const cur = wires[portName];
      const pathStr = Array.isArray(cur) ? cur.join('.') : (cur === undefined ? '' : String(cur));
      const row = el('div', { class: 'cb-port-row' },
        el('span', { class: 'cb-port-name' }, portName),
        el('input', {
          type: 'text',
          value: pathStr,
          placeholder: 'store-id or dotted.path',
          class: 'cb-input cb-port-input',
          onchange: (ev) => setWire(id, direction, portName, ev.target.value),
        }),
        el('button', {
          class: 'cb-btn cb-btn-sm',
          onclick: () => clearWire(id, direction, portName),
        }, '✕'),
      );
      wrap.appendChild(row);
    }
    // Free-form "+ port" entry for processes whose schema we couldn't read.
    const addRow = el('div', { class: 'cb-port-row' },
      el('input', {
        type: 'text', placeholder: '+ add port', class: 'cb-input',
        onkeydown: (ev) => {
          if (ev.key === 'Enter') {
            const p = ev.target.value.trim();
            if (p && !(node[direction] || {})[p]) {
              node[direction] = node[direction] || {};
              node[direction][p] = '';
              renderInspector();
              autosave();
            }
          }
        },
      }),
    );
    wrap.appendChild(addRow);
    return wrap;
  }

  function setWire(nodeId, direction, port, value) {
    const node = state.model.state[nodeId];
    if (!node) return;
    node[direction] = node[direction] || {};
    if (!value || !value.trim()) {
      delete node[direction][port];
    } else {
      const parts = value.includes('.') ? value.split('.') : [value];
      node[direction][port] = parts.length === 1 ? parts : parts;
      // Auto-create the store target if needed.
      ensureStoreFromPath(parts.length === 1 ? parts[0] : parts);
    }
    syncCytoscapeFromModel();
    autosave();
  }

  function clearWire(nodeId, direction, port) {
    const node = state.model.state[nodeId];
    if (!node || !node[direction]) return;
    delete node[direction][port];
    syncCytoscapeFromModel();
    renderInspector();
    autosave();
  }

  function renderStoreInspector(id, node) {
    const frag = document.createDocumentFragment();
    const valueText = node === null ? '' : (typeof node === 'object'
      ? JSON.stringify(node) : String(node));
    const inp = el('input', {
      type: 'text',
      value: valueText,
      placeholder: 'default value (scalar or JSON literal)',
      class: 'cb-input',
      onchange: (ev) => {
        state.model.state[id] = coerceScalar(ev.target.value);
        autosave();
      },
    });
    frag.appendChild(label('value', inp));
    frag.appendChild(el('div', { class: 'cb-help' },
      'Stores hold state. Numbers / "${param}" placeholders / JSON literals are accepted.'));
    return frag;
  }

  function coerceScalar(raw) {
    if (raw === '' || raw === null || raw === undefined) return null;
    const trimmed = String(raw).trim();
    if (trimmed === 'null') return null;
    if (trimmed === 'true') return true;
    if (trimmed === 'false') return false;
    const num = Number(trimmed);
    if (Number.isFinite(num) && trimmed === String(num)) return num;
    try {
      if (trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.startsWith('"')) {
        return JSON.parse(trimmed);
      }
    } catch (_) { /* fall through to raw string */ }
    return trimmed;
  }

  function renameNode(oldId, newId) {
    newId = String(newId || '').trim();
    if (!newId || newId === oldId) return;
    if (state.model.state[newId] !== undefined) {
      toast(`node '${newId}' already exists`, 'error');
      renderInspector();
      return;
    }
    state.model.state[newId] = state.model.state[oldId];
    delete state.model.state[oldId];
    // Update wires that referred to oldId by exact node-id.
    for (const node of Object.values(state.model.state)) {
      if (!node || typeof node !== 'object') continue;
      for (const dir of ['inputs', 'outputs']) {
        const wires = node[dir];
        if (!wires) continue;
        for (const [port, tgt] of Object.entries(wires)) {
          if (Array.isArray(tgt) && tgt.length === 1 && tgt[0] === oldId) {
            wires[port] = [newId];
          } else if (tgt === oldId) {
            wires[port] = [newId];
          }
        }
      }
    }
    state.selected = newId;
    syncCytoscapeFromModel();
    renderInspector();
    autosave();
  }

  function deleteNode(id) {
    delete state.model.state[id];
    // Also remove any wires that referenced it by exact id.
    for (const node of Object.values(state.model.state)) {
      if (!node || typeof node !== 'object') continue;
      for (const dir of ['inputs', 'outputs']) {
        const wires = node[dir] || {};
        for (const [port, tgt] of Object.entries(wires)) {
          const refs = Array.isArray(tgt) ? tgt : [tgt];
          if (refs.length === 1 && refs[0] === id) delete wires[port];
        }
      }
    }
    if (state.selected === id) state.selected = null;
    syncCytoscapeFromModel();
    renderInspector();
    autosave();
  }

  // ============================================================
  // Top-meta (name, description, parameters)
  // ============================================================
  function renderTopMeta() {
    dom.meta.textContent = '';
    const nameInp = el('input', {
      type: 'text', value: state.model.name || '',
      placeholder: 'composite-name (slug)',
      class: 'cb-input cb-input-wide',
      oninput: (ev) => { state.model.name = ev.target.value.trim(); autosave(); },
    });
    const descInp = el('input', {
      type: 'text', value: state.model.description || '',
      placeholder: 'short description',
      class: 'cb-input cb-input-wide',
      oninput: (ev) => { state.model.description = ev.target.value; autosave(); },
    });
    dom.meta.appendChild(label('name', nameInp));
    dom.meta.appendChild(label('description', descInp));

    // Parameters: simple table with +/-.
    const paramSec = el('div', { class: 'cb-section' },
      el('h4', { class: 'cb-section-h' }, 'parameters'));
    const tbl = el('div', { class: 'cb-params' });
    const params = state.model.parameters || {};
    for (const [pname, pdef] of Object.entries(params)) {
      tbl.appendChild(renderParamRow(pname, pdef));
    }
    const addBtn = el('button', { class: 'cb-btn cb-btn-sm', onclick: () => {
      const nm = window.prompt('parameter name (slug)');
      if (!nm) return;
      state.model.parameters = state.model.parameters || {};
      if (state.model.parameters[nm]) return;
      state.model.parameters[nm] = { type: 'float', default: 0.0 };
      renderTopMeta();
      autosave();
    } }, '+ parameter');
    paramSec.appendChild(tbl);
    paramSec.appendChild(addBtn);
    dom.meta.appendChild(paramSec);
  }

  function renderParamRow(name, pdef) {
    return el('div', { class: 'cb-param-row' },
      el('span', { class: 'cb-param-name' }, name),
      el('select', {
        class: 'cb-input cb-input-sm',
        onchange: (ev) => { pdef.type = ev.target.value; autosave(); },
      },
        ['float', 'int', 'string', 'bool'].map((t) => {
          const opt = el('option', { value: t }, t);
          if ((pdef.type || 'float') === t) opt.setAttribute('selected', 'selected');
          return opt;
        })),
      el('input', {
        type: 'text',
        value: pdef.default === undefined || pdef.default === null
          ? '' : String(pdef.default),
        placeholder: 'default',
        class: 'cb-input cb-input-sm',
        onchange: (ev) => { pdef.default = coerceScalar(ev.target.value); autosave(); },
      }),
      el('button', { class: 'cb-btn cb-btn-sm cb-btn-danger',
        onclick: () => {
          delete state.model.parameters[name];
          renderTopMeta();
          autosave();
        } }, '✕'),
    );
  }

  // ============================================================
  // Soft validation (client-side)
  // ============================================================
  function runSoftChecks() {
    const issues = [];
    if (!state.model.name) {
      issues.push({ kind: 'missing', path: 'name', message: 'name is required' });
    }
    for (const [id, node] of Object.entries(state.model.state)) {
      const k = nodeKind(node);
      if (k === 'process' || k === 'step') {
        if (!node?.address) {
          issues.push({ kind: 'missing', path: `state.${id}.address`,
            message: 'process/step requires an address', node: id });
        }
        // Schema-driven required-input check.
        const schema = state.schemaCache.get(node.address);
        if (schema && schema.inputs) {
          for (const p of schema.inputs) {
            if (!(node.inputs && node.inputs[p.name])) {
              issues.push({ kind: 'unwired', path: `state.${id}.inputs.${p.name}`,
                message: `unwired input port '${p.name}'`, node: id });
            }
          }
        }
      }
    }
    state.softIssues = issues;
    renderIssues();
  }

  function renderIssues() {
    dom.issues.textContent = '';
    const all = [...(state.softIssues || []),
      ...((state.validation && state.validation.errors) || [])];
    if (!all.length) {
      dom.issues.appendChild(el('span', { class: 'cb-ok' }, '✓ no issues'));
      return;
    }
    for (const iss of all.slice(0, 6)) {
      dom.issues.appendChild(el('div', {
        class: 'cb-issue',
        onclick: () => { if (iss.node) selectNode(iss.node); },
      }, `[${iss.kind}] ${iss.path}: ${iss.message}`));
    }
    if (all.length > 6) {
      dom.issues.appendChild(el('div', { class: 'cb-issue cb-issue-more' },
        `… and ${all.length - 6} more`));
    }
  }

  // ============================================================
  // Save / commit lifecycle
  // ============================================================
  function autosave() {
    runSoftChecks();
    clearTimeout(state.autosaveTimer);
    state.autosaveTimer = setTimeout(doAutosave, 1000);
  }

  async function doAutosave() {
    try {
      const resp = await jpost('/api/composite/create', {
        draft_id: state.draftId || undefined,
        draft: state.model,
        skip_validation: true,
      });
      state.draftId = resp.draft_id;
      dom.saveStatus.textContent = 'autosaved';
      setTimeout(() => { dom.saveStatus.textContent = ''; }, 1200);
    } catch (e) {
      dom.saveStatus.textContent = `autosave failed: ${e.message}`;
    }
  }

  async function onSave() {
    dom.saveStatus.textContent = 'saving…';
    try {
      // 1. flush any pending autosave so the server has the latest draft.
      clearTimeout(state.autosaveTimer);
      const create = await jpost('/api/composite/create', {
        draft_id: state.draftId || undefined,
        draft: state.model,
      });
      state.draftId = create.draft_id;
      state.validation = create.validation;
      renderIssues();
      if (!create.validation.ok && !create.validation.skipped) {
        dom.saveStatus.textContent = 'draft saved with validation errors';
        return;
      }
      // 2. promote draft → <pkg>/composites/<name>.composite.yaml.
      const promo = await jpost(`/api/composite/draft/${encodeURIComponent(state.draftId)}/promote`, {
        name: state.model.name,
        overwrite: false,
      });
      state.lastPublishedPath = promo.path;
      state.validation = promo.validation;
      renderIssues();
      dom.saveStatus.textContent = `saved → ${promo.path}`;
      toast('saved to workspace', 'ok');
    } catch (e) {
      dom.saveStatus.textContent = `save failed: ${e.message}`;
      toast(`save failed: ${e.message}`, 'error');
    }
  }

  async function onCommit() {
    if (!state.lastPublishedPath) {
      toast('save before commit', 'error');
      return;
    }
    dom.saveStatus.textContent = 'committing…';
    try {
      const resp = await jpost('/api/composite/commit', {
        path: state.lastPublishedPath,
      });
      dom.saveStatus.textContent = `committed ${resp.commit} on ${resp.branch}`;
      toast(`committed ${resp.commit} on ${resp.branch}`, 'ok');
    } catch (e) {
      dom.saveStatus.textContent = `commit failed: ${e.message}`;
      if (e.status === 409 && e.body && e.body.validation) {
        state.validation = e.body.validation;
        renderIssues();
      }
      toast(`commit failed: ${e.message}`, 'error');
    }
  }

  // ============================================================
  // Boot
  // ============================================================
  function loadDraftFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('draft');
    if (!id) return Promise.resolve(false);
    return jget(`/api/composite/draft/${encodeURIComponent(id)}`)
      .then((data) => {
        state.draftId = id;
        if (data.parsed && typeof data.parsed === 'object') {
          state.model = Object.assign({
            name: '', description: '', requires: { processes: [] },
            parameters: {}, state: {},
          }, data.parsed);
        }
        return true;
      })
      .catch(() => false);
  }

  function bootUI() {
    document.body.classList.add('cb-body');
    const root = $('#composite-builder-root');
    if (!root) {
      console.error('#composite-builder-root not found');
      return;
    }

    dom.palette = el('aside', { class: 'cb-pane cb-palette' });
    dom.canvas = el('div', { id: 'composite-canvas', class: 'cb-canvas' });
    dom.inspector = el('aside', { class: 'cb-pane cb-inspector' });

    // Port handle overlay (positioned absolutely inside the canvas).
    dom.portOverlay = el('div', { id: 'cb-port-overlay' });
    dom.canvas.appendChild(dom.portOverlay);

    // Ghost edge SVG (drawn during drag-to-connect).
    dom.ghostSvg  = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    dom.ghostSvg.id = 'cb-ghost-svg';
    dom.ghostLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    dom.ghostLine.style.display = 'none';
    dom.ghostSvg.appendChild(dom.ghostLine);
    dom.canvas.appendChild(dom.ghostSvg);

    const paletteFilter = el('input', {
      type: 'text', placeholder: 'filter…',
      class: 'cb-input',
      oninput: (ev) => renderPalette(state.palette, ev.target.value),
    });
    dom.paletteList = el('div', { class: 'cb-palette-list' });

    const paletteHeader = el('div', {
      style: 'display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;',
    },
      el('h3', { class: 'cb-pane-h', style: 'margin:0;' }, 'Palette'),
      el('button', {
        class: 'cb-btn cb-btn-sm',
        title: 'Refresh registry',
        onclick: () => loadPalette(true),
      }, '↺'),
    );
    dom.palette.appendChild(paletteHeader);
    dom.palette.appendChild(paletteFilter);
    dom.palette.appendChild(dom.paletteList);
    dom.palette.appendChild(el('button', {
      class: 'cb-btn',
      onclick: () => {
        const id = uniqueNodeId('store');
        state.model.state[id] = null;
        syncCytoscapeFromModel();
        selectNode(id);
        autosave();
      },
    }, '+ store node'));

    dom.meta = el('div', { class: 'cb-meta' });
    const toolbar = el('div', { class: 'cb-toolbar' },
      dom.meta,
      el('div', { class: 'cb-toolbar-actions' },
        el('span', { id: 'cb-save-status', class: 'cb-status' }),
        el('button', { class: 'cb-btn', onclick: () => onSave() }, 'Save'),
        el('button', { class: 'cb-btn cb-btn-primary', onclick: () => onCommit() }, 'Commit'),
      ),
    );
    dom.issues = el('div', { class: 'cb-issues' });

    const center = el('div', { class: 'cb-center' },
      toolbar,
      dom.canvas,
      dom.issues,
    );

    root.appendChild(dom.palette);
    root.appendChild(center);
    root.appendChild(dom.inspector);
    dom.saveStatus = $('#cb-save-status');

    buildCytoscape();
    renderInspector();
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      bootUI();
      await Promise.all([loadPalette(), loadDraftFromQuery()]);
      syncCytoscapeFromModel();
      renderInspector();
    } catch (err) {
      const root = document.getElementById('composite-builder-root');
      if (root) {
        root.innerHTML = '';
        root.appendChild(
          Object.assign(document.createElement('div'), {
            className: 'cb-error',
            style: 'padding:24px;font-size:13px;',
            textContent: `Builder failed to initialise: ${err.message}`,
          }),
        );
      }
      console.error('composite builder boot error', err);
    }
  });
})();
