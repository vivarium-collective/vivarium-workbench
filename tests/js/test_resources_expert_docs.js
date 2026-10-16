// tests/js/test_resources_expert_docs.js — run with: node tests/js/test_resources_expert_docs.js
// The Resources page (_renderInputs in walkthrough.js, inside the IIFE) renders
// ONE repo-wide resource set — References, Datasets, Expert docs — from the
// global inputs block (the server folds every investigation's declared
// resources into it). There is no separate "this investigation's sources"
// section and no "Load sources into" picker. Each reference shows "Cited by"
// study chips. _renderInputs et al. are not exported, so assert the wiring in
// source (same approach as test_process_card_contract.js's walkthrough checks).
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, '../../vivarium_workbench/static');
const w = fs.readFileSync(path.join(dir, 'walkthrough.js'), 'utf8');

// Unified page: expert docs render from the GLOBAL block (which now folds in
// every investigation's declared expert_docs, server-side).
assert.ok(w.includes('var experts = glob.expert_docs'),
  'Resources page reads expert docs from the global block');
assert.ok(w.includes('_inputsExpertDocsHtml(experts)'),
  'Resources page renders the (merged) expert docs');

// Three repo-wide sections, each with its own "+ Add" upload affordance.
assert.ok(w.includes("_inputsSection('References'"), 'References section present');
assert.ok(w.includes("_inputsSection('Datasets'"), 'Datasets section present');
assert.ok(w.includes("_inputsSection('Expert docs'"), 'Expert docs section present');

// The old split layout is gone: no per-investigation "sources" heading and no
// "Load sources into" investigation picker.
assert.ok(!w.includes('This investigation’s sources'),
  'per-investigation sources heading removed');
assert.ok(!w.includes('Load sources into:'),
  'investigation picker removed');

// Each reference is labeled with the studies that cite it, linking to the study
// page (snapshot-safe via _studyHref).
assert.ok(w.includes('function _inputsCitedByHtml'),
  'cited-by renderer present');
assert.ok(w.includes('_inputsCitedByHtml(ref.cited_by)'),
  'reference card renders its cited_by chips');
assert.ok(/_studyHref\(slug\)/.test(w),
  'cited-by chips link to the study page via _studyHref');

// The expert-docs renderer still surfaces the description (source dossiers).
assert.ok(w.includes('doc.description'),
  '_inputsExpertDocsHtml renders a description when present');

console.log('test_resources_expert_docs OK');
