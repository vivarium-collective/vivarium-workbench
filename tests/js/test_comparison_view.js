// tests/js/test_comparison_view.js — run with: node tests/js/test_comparison_view.js
// Pure builders of static/comparison-view.js (the Results-tab cross-engine view).
const assert = require('assert');
const CV = require('../../vivarium_workbench/static/comparison-view.js');

const job = (model, over) => Object.assign({
  model, job: 'auto_ten_seconds', engines: ['copasi', 'tellurium'],
  matrix: { copasi: { tellurium: 0.074 }, tellurium: { copasi: 0.074 } },
  max_nrmse: 0.074, worst_pair: ['copasi', 'tellurium'],
  bucket: 'ok', bucket_label: 'OK (≤5%)', runs: {},
}, over || {});

// heat levels follow the documented thresholds; non-finite never gets a color
assert.deepStrictEqual([0, 0.001, 0.0011, 0.01, 0.05, 0.1, 0.11].map(CV.heatLevel), [0, 0, 1, 1, 2, 3, 4]);
assert.strictEqual(CV.heatLevel(null), -1);
assert.strictEqual(CV.heatLevel(NaN), -1);
assert.strictEqual(CV.heatLevel(undefined), -1);

// model names are server data: never markup (XSS)
const evil = '<img src=x onerror=alert(1)>"\'';
const rows = CV.rowHtml(job(evil, { bucket_label: evil, worst_pair: [evil, 'b'] }), 0);
assert.ok(!/<img/i.test(rows), 'raw tag leaked into row html');
assert.ok(rows.includes('&lt;img'), 'model name is escaped');
const map = CV.heatmapHtml(job('m', { engines: [evil, 'b'], matrix: { [evil]: { b: 1 }, b: { [evil]: 1 } } }));
assert.ok(!/<img/i.test(map), 'raw tag leaked into heatmap html');
const runs = CV.runsHtml(job('m', { runs: { x: { status: evil, error: evil, runtime_s: 1, n_points: 3 } } }));
assert.ok(!/<img/i.test(runs), 'raw tag leaked into runs html');

// heatmap: diagonal is a dash, null/NaN cells are "—" with the no-value class, values are printed
const h = CV.heatmapHtml(job('m', { matrix: { copasi: { tellurium: null }, tellurium: { copasi: 0.074 } } }));
assert.strictEqual((h.match(/class="cv-self"/g) || []).length, 2, 'two diagonal cells');
assert.ok(h.includes('class="cv-na"') && h.includes('cv-h3') && h.includes('0.074'));
assert.strictEqual(CV.heatmapHtml(job('m', { engines: [], matrix: {} })), '', 'nothing to draw -> empty, not a broken table');

// engine problems come from run status, not from the matrix
const ran = job('r', { runs: { copasi: { status: 'ok' }, pysces: { status: 'unavailable' }, amici: { status: 'error' } } });
assert.deepStrictEqual(CV.problemEngines(ran).sort(), ['amici', 'pysces']);

// filtering: text over model/job/engine, bucket label exact, problems toggle, all combine
const jobs = [job('BIOMD0000000001'), job('BIOMD0000000002', { bucket_label: 'Good (≤1%)' }), ran];
assert.strictEqual(CV.filterJobs(jobs, {}).length, 3);
assert.deepStrictEqual(CV.filterJobs(jobs, { q: ' biomd0000000002 ' }).map((j) => j.model), ['BIOMD0000000002']);
assert.strictEqual(CV.filterJobs(jobs, { q: 'tellurium' }).length, 3, 'matches engine names');
assert.deepStrictEqual(CV.filterJobs(jobs, { bucket: 'Good (≤1%)' }).map((j) => j.model), ['BIOMD0000000002']);
assert.deepStrictEqual(CV.filterJobs(jobs, { problems: true }).map((j) => j.model), ['r']);
assert.strictEqual(CV.filterJobs(jobs, { q: 'zzz' }).length, 0);
assert.deepStrictEqual(CV.filterJobs(null, {}), []);

// unclassified jobs are still reachable through the bucket filter
assert.strictEqual(CV.filterJobs([job('u', { bucket_label: null, bucket: null })], { bucket: 'unclassified' }).length, 1);

console.log('ok');
