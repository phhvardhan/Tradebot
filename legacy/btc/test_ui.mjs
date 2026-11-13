// node test_ui.mjs: dashboard logic with a fake DOM and fake server.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const els = {};
const el = () => ({ classList: { toggle() {} }, replaceChildren() {}, append() {}, setAttribute() {},
  addEventListener(_, f) { this.click = f; }, hidden: false, disabled: false, textContent: '' });
globalThis.document = { getElementById: id => els[id] ??= el(), createElement: el, createElementNS: el, documentElement: {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.setTimeout = () => {};
const state = { token: 't', source: 'demo', capital: '4000', equity: '4000', cash: '4000', realized: '0', fees: '0', qty: '0',
  mark: '0', entry: '0', decision: '', updated: null, running: false, busy: false, error: null, fee_rate: '.004', slippage: '.001',
  candles: [], history: [], trades: [{ time: 0, side: 'BUY', qty: '1', price: '1', fee: '0', pnl: '0', reason: '' }] };
let post = { ok: false, status: 400, text: async () => 'Unknown action or pause first' };
globalThis.fetch = async (url, opts) => opts?.method === 'POST' ? post
  : { ok: true, json: async () => url === '/api/account' ? { enabled: false, balances: [] } : structuredClone(state) };
const tick = () => new Promise(r => setImmediate(r));

new Function(readFileSync(new URL('static/app.js', import.meta.url), 'utf8'))();
await tick(); await tick();

await els.step.click(); await tick();
assert.equal(els.error.hidden, false, 'action error was hidden by refresh');
assert.match(els.error.textContent, /pause first/);

post = { ok: true, text: async () => '' };
await els.step.click(); await tick();
assert.equal(els.error.hidden, true, 'error should clear after a successful action');

state.trades = Array(100).fill(state.trades[0]);
await els.step.click(); await tick();
assert.equal(els.count.textContent, '100 trades');
state.more = true;
await els.step.click(); await tick();
assert.equal(els.count.textContent, '100+ trades');
console.log('ui ok');
