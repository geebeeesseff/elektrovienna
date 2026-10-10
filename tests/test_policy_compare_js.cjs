// Synthetic DOM only: no actual customer preference or browser download is created.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let handler, blob, clicked = false, revoked = false;
const status = {textContent: ''};
const sections = [null, 'A', 'equal'].map((value, i) => ({
  dataset: {case: `synthetic-${i}`},
  querySelector(selector) {
    return selector === 'input:checked' ? (value ? {value} : null) : {value: i ? 'Test comment' : ''};
  }
}));
const context = {
  document: {
    body: {dataset: {comparison: 'synthetic-run'}},
    getElementById: id => id === 'download' ? {addEventListener: (event, fn) => {handler = fn;}} : status,
    querySelectorAll: () => sections,
    createElement: () => ({click() { clicked = true; }})
  },
  Blob, Date, JSON, Array,
  URL: {createObjectURL(value) { blob = value; return 'blob:synthetic'; }, revokeObjectURL() { revoked = true; }},
  setTimeout: fn => fn()
};
vm.runInNewContext(fs.readFileSync('src/elektro_vienna/policy_compare.js', 'utf8'), context);
handler();
blob.text().then(text => {
  const record = JSON.parse(text);
  assert.equal(record.comparison, 'synthetic-run');
  assert.deepEqual(record.cases.map(c => c.preference), [null, 'A', 'equal']);
  assert.equal(record.cases[2].comment, 'Test comment');
  assert(!text.includes('policy_only') && !text.includes('"PH"'));
  assert(clicked && revoked && status.textContent.includes('keine automatische Speicherung'));
  console.log('Policy comparison download contract passed');
});
