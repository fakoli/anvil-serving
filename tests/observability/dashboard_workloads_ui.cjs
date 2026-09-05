'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let source = fs.readFileSync(process.argv[2], 'utf8');
if (process.argv[3] === 'negative-generation-guard') {
  const guard = 'if (generation !== request.generation) return;';
  assert.ok(source.includes(guard));
  source = source.split(guard).join('');
}
const TOKEN_A = 'fixture-workload-token-a';
const TOKEN_B = 'fixture-workload-token-b';
const TIME = '2026-09-05T12:00:00.000001Z';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.children = [];
    this.listeners = new Map();
    this.className = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this._text = '';
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map(child => child.textContent).join('');
  }
  set innerHTML(_value) { throw new Error('innerHTML is forbidden'); }
  get innerHTML() { throw new Error('innerHTML is forbidden'); }
  append(...children) {
    for (const child of children) {
      assert.ok(child instanceof Element, 'DOM append accepts only created elements');
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children = [];
    this._text = '';
    this.append(...children);
  }
  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }
  dispatch(name, values = {}) {
    let prevented = false;
    const event = {
      type: name,
      target: this,
      preventDefault() { prevented = true; },
      ...values,
    };
    for (const callback of this.listeners.get(name) || []) callback(event);
    return prevented;
  }
}

function harness({hidden = true} = {}) {
  let now = 0;
  let nextTimer = 1;
  const timers = new Map();
  const calls = [];
  const logs = [];
  const ids = [
    'workloads', 'workload-results', 'workload-status', 'workload-token',
    'workload-disconnect', 'workload-auth', 'workload-owner', 'workload-kind',
    'workload-state', 'workload-host', 'workload-active', 'workload-recent',
    'workload-limit',
  ];
  const elements = new Map(ids.map(id => [id, new Element('div', id)]));
  elements.get('workloads').hidden = hidden;
  elements.get('workload-active').value = 'false';
  elements.get('workload-recent').value = '3600';
  elements.get('workload-limit').value = '200';
  const documentListeners = new Map();
  const document = {
    hidden: false,
    getElementById(id) { return elements.get(id) || null; },
    createElement(tag) { return new Element(tag); },
    addEventListener(name, callback) {
      const callbacks = documentListeners.get(name) || [];
      callbacks.push(callback);
      documentListeners.set(name, callbacks);
    },
    dispatch(name) {
      for (const callback of documentListeners.get(name) || []) callback({type: name});
    },
  };
  Object.defineProperty(document, 'cookie', {
    get() { throw new Error('cookie read'); },
    set() { throw new Error('cookie write'); },
  });
  const storage = new Proxy({}, {get() { throw new Error('storage access'); }});
  class AbortController {
    constructor() { this.signal = {aborted: false}; }
    abort() { this.signal.aborted = true; }
  }
  const setTimeout = (callback, delay = 0) => {
    assert.ok(Number.isFinite(Number(delay)) && Number(delay) >= 0);
    const id = nextTimer++;
    timers.set(id, {at: now + Number(delay), callback});
    return id;
  };
  const clearTimeout = id => timers.delete(id);
  const fetch = (url, options) => {
    const pending = deferred();
    calls.push({url, options, pending});
    return pending.promise;
  };
  const window = {};
  const context = vm.createContext({
    window, document, fetch, AbortController, URLSearchParams, TextEncoder,
    performance: {now: () => now}, setTimeout, clearTimeout,
    sessionStorage: storage, localStorage: storage,
    console: {
      log: (...values) => logs.push(values.join(' ')),
      warn: (...values) => logs.push(values.join(' ')),
      error: (...values) => logs.push(values.join(' ')),
    },
  });
  vm.runInContext(source, context, {filename: process.argv[2], timeout: 2000});

  async function flush() {
    for (let index = 0; index < 12; index += 1) await Promise.resolve();
  }
  async function advance(milliseconds) {
    now += milliseconds;
    while (true) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.at <= now)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0]);
      if (!due.length) break;
      const [id, timer] = due[0];
      timers.delete(id);
      timer.callback();
      await flush();
    }
  }
  return {window, document, elements, calls, logs, timers, flush, advance};
}

function response(body, status = 200, textPromise = null) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => textPromise ? textPromise.promise : JSON.stringify(body),
  };
}

function record({id = 'a'.repeat(64), label = 'Controller Operation', state = 'terminal',
  phase = 'completed', outcome = 'success', quality = 'recorded', progress = undefined} = {}) {
  const value = {
    schema: 'anvil-workloads/v1', id, kind: 'controller-operation', owner: 'controller',
    host: 'worker-a', label, state, phase,
    created_at: TIME, updated_at: TIME, source_timestamp: TIME,
    source_authority: 'controller-store', observation_quality: quality,
  };
  if (outcome !== undefined) value.outcome = outcome;
  if (progress !== undefined) value.progress = progress;
  return value;
}

function snapshot({status = 'complete', records = [record()], omitted = 0,
  error = null, nodes = undefined} = {}) {
  const source = {
    schema: 'anvil-workloads/v1', owner: 'controller', status,
    collection_timestamp: TIME, records,
    truncation: {returned: records.length, omitted}, error,
  };
  const selectedNodes = nodes === undefined ? [{
    schema: 'anvil-workloads/v1', host: 'worker-a', status,
    collection_timestamp: TIME, sources: [source],
  }] : nodes;
  return {ok: true, data: {
    schema: 'anvil-workloads/v1', status, collection_timestamp: TIME,
    nodes: selectedNodes,
    truncation: {returned: selectedNodes.reduce(
      (total, node) => total + node.sources.reduce(
        (count, item) => count + item.records.length, 0), 0), omitted},
  }};
}

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const item of Object.values(value)) deepFreeze(item);
  }
  return value;
}

function allText(h) {
  return [...h.elements.values()].map(item => item.textContent).join('\n');
}

async function connect(h, token = TOKEN_A) {
  h.elements.get('workload-token').value = token;
  assert.equal(h.elements.get('workload-auth').dispatch('submit'), true);
  await h.advance(0);
}

async function settle(h, index, body, status = 200, textPromise = null) {
  h.calls[index].pending.resolve(response(body, status, textPromise));
  await h.flush();
}

async function testHiddenAndCanonicalRendering() {
  const h = harness({hidden: true});
  assert.deepEqual(Object.getOwnPropertyNames(h.window), ['AnvilWorkloads']);
  assert.ok(Object.isFrozen(h.window.AnvilWorkloads));
  await connect(h);
  assert.equal(h.calls.length, 0, 'hidden panel must not fetch');
  h.window.AnvilWorkloads.setVisible(true);
  await h.advance(0);
  assert.equal(h.calls.length, 1);
  const call = h.calls[0];
  assert.equal(call.url, '/v1/workloads?active_only=false&recent_seconds=3600&limit=200');
  assert.equal(call.options.headers.Authorization, 'Bearer ' + TOKEN_A);
  assert.equal(call.options.cache, 'no-store');
  assert.equal(call.options.credentials, 'omit');
  assert.equal(call.options.redirect, 'error');
  assert.equal(call.options.signal.aborted, false);
  const body = deepFreeze(snapshot({records: [
    record({label: '<img src=x onerror=private-marker>', progress: {completed: 2, total: 3, unit: 'steps'}}),
    record({id: 'b'.repeat(64), state: 'running', phase: 'running', outcome: undefined}),
    record({id: 'c'.repeat(64), state: 'running', phase: 'running', outcome: undefined, quality: 'stale'}),
  ]}));
  const before = JSON.stringify(body);
  await settle(h, 0, body);
  const text = allText(h);
  assert.match(text, /<img src=x onerror=private-marker>/);
  assert.match(text, /Terminal/);
  assert.match(text, /Active/);
  assert.match(text, /Stale/);
  assert.match(text, /2 \/ 3 steps/);
  assert.match(text, /2026-09-05T12:00:00\.000001Z/);
  assert.equal(JSON.stringify(body), before, 'renderer mutated the source fixture');
  assert.equal(h.elements.get('workload-token').value, '');
  assert.ok(!call.url.includes(TOKEN_A));
  assert.ok(!allText(h).includes(TOKEN_A));
  assert.ok(!h.logs.join('\n').includes(TOKEN_A));
  assert.deepEqual(Object.getOwnPropertyNames(h.window), ['AnvilWorkloads']);
}

async function testStatusesAndOmissions() {
  const cases = [
    [snapshot({records: [], status: 'complete'}), 'No matching workloads.'],
    [snapshot({records: [], status: 'partial', omitted: null, error: 'invalid-workload'}), 'Incomplete workload evidence'],
    [snapshot({records: [], status: 'unavailable', omitted: null, error: 'workload-source-unavailable'}), 'Workload evidence unavailable.'],
    [snapshot({status: 'partial', omitted: 9, error: 'invalid-workload'}), '9 omitted · truncated / incomplete'],
    [snapshot({status: 'partial', omitted: null, error: 'invalid-workload'}), 'unknown omitted · truncated / incomplete'],
  ];
  for (const [body, expected] of cases) {
    const h = harness({hidden: false});
    await connect(h);
    await settle(h, 0, body);
    assert.ok(allText(h).includes(expected));
    if (body.data.status !== 'complete') {
      assert.ok(!allText(h).includes('No matching workloads.'));
    }
  }
}

async function testFiltersAndValidationFailures() {
  const h = harness({hidden: false});
  Object.assign(h.elements.get('workload-owner'), {value: 'controller'});
  Object.assign(h.elements.get('workload-kind'), {value: 'controller-operation'});
  Object.assign(h.elements.get('workload-state'), {value: 'running'});
  Object.assign(h.elements.get('workload-host'), {value: 'worker-a'});
  Object.assign(h.elements.get('workload-active'), {value: 'true'});
  Object.assign(h.elements.get('workload-recent'), {value: '42'});
  Object.assign(h.elements.get('workload-limit'), {value: '17'});
  await connect(h);
  assert.equal(h.calls[0].url,
    '/v1/workloads?owner=controller&kind=controller-operation&state=running&host=worker-a&active_only=true&recent_seconds=42&limit=17');
  h.calls[0].pending.reject(new Error('private-fetch-detail'));
  await h.flush();
  assert.equal(h.elements.get('workload-status').textContent,
    'Workload evidence unavailable. Check the connection and configuration.');
  assert.ok(!allText(h).includes('private-fetch-detail'));

  h.elements.get('workload-limit').value = '0';
  h.elements.get('workload-limit').dispatch('change');
  await h.advance(5000);
  assert.equal(h.calls.length, 1);
  assert.equal(h.elements.get('workload-status').textContent,
    'Invalid workload filters. Check the selected values.');

  h.elements.get('workload-token').value = 'short';
  h.elements.get('workload-auth').dispatch('submit');
  await h.advance(0);
  assert.equal(h.calls.length, 1);
  assert.equal(h.elements.get('workload-status').textContent,
    'Enter a valid workload read credential.');
}

async function testMalformedResponsesAreFixed() {
  const invalid = [];
  const extra = snapshot(); extra.private_marker = 'private-body-detail'; invalid.push(extra);
  const schema = snapshot(); schema.data.schema = 'other'; invalid.push(schema);
  const status = snapshot(); status.data.status = 'idle'; invalid.push(status);
  const arrays = snapshot(); arrays.data.nodes = {}; invalid.push(arrays);
  const times = snapshot(); times.data.nodes[0].sources[0].records[0].updated_at = 'bad-private-time'; invalid.push(times);
  const count = snapshot(); count.data.truncation.returned = 99; invalid.push(count);
  const duplicateNode = snapshot(); duplicateNode.data.nodes.push(duplicateNode.data.nodes[0]); invalid.push(duplicateNode);
  const duplicateOwner = snapshot(); duplicateOwner.data.nodes[0].sources.push(duplicateOwner.data.nodes[0].sources[0]); invalid.push(duplicateOwner);
  const duplicateId = snapshot({records: [record(), record()]}); invalid.push(duplicateId);
  const overflow = snapshot({records: Array.from({length: 201}, (_, index) => record({id: index.toString(16).padStart(64, '0')}))}); invalid.push(overflow);
  for (const body of invalid) {
    const h = harness({hidden: false});
    await connect(h);
    await settle(h, 0, body);
    assert.equal(h.elements.get('workload-status').textContent,
      'Workload evidence unavailable. Check the connection and configuration.');
    assert.equal(h.elements.get('workload-results').children.length, 0);
    assert.ok(!allText(h).includes('private-body-detail'));
    assert.ok(!allText(h).includes('bad-private-time'));
  }
}

async function testCadenceTimeoutAndLateCompletion() {
  const h = harness({hidden: false});
  await connect(h);
  assert.equal(h.calls.length, 1);
  await h.advance(4000);
  assert.equal(h.calls.length, 1, 'requests overlap');
  await settle(h, 0, snapshot());
  await h.advance(4999);
  assert.equal(h.calls.length, 1);
  await h.advance(1);
  assert.equal(h.calls.length, 2, 'next poll was not five seconds after completion');

  await h.advance(8000);
  assert.equal(h.calls[1].options.signal.aborted, true);
  assert.match(h.elements.get('workload-status').textContent, /timed out/);
  assert.equal(h.calls.length, 2, 'timed out call was replaced before settlement');
  await settle(h, 1, snapshot({records: [record({label: 'private-late-marker'})]}));
  assert.ok(!allText(h).includes('private-late-marker'));
  await h.advance(4999);
  assert.equal(h.calls.length, 2);
  await h.advance(1);
  assert.equal(h.calls.length, 3);
}

async function testGenerationInvalidationAndReconnect() {
  const h = harness({hidden: false});
  await connect(h, TOKEN_A);
  const textDone = deferred();
  h.calls[0].pending.resolve(response(snapshot(), 200, textDone));
  await h.flush();
  h.elements.get('workload-disconnect').dispatch('click');
  assert.equal(h.calls[0].options.signal.aborted, true);
  await connect(h, TOKEN_B);
  assert.equal(h.calls.length, 1, 'new credential overlapped the old text read');
  textDone.resolve(JSON.stringify(snapshot({records: [record({label: 'private-old-marker'})]})));
  await h.flush();
  assert.ok(!allText(h).includes('private-old-marker'));
  await h.advance(5000);
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].options.headers.Authorization, 'Bearer ' + TOKEN_B);
  h.document.hidden = true;
  h.document.dispatch('visibilitychange');
  assert.equal(h.calls[1].options.signal.aborted, true);
  await settle(h, 1, snapshot({records: [record({label: 'private-hidden-marker'})]}));
  assert.ok(!allText(h).includes('private-hidden-marker'));
  await h.advance(10000);
  assert.equal(h.calls.length, 2);
  h.document.hidden = false;
  h.document.dispatch('visibilitychange');
  await h.advance(0);
  assert.equal(h.calls.length, 3);
  h.window.AnvilWorkloads.setVisible(false);
  assert.equal(h.calls[2].options.signal.aborted, true);
}

async function testFilterChangeInvalidatesOldGeneration() {
  const h = harness({hidden: false});
  await connect(h);
  h.elements.get('workload-owner').value = 'controller';
  h.elements.get('workload-owner').dispatch('change');
  assert.equal(h.calls[0].options.signal.aborted, true);
  await h.advance(5000);
  assert.equal(h.calls.length, 1, 'filter change overlapped an unresolved read');
  await settle(h, 0, snapshot({records: [record({label: 'private-filter-marker'})]}));
  assert.ok(!allText(h).includes('private-filter-marker'));
  await h.advance(4999);
  assert.equal(h.calls.length, 1);
  await h.advance(1);
  assert.equal(h.calls.length, 2);
  assert.match(h.calls[1].url, /owner=controller/);
}

async function testAuthorizationAndRetryRelease() {
  for (const denied of [401, 403]) {
    const h = harness({hidden: false});
    await connect(h);
    await settle(h, 0, {private: 'private-denied-body'}, denied);
    assert.match(h.elements.get('workload-status').textContent, /access denied/);
    assert.equal(h.elements.get('workload-results').children.length, 0);
    await h.advance(20000);
    assert.equal(h.calls.length, 1, 'denial continued polling');
    assert.ok(!allText(h).includes('private-denied-body'));
  }

  const h = harness({hidden: false});
  await connect(h);
  await settle(h, 0, {private: 'private-500-body'}, 500);
  assert.equal(h.elements.get('workload-results').children.length, 0);
  assert.ok(!allText(h).includes('private-500-body'));
  await h.advance(5000);
  assert.equal(h.calls.length, 2, 'failed response did not release in-flight state');
}

const tests = [
  testHiddenAndCanonicalRendering,
  testStatusesAndOmissions,
  testFiltersAndValidationFailures,
  testMalformedResponsesAreFixed,
  testCadenceTimeoutAndLateCompletion,
  testGenerationInvalidationAndReconnect,
  testFilterChangeInvalidatesOldGeneration,
  testAuthorizationAndRetryRelease,
];

(async () => {
  for (const test of tests) await test();
  process.stdout.write(JSON.stringify({ok: true, scenarios: tests.map(test => test.name)}) + '\n');
})().catch(error => {
  process.stderr.write((error && error.stack ? error.stack : String(error)) + '\n');
  process.exitCode = 1;
});
