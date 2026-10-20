const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(require.resolve('../components/profile/tradeSize.ts'), 'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: exportsObject });
const { marginSelection: select } = exportsObject;

test('$100 of own funds with $50 available is rejected immediately', () => {
  const result = select('10', '100', 50);
  assert.match(result.error, /Insufficient available balance/);
  assert.match(result.error, /\$100\.00.*\$50\.00/);
  assert.equal(result.percentage, null);
});
test('100% of $50 means a $50 budget, never $100', () => {
  const result = select('100', null, 50);
  assert.equal(result.error, null);
  assert.equal(result.budget, 50);
});
test('dollars and percentages use available funds, independent of leverage', () => {
  assert.equal(select('10', '25', 50).percentage, 50);
  assert.equal(select('50', null, 50).budget, 25);
  assert.equal(select('10', '50', 50).percentage, 100);
});
test('balance changes revalidate the entered dollar amount', () => {
  assert.equal(select('50', '40', 50).error, null);
  assert.match(select('50', '40', 30).error, /Insufficient available balance/);
});
test('unavailable, zero, negative or non-finite balances cannot save', () => {
  for (const balance of [null, undefined, 0, -1, NaN, Infinity]) {
    assert.ok(select('50', null, balance).error);
  }
});
test('invalid values and percentages over 100 are rejected', () => {
  for (const amount of ['', '0', '-5', 'NaN', 'Infinity', '51']) assert.ok(select('50', amount, 50).error);
  for (const pct of ['', '0', '-1', '1.5', '101', '200', 'Infinity']) assert.ok(select(pct, null, 50).error);
});
test('dollar conversion rounds down and never allocates more than entered', () => {
  const result = select('1', '25', 123.45);
  assert.equal(result.percentage, 20);
  assert.ok(result.budget <= 25);
  assert.equal(select('1', '29', 50).percentage, 58);
  assert.ok(select('1', '.001', 50).error);
  for (let cents = 50; cents <= 5000; cents += 7) {
    const amount = cents / 100;
    const selection = select('1', String(amount), 50);
    assert.equal(selection.error, null);
    assert.ok(selection.budget <= amount);
  }
});
