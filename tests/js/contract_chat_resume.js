// Helper for tests/test_ai_chat.py (NOT a standalone test). Loads the client state a
// real turn produced, answers every pending approval with the REAL chat-core
// decide()/buildResumeRequest(), and prints the request body the browser would send.
//   node contract_chat_resume.js <state.json> <approve|deny>
const fs = require('fs');
const C = require('../../vivarium_workbench/static/chat-core.js');
const st = C.restore(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')));
const approve = process.argv[3] === 'approve';
st.pending.slice().forEach(id => C.decide(st, id, approve));
process.stdout.write(JSON.stringify({ body: C.buildResumeRequest(st), pendingLeft: st.pending.length }) + '\n');
