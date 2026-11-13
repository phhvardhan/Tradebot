// node tests/test_ui.mjs: stocks.js runs against a fake page and a fake server without throwing, and shows the warnings.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const els = {};
const make = () => new Proxy(function () {}, {
  get: (t, k) => typeof k === 'symbol' ? undefined : k === 'valueOf' ? () => 0 : k === 'toString' ? () => '' : k === 'children' || /^\d+$/.test(k) ? make() : k === 'clientWidth' ? 800 : k === 'getTotalLength' ? () => 100 : k === 'hidden' || k === 'disabled' ? t[k] ?? false : k === 'textContent' ? t[k] ?? '' : k in t ? t[k]
    : k === 'classList' || k === 'dataset' || k === 'style' ? (t[k] = make()) : (...a) => make(),
  set: (t, k, v) => (t[k] = v, true), apply: () => make() });
globalThis.document = { getElementById: id => els[id] ??= make(), createElement: make, createElementNS: make, querySelectorAll: () => [], querySelector: () => make(), documentElement: make(), addEventListener() {}, body: make() };
globalThis.Image = function () { return make(); }; globalThis.window = globalThis; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.history = { pushState() {} }; globalThis.addEventListener = () => {};
globalThis.location = { pathname: '/stocks' }; globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.setTimeout = () => 0; globalThis.setInterval = () => 0; globalThis.requestAnimationFrame = () => 0; globalThis.cancelAnimationFrame = () => {};
globalThis.dispatchEvent = () => true; globalThis.Event = function () {};
const ui = readFileSync(new URL('../static/ui.js', import.meta.url), 'utf8');
const state = { token: 't', capital: '5000', equity: '5000', cash: '4500', realized: '0', day_start: '5000', daily_loss: '400', per_trade: '500', stop: '0.1', target: '0.25',
  alert_at: '0.2', trail: '0', max_open: '0', max_new: '3', congress_days: '0', slippage: '0.0005', gate: true, symbols: ['AAA'], decisions: {}, quotes: {},
  positions: { AAA: { qty: '1', entry: '100', cost: '100', last: '100', opened: '2026-10-26', alerted: false } },
  trades: [], history: [], daily: {}, alerts: [], rule_log: [{ at: 1, text: 'stop: 0.2 → 0.1' }], updated: Date.now() / 1000, running: false, busy: false, error: null, status: '1 open', market: { open: true, date: '2026-10-26' }, decision: '', bench: null, edge: null };
let current = state;
globalThis.fetch = async url => ({ ok: true, json: async () => url === '/api/congress' ? { trades: [], configured: true } : structuredClone(current) });
const tick = () => new Promise(r => setImmediate(r));

new Function(ui + '\n' + readFileSync(new URL('../static/stocks.js', import.meta.url), 'utf8'))();
await tick(); await tick();
assert.match(els.error.textContent, /paused with 1 open position.*NOT being checked/);
assert.equal(els['r-maxnew'].textContent, '3 a day');

current = { ...state, running: true, updated: Date.now() / 1000 - 900 };
new Function(ui + '\n' + readFileSync(new URL('../static/stocks.js', import.meta.url), 'utf8'))();
await tick(); await tick();
assert.match(els.error.textContent, /No check for 15 minutes/);
console.log('stocks.js smoke test ok');

// desk.js: the Action-needed page renders cards, notes and the watch table against a fake server without throwing.
const deskState = { holdings: { account: 'Robinhood ••••1234', source: 'test', synced_at: Date.now() / 1000 - 60, cash: '2000', positions: [] }, rules: { per_trade: '500', stop: '0.1', target: '0.25', max_new: '3', max_open: '0' },
  updated: Date.now() / 1000, market: { open: true, date: '2026-11-12', reason: 'regular session' }, notes: [{ kind: 'cash', text: 'Cash is not in the import.' }],
  cards: [{ action: 'BUY', symbol: 'KO', trigger: 'Close 101.00 broke the 20-day closing high 100.00.', price: '101', quote_at: Date.now() / 1000, qty: '4.9504', cost: '500', stop: '90.9', target: '126.25', est_loss: '50', est_note: 'estimate', checks: [{ ok: true, text: 'Market open' }, { ok: false, text: 'Cash $0.00' }], passes: false, can_place: false },
    { action: 'SELL', symbol: 'AAPL', trigger: 'Stop', price: '89', quote_at: null, qty: '4', proceeds: '356', est_pnl: '-44', est_note: 'estimate', checks: [{ ok: true, text: 'Market open' }], passes: true, can_place: true }],
  watching: [{ symbol: 'AAPL', purpose: 'swing', qty: '4', price: '89', value: '356', avg_cost: '100', gain: '-0.11', plan: { stop: '90', target: '125' }, to_stop: '-0.011' }, { symbol: 'XOM', purpose: 'invest', qty: '1', price: '80', value: '80', avg_cost: null, gain: null, plan: null }], unpriced: ['MSFT'], agent: { connected: true, paper: true, error: null, modes: { off: 'Off', ask: 'Ask me', auto: 'Automatic' }, settings: { mode: 'ask', per_trade: '1000', max_new: '3', daily_loss: '500' },
  orders: [{ at: Date.now() / 1000, symbol: 'KO', side: 'buy', qty: '9', mode: 'ask', ok: true, status: 'accepted', plan: { stop: '90.90', target: '126.25' } }, { at: Date.now() / 1000, symbol: 'V', side: 'buy', qty: '2', mode: 'auto', ok: false, error: 'refused' }] } };
globalThis.location = { pathname: '/desk' };
globalThis.fetch = async () => ({ ok: true, json: async () => structuredClone(deskState) });
new Function(ui + '\n' + readFileSync(new URL('../static/desk.js', import.meta.url), 'utf8'))();
await tick(); await tick();
assert.equal(els.todo.textContent, '2');
assert.match(els['todo-sub'].textContent, /1 pass/);
assert.equal(els['cards-empty'].hidden, true);
assert.match(els.synced.textContent, /value leaves out MSFT/);
assert.equal(els.agent.hidden, false);
assert.equal(els['orders-empty'].hidden, true);
assert.equal(els['a-per_trade'].value, '1000');
assert.match(els['agent-mode-text'].textContent, /Ask me/);
console.log('desk.js smoke test ok');
