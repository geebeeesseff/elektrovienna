// Synthetic DOM: verifies partial/completed exports and unsaved changes without customer data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let download, input, beforeunload, blob, revoked = false, clicked = false;
const progress = {textContent: ''}, saved = {textContent: ''};
const values = [null, 'same', 'different'];
const cards = values.map((_, i) => ({
  dataset: {id: `example-${i}`},
  querySelector: selector => selector === 'input:checked'
    ? (values[i] ? {value: values[i]} : null) : {value: ' Exact\ncomment  '}
}));
const context = {
  document: {
    body: {dataset: {set: 'synthetic-set'}, appendChild() {}},
    querySelectorAll: () => cards,
    addEventListener: (_, fn) => { input = fn; },
    getElementById: id => id === 'download' ? {addEventListener: (_, fn) => { download = fn; }}
      : id === 'progress' ? progress : saved,
    createElement: () => ({click() { clicked = true; }, remove() {}})
  },
  window: {addEventListener: (_, fn) => { beforeunload = fn; }},
  URL: {createObjectURL(value) { blob = value; return 'blob:synthetic'; }, revokeObjectURL() { revoked = true; }},
  Blob, Date, JSON, Array, setTimeout: fn => fn()
};
(async () => {
  vm.runInNewContext(fs.readFileSync('src/elektro_vienna/response_calibration.js', 'utf8'), context);
  assert.equal(progress.textContent, '2 / 3 bewertet');
  input();
  let prevented = false;
  beforeunload({preventDefault() { prevented = true; }});
  assert(prevented);
  download();
  let review = JSON.parse(await blob.text());
  assert.equal(review.set, 'synthetic-set');
  assert.equal(review.complete, false);
  assert.deepEqual(review.cases.map(c => c.choice), [null, 'same', 'different']);
  assert.equal(review.cases[0].comment, ' Exact\ncomment  ');
  assert(clicked && revoked);
  prevented = false;
  beforeunload({preventDefault() { prevented = true; }});
  assert(!prevented);
  values[0] = 'different'; input(); download();
  review = JSON.parse(await blob.text());
  assert.equal(review.complete, true);
  assert.equal(review.reviewer, 'Giovanni Superti-Furga');
  assert.equal(progress.textContent, '3 / 3 bewertet');
  assert.equal(review.kind, 'first_response_calibration_review');
  context.document.body.dataset.mode = 'validation';
  context.document.body.dataset.answers = 'frozen-answer-hash';
  values.splice(0, 3, 'send', 'minor_change', 'reject');
  input(); download();
  review = JSON.parse(await blob.text());
  assert.equal(review.kind, 'first_response_validation_review');
  assert.equal(review.answers_sha256, 'frozen-answer-hash');
  assert.deepEqual(review.cases.map(c => c.choice), ['send', 'minor_change', 'reject']);
  console.log('Response calibration export tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
