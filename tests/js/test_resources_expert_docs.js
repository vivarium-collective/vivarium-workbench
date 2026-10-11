// tests/js/test_resources_expert_docs.js — run with: node tests/js/test_resources_expert_docs.js
// The Resources page (_renderInputs in walkthrough.js, inside the IIFE) now
// renders a repo-wide "Expert docs" block from the global inputs, beside the
// repo-wide datasets + references. _renderInputs / _inputsExpertDocsHtml are not
// exported, so assert the wiring in source (same approach as
// test_process_card_contract.js's walkthrough checks).
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, '../../vivarium_workbench/static');
const w = fs.readFileSync(path.join(dir, 'walkthrough.js'), 'utf8');

// The repo-wide section renders expert docs from the GLOBAL block (workspace-
// level expert_docs), not only from a selected investigation.
assert.ok(w.includes('Repo-wide expert docs'), 'repo-wide expert-docs header present');
assert.ok(w.includes('_inputsExpertDocsHtml(glob.expert_docs)'),
  'repo-wide block renders glob.expert_docs');
// Guarded so a workspace with no expert docs shows no empty section.
assert.ok(w.includes('glob.expert_docs && glob.expert_docs.length'),
  'repo-wide expert-docs block is length-guarded');
// It sits in the repo-wide panel, i.e. after the repo-wide references line.
assert.ok(
  w.indexOf('_inputsExpertDocsHtml(glob.expert_docs)') >
    w.indexOf('_inputsRefsHtml(glob.references)'),
  'repo-wide expert docs render after repo-wide references');

// The expert-docs renderer surfaces the description (useful for source dossiers).
assert.ok(w.includes('doc.description'),
  '_inputsExpertDocsHtml renders a description when present');

console.log('test_resources_expert_docs OK');
