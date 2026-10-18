// tests/js/test_study_contract_display.js — run: node tests/js/test_study_contract_display.js
// The study page renders a Design-phase study's PLANNED design across the right
// tabs (Model: contract + mechanisms + methods — the single home for the typed
// interface; Overview: expert_questions), and the investigation page renders
// competing_frameworks. The render fns live inside IIFEs (not exported), so
// assert the wiring in source — same approach as test_process_card_contract.js.
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, '../../vivarium_workbench/static');
const sd = fs.readFileSync(path.join(dir, 'study-detail.js'), 'utf8');
const wt = fs.readFileSync(path.join(dir, 'walkthrough.js'), 'utf8');

// --- study page: planning helpers exist ---
assert.ok(sd.includes('function _pdContractHtml'), 'typed-contract renderer present');
assert.ok(sd.includes("s._units") && sd.includes('s._type'), 'contract table renders type + units');
assert.ok(sd.includes('function _pdBounds'), 'contract table renders bounds (min..max)');

// --- MODEL tab: contract + mechanisms + methods ---
assert.ok(sd.includes("'model-planned-design'"), 'Model tab has a planned-design slot');
assert.ok(sd.indexOf("_pdContractHtml(_pdSpec.contract") !== -1, 'Model tab renders spec.contract');
assert.ok(sd.includes("_pdListHtml('Proposed mechanisms', _pdSpec.proposed_mechanisms)"), 'Model tab renders proposed_mechanisms');
assert.ok(sd.includes("_pdListHtml('Proposed models & methods', _pdSpec.proposed_models_methods)"), 'Model tab renders proposed_models_methods');
// It must inject into panel-compose BEFORE the "no composite" early return, so
// a composite-less Design study still shows the planned model.
assert.ok(sd.indexOf("'model-planned-design'") < sd.indexOf('No composite declared for this study yet'),
  'planned design renders even when no composite is declared');

// --- AUDIT tab: the contract table was removed (it duplicated Model's and now
//     lives in Model only). ---
assert.ok(!sd.includes('_loadDeclaredContract(spec);'), '_loadAudit no longer renders the duplicate contract');
assert.ok(!sd.includes("'audit-declared-contract'"), 'Audit contract slot removed');

// --- OVERVIEW tab: expert_questions ---
assert.ok(sd.includes('function _renderPlanningOverview'), 'Overview expert-questions renderer present');
assert.ok(sd.includes('_renderPlanningOverview();'), '_runStudyInit calls _renderPlanningOverview');
assert.ok(sd.includes("'overview-expert-questions'"), 'Overview has an expert-questions slot');
assert.ok(sd.includes('Open questions / decisions'), 'Overview labels the expert-questions block');

// --- investigation page: competing_frameworks folded into the evidence tab ---
assert.ok(wt.includes('d.competing_frameworks'), 'investigation opening reads competing_frameworks');
assert.ok(wt.includes('inv-brief-frameworks-fold'), 'competing_frameworks folded into the Hypothesis & evidence tab (no standalone tab)');
assert.ok(!wt.includes("label:'Competing frameworks'"), 'competing_frameworks is no longer a standalone brief tab');

console.log('test_study_contract_display OK');
