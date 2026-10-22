const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let download, copy, input, unload, blob, copied, selected = false;
const status = {textContent: ''};
const draft = {textContent: 'Customer body\n\nViele Grüße\nGiovanni Superti-Furga'};
const article = {dataset: {input: 'source-hash', card: 'card-hash'}, querySelector: selector =>
  selector === '.draft' ? draft : selector === 'input:checked' ? {value: 'needs_edit'} : {value: ' Exact typo\n'}};
const button = {closest: () => article, addEventListener: (_, fn) => {copy = fn;}};
const context = {
  document: {body: {dataset: {batch: 'batch-hash'}},
    querySelectorAll: selector => selector === 'article' ? [article] : [button],
    getElementById: id => id === 'status' ? status : {addEventListener: (_, fn) => {download = fn;}},
    addEventListener: (_, fn) => {input = fn;},
    createElement: () => ({click() {}}), createRange: () => ({selectNodeContents(value) {selected = value === draft;}})},
  window: {addEventListener: (_, fn) => {unload = fn;}, getSelection: () => ({removeAllRanges() {}, addRange() {}})},
  navigator: {clipboard: {async writeText(value) {copied = value;}}},
  URL: {createObjectURL(value) {blob = value; return 'blob:test';}, revokeObjectURL() {}},
  Blob, JSON, Date, setTimeout: fn => fn()
};
(async () => {
  vm.runInNewContext(fs.readFileSync('src/elektro_vienna/shadow_review.js', 'utf8'), context);
  input(); let warned = false; unload({preventDefault() {warned = true;}}); assert(warned);
  await copy(); assert.equal(copied, draft.textContent); assert(!copied.includes('CRM'));
  context.navigator.clipboard.writeText = async () => {throw new Error('unavailable');};
  await copy(); assert(selected); assert(status.textContent.includes('Ctrl+C'));
  download(); const feedback = JSON.parse(await blob.text());
  assert.equal(feedback.kind, 'live_shadow_feedback'); assert.equal(feedback.batch, 'batch-hash');
  assert.deepEqual(feedback.items, [{input_reference: 'source-hash', card_reference: 'card-hash', choice: 'needs_edit', comment: ' Exact typo\n'}]);
  assert(status.textContent.includes('does not autosave'));
  console.log('Shadow feedback, binding, copy-only and fallback tests passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
