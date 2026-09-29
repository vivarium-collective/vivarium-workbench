// Helper for tests/test_ai_chat.py (NOT a standalone test — no `test_` prefix, so
// scripts/run_js_tests.sh skips it). Feeds NDJSON captured from the REAL
// POST /api/chat/turn into the REAL static/chat-core.js reducer, in awkward
// chunk sizes, and prints the resulting state as JSON.
//   node contract_chat_core.js <ndjson-file> <chunk-size> [<prior-state-json-file>] [<resume-decisions-json>]
const fs = require('fs');
const C = require('../../vivarium_workbench/static/chat-core.js');
const [file, chunk, priorFile] = [process.argv[2], parseInt(process.argv[3], 10), process.argv[4]];
const st = priorFile ? C.restore(JSON.parse(fs.readFileSync(priorFile, 'utf8'))) : C.newState();
if (priorFile) C.startResume(st); else C.startUserTurn(st, 'contract');
const text = fs.readFileSync(file, 'utf8');
const sp = C.createSplitter();
for (let i = 0; i < text.length; i += chunk) sp.push && sp.push(text.slice(i, i + chunk)).forEach(f => C.applyFrame(st, f));
sp.flush().forEach(f => C.applyFrame(st, f));
process.stdout.write(JSON.stringify(C.snapshot(st)) + '\n');
