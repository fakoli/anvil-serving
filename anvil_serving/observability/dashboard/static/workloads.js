/* Canonical workload view. Credentials and request state never leave this closure. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const panel = byId('workloads');
  const results = byId('workload-results');
  const status = byId('workload-status');
  const tokenInput = byId('workload-token');
  const disconnect = byId('workload-disconnect');
  const controls = {
    owner: byId('workload-owner'), kind: byId('workload-kind'),
    state: byId('workload-state'), host: byId('workload-host'),
    active_only: byId('workload-active'), recent_seconds: byId('workload-recent'),
    limit: byId('workload-limit'),
  };
  const kinds = ['router-request', 'controller-operation', 'benchmark-job', 'media-job', 'recipe-serve'];
  const ownerKinds = {router: kinds[0], controller: kinds[1], benchmark: kinds[2], media: kinds[3], recipe: kinds[4], manifest: kinds[4]};
  const states = ['checking', 'admitted', 'dispatched', 'streaming', 'queued', 'running', 'terminal', 'configured', 'absent', 'unavailable', 'unsupported'];
  const phases = [...states, 'completed', 'failed', 'cancelled', 'awaiting-approval', 'preparing', 'submitting'];
  const outcomes = ['success', 'error', 'cancelled', 'timeout', 'rejected', 'disconnected', 'unavailable', 'unknown'];
  const qualities = ['recorded', 'configured', 'observed-running', 'healthy-identity', 'stale', 'absent', 'inspection-error'];
  const authorities = ['router-memory', 'controller-store', 'benchmark-store', 'media-store', 'managed-status'];
  const sourceErrors = {
    'invalid-workload': 'Invalid workload evidence',
    'unsupported-workload': 'Unsupported workload evidence',
    'workload-source-unavailable': 'Workload source unavailable',
    'future-workload-timestamp': 'Future workload timestamp',
  };
  const statuses = ['complete', 'partial', 'unavailable'];
  const active = new Set(states.slice(0, 6));
  const MAX_BYTES = 8 * 1024 * 1024;
  let credential = '';
  let visible = !panel.hidden;
  let generation = 0;
  let inFlight = null;
  let timer = null;
  let nextAt = 0;

  function require(value) { if (!value) throw new Error('Invalid workload response'); }
  function text(value) { return typeof value === 'string' && value.length > 0 && value.length <= 1024; }
  function count(value, max = 1000000000) { return Number.isSafeInteger(value) && value >= 0 && value <= max; }
  function fields(value, required, optional = []) {
    require(value !== null && typeof value === 'object' && !Array.isArray(value));
    require(required.every(key => Object.hasOwn(value, key)));
    require(Object.keys(value).every(key => required.includes(key) || optional.includes(key)));
  }
  function timestamp(value) {
    require(typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value) && Number.isFinite(Date.parse(value)));
  }
  function truncation(value, returned) {
    fields(value, ['returned', 'omitted']);
    require(count(value.returned, 1000) && value.returned === returned);
    require(value.omitted === null || count(value.omitted));
  }
  function combined(values) {
    if (values.length && values.every(value => value === 'unavailable')) return 'unavailable';
    return values.some(value => value !== 'complete') ? 'partial' : 'complete';
  }
  function validateRecord(record, owner, host) {
    fields(record, ['schema', 'id', 'kind', 'owner', 'host', 'label', 'state', 'phase', 'created_at', 'updated_at', 'source_timestamp', 'source_authority', 'observation_quality'], ['outcome', 'progress']);
    require(record.schema === 'anvil-workloads/v1' && /^[0-9a-f]{64}$/.test(record.id));
    require(record.owner === owner && record.host === host && record.kind === ownerKinds[owner]);
    require(text(record.label) && states.includes(record.state) && phases.includes(record.phase));
    require(authorities.includes(record.source_authority) && qualities.includes(record.observation_quality));
    ['created_at', 'updated_at', 'source_timestamp'].forEach(key => timestamp(record[key]));
    if (Object.hasOwn(record, 'outcome')) require(outcomes.includes(record.outcome));
    if (Object.hasOwn(record, 'progress')) {
      const progress = record.progress;
      fields(progress, ['completed', 'total', 'unit']);
      require(count(progress.completed) && (progress.total === null || (count(progress.total) && progress.completed <= progress.total)));
      require(['items', 'requests', 'steps'].includes(progress.unit));
    }
  }
  function validateSnapshot(body) {
    fields(body, ['ok', 'data']);
    require(body.ok === true);
    const fleet = body.data;
    fields(fleet, ['schema', 'status', 'collection_timestamp', 'nodes', 'truncation']);
    require(fleet.schema === 'anvil-workloads/v1' && statuses.includes(fleet.status));
    timestamp(fleet.collection_timestamp);
    require(Array.isArray(fleet.nodes) && fleet.nodes.length <= 1000);
    let total = 0;
    const hosts = new Set();
    for (const node of fleet.nodes) {
      fields(node, ['schema', 'host', 'status', 'collection_timestamp', 'sources']);
      require(node.schema === fleet.schema && text(node.host) && !hosts.has(node.host) && statuses.includes(node.status));
      hosts.add(node.host);
      timestamp(node.collection_timestamp);
      require(Array.isArray(node.sources) && node.sources.length > 0 && node.sources.length <= 6);
      const owners = new Set();
      for (const source of node.sources) {
        fields(source, ['schema', 'owner', 'status', 'collection_timestamp', 'records', 'truncation', 'error']);
        require(source.schema === fleet.schema && Object.hasOwn(ownerKinds, source.owner) && !owners.has(source.owner));
        owners.add(source.owner);
        require(statuses.includes(source.status) && (source.error === null || Object.hasOwn(sourceErrors, source.error)));
        timestamp(source.collection_timestamp);
        require(Array.isArray(source.records) && source.records.length <= 200);
        total += source.records.length;
        require(total <= 1000);
        truncation(source.truncation, source.records.length);
        if (source.status === 'complete') require(source.error === null && source.truncation.omitted === 0);
        if (source.status === 'partial') require(source.error !== null || source.truncation.omitted !== 0);
        if (source.status === 'unavailable') require(source.records.length === 0 && source.error !== null);
        const ids = new Set();
        for (const record of source.records) {
          validateRecord(record, source.owner, node.host);
          require(!ids.has(record.id));
          ids.add(record.id);
        }
      }
      require(node.status === combined(node.sources.map(source => source.status)));
    }
    truncation(fleet.truncation, total);
    const expected = combined(fleet.nodes.map(node => node.status));
    require(fleet.status === (expected === 'complete' && fleet.truncation.omitted !== 0 ? 'partial' : expected));
    return fleet;
  }
  function element(tag, value, className = '') {
    const item = document.createElement(tag);
    if (value !== undefined) item.textContent = String(value);
    if (className) item.className = className;
    return item;
  }
  function omission(value) {
    return `${value.returned} returned · ${value.omitted === null ? 'unknown omitted' : `${value.omitted} omitted`}${value.omitted !== 0 ? ' · truncated / incomplete' : ''}`;
  }
  function emptyMessage(value) {
    return value === 'complete' ? 'No matching workloads.' : value === 'unavailable' ? 'Workload evidence unavailable.' : 'Incomplete workload evidence; no matching records reported.';
  }
  function renderRecord(record) {
    const card = element('article', undefined, 'workload-record');
    const activity = record.observation_quality === 'stale' ? 'Stale' : record.state === 'terminal' ? 'Terminal' : active.has(record.state) ? 'Active' : 'Observed';
    card.append(element('h5', `${activity} · ${record.label}`));
    const detail = element('dl');
    const pairs = [
      ['State / phase', `${record.state} / ${record.phase}`],
      ['Outcome', record.outcome ?? 'Not reported'],
      ['ID', record.id], ['Created', record.created_at], ['Updated', record.updated_at],
      ['Source timestamp', record.source_timestamp], ['Authority', record.source_authority],
      ['Observation', record.observation_quality],
    ];
    if (record.progress) pairs.push(['Progress', `${record.progress.completed} / ${record.progress.total ?? 'unknown'} ${record.progress.unit}`]);
    for (const [label, value] of pairs) detail.append(element('dt', label), element('dd', value));
    card.append(detail);
    return card;
  }
  function render(fleet) {
    const nodes = [];
    for (const node of fleet.nodes) {
      const card = element('article', undefined, 'panel workload-node');
      card.append(element('h3', `${node.host} · ${node.status}`));
      card.append(element('div', `Node collected: ${node.collection_timestamp}`, 'workload-meta'));
      for (const source of node.sources) {
        const section = element('section', undefined, 'workload-source');
        section.append(element('h4', `${ownerKinds[source.owner]} · ${source.owner} · ${source.status}`));
        section.append(element('div', `Source collected: ${source.collection_timestamp} · ${omission(source.truncation)}`, 'workload-meta'));
        if (source.error) section.append(element('p', sourceErrors[source.error]));
        if (!source.records.length) section.append(element('p', emptyMessage(source.status), 'empty'));
        for (const record of source.records) section.append(renderRecord(record));
        card.append(section);
      }
      nodes.push(card);
    }
    if (!nodes.length) nodes.push(element('p', emptyMessage(fleet.status), 'empty'));
    results.replaceChildren(...nodes);
    status.textContent = `Fleet ${fleet.status} · collected ${fleet.collection_timestamp} · ${omission(fleet.truncation)}`;
  }
  function clear(message) {
    results.replaceChildren();
    status.textContent = message;
    disconnect.disabled = !credential;
  }
  function query() {
    const values = Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value]));
    require(values.owner === '' || Object.hasOwn(ownerKinds, values.owner));
    require(values.kind === '' || kinds.includes(values.kind));
    require(values.state === '' || states.includes(values.state));
    require(values.host === '' || (values.host.length <= 1024 && /^[A-Za-z][A-Za-z0-9_-]*$/.test(values.host)));
    require(['true', 'false'].includes(values.active_only));
    for (const [key, max] of [['recent_seconds', 86400], ['limit', 1000]]) {
      require(/^[0-9]{1,5}$/.test(values[key]) && Number(values[key]) >= 1 && Number(values[key]) <= max);
    }
    return new URLSearchParams(Object.entries(values).filter(([, value]) => value !== '')).toString();
  }
  function eligible() { return Boolean(credential) && visible && !document.hidden; }
  function schedule() {
    if (!eligible() || inFlight || timer !== null) return;
    timer = setTimeout(() => { timer = null; poll(); }, Math.max(0, nextAt - performance.now()));
  }
  function invalidate(message) {
    generation += 1;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (inFlight) inFlight.controller.abort();
    clear(message);
    schedule();
  }
  async function poll() {
    if (!eligible() || inFlight) return;
    let rawQuery;
    try { rawQuery = query(); } catch (_) { clear('Invalid workload filters. Check the selected values.'); return; }
    const request = {generation, controller: new AbortController()};
    inFlight = request;
    status.textContent = 'Reading workload evidence…';
    const deadline = setTimeout(() => {
      if (inFlight === request && generation === request.generation) {
        generation += 1;
        clear('Workload request timed out. Evidence is unavailable.');
      }
      request.controller.abort();
    }, 8000);
    try {
      const response = await fetch('/v1/workloads?' + rawQuery, {
        headers: {Authorization: 'Bearer ' + credential}, cache: 'no-store',
        credentials: 'omit', redirect: 'error', signal: request.controller.signal,
      });
      if (generation !== request.generation) return;
      if (response.status === 401 || response.status === 403) {
        credential = '';
        clear('Workload access denied. Connect with an authorized read credential.');
        return;
      }
      require(response.ok && response.status === 200);
      const raw = await response.text();
      if (generation !== request.generation) return;
      require(typeof raw === 'string' && raw.length <= MAX_BYTES && new TextEncoder().encode(raw).length <= MAX_BYTES);
      render(validateSnapshot(JSON.parse(raw)));
    } catch (_) {
      if (generation === request.generation) clear('Workload evidence unavailable. Check the connection and configuration.');
    } finally {
      clearTimeout(deadline);
      inFlight = null;
      nextAt = performance.now() + 5000;
      schedule();
    }
  }
  byId('workload-auth').addEventListener('submit', event => {
    event.preventDefault();
    const value = tokenInput.value;
    tokenInput.value = '';
    credential = /^[\x21-\x7e]{16,4096}$/.test(value) ? value : '';
    invalidate(credential ? 'Connected. Waiting for workload evidence…' : 'Enter a valid workload read credential.');
  });
  disconnect.addEventListener('click', () => {
    credential = '';
    tokenInput.value = '';
    invalidate('Disconnected. Connect with a workload read credential.');
  });
  for (const control of Object.values(controls)) {
    control.addEventListener('change', () => invalidate(credential ? 'Filters changed. Waiting for fresh workload evidence…' : 'Disconnected. Connect with a workload read credential.'));
  }
  function setVisible(value) {
    visible = value === true;
    invalidate(eligible() ? 'Waiting for fresh workload evidence…' : credential ? 'Workload polling paused while hidden.' : 'Disconnected. Connect with a workload read credential.');
  }
  document.addEventListener('visibilitychange', () => setVisible(visible));
  Object.defineProperty(window, 'AnvilWorkloads', {value: Object.freeze({setVisible}), writable: false, configurable: false});
})();
