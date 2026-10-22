// Pure chart aggregation checks; no browser or external dependencies.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('ai_platform/public/elements/Results.jsx', 'utf8');
const start = source.indexOf('function chartGroups(');
const end = source.indexOf('function ResultChart(', start);
const context = vm.createContext({ isBlank: value => value == null || value === '' });
vm.runInContext(source.slice(start, end), context);
const groups = (...args) => JSON.parse(JSON.stringify(context.chartGroups(...args)));
const columns = ['region', 'weight'];
const rows = [['Asia', 10], ['Asia', null], ['Europe', -4], [null, 0], ['Asia', 5], ['Europe', NaN]];
assert.deepEqual(groups(columns, rows, 'region', '__count'), [
  {name:'Asia',value:3}, {name:'Europe',value:2}, {name:'Not specified',value:1}
]);
assert.deepEqual(groups(columns, rows, 'region', 'weight'), [
  {name:'Asia',value:15}, {name:'Europe',value:-4}, {name:'Not specified',value:0}
]);
assert.deepEqual(groups(columns, [], 'region', '__count'), []);
assert.deepEqual(groups(columns, [['Asia', Infinity]], 'region', 'weight'), []);
console.log('Chart aggregation: counts, sums, negatives, blanks and empty results passed.');
