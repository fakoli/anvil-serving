import { test, expect, chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { chmod, lstat, mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import * as http from 'node:http';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';

const dashHost = 'dash.example.test';
const authHost = 'auth.example.test';
const controlHost = 'control.example.test';
const connectRoot = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const defaultFixtureTest = 'pinned Caddy and Authelia browser edge retains Connect and native controls';
let fixture;

async function waitForExit(child, milliseconds) {
  if (!child) return { done: true, code: 0, signal: null };
  if (child.exitCode !== null || child.signalCode !== null) return { done: true, code: child.exitCode, signal: child.signalCode };
  return new Promise(resolve => {
    const finish = value => { clearTimeout(timer); child.off('exit', exited); child.off('error', errored); resolve(value); };
    const exited = (code, signal) => finish({ done: true, code, signal });
    const errored = error => finish({ done: true, error });
    const timer = setTimeout(() => finish({ done: false }), milliseconds);
    child.once('exit', exited); child.once('error', errored);
  });
}

function cleanExit(result) {
  return result.done && !result.error && result.code === 0 && result.signal === null;
}

function fixtureMarker(line) {
  const match = /\b(browser_(?:edge|runtime)_fixture_test\.go):(\d+):/.exec(line);
  return match ? `${match[1]}:${match[2]}` : '';
}

async function stopChild(child) {
  if (!child) return;
  // A live fixture may stop only through its stdin EOF: Go then runs its
  // Caddy/Authelia cleanups. An early exit, signal escalation, or nonzero
  // status is a fixture failure even when its processes have been reaped.
  if (child.exitCode !== null || child.signalCode !== null) {
    throw new Error(`owned edge fixture exited before cleanup (${child.exitCode ?? child.signalCode})`);
  }
  if (child.stdin && !child.stdin.destroyed) child.stdin.end();
  const afterEOF = await waitForExit(child, 8_000);
  if (cleanExit(afterEOF)) return;
  if (!afterEOF.done) {
    child.kill('SIGTERM');
    const afterTerm = await waitForExit(child, 3_000);
    if (!afterTerm.done) {
      child.kill('SIGKILL');
      await waitForExit(child, 3_000);
    }
  }
  throw new Error(`owned edge fixture did not cleanly exit after EOF (${afterEOF.error ? 'spawn error' : afterEOF.code ?? afterEOF.signal ?? 'timeout'})`);
}

function run(command, args, timeout = 10_000, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options });
    let output = '';
    const append = data => { output = (output + data).slice(-8192); };
    child.stdout.on('data', append); child.stderr.on('data', append);
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(`${command} timed out`)); }, timeout);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('exit', code => {
      clearTimeout(timer);
      code === 0 ? resolve() : reject(new Error(`${command} exited (${code}): ${output}`));
    });
  });
}

async function buildDeviceCLI(directory) {
  const binary = join(directory, 'anvil-connect');
  await run(process.env.ANVIL_CONNECT_GO || 'go', ['build', '-o', binary, './cmd/anvil-connect'], 60_000, { cwd: connectRoot });
  return binary;
}

async function startDeviceCLI(binary, value) {
  const trustDirectory = await mkdtemp(join(value.dir, 'cli-empty-ca-'));
  const env = {
    PATH: process.env.PATH,
    HOME: value.client_home,
    XDG_CONFIG_HOME: join(value.client_home, '.config'),
    XDG_CACHE_HOME: join(value.client_home, '.cache'),
    XDG_DATA_HOME: join(value.client_home, '.data'),
    SSL_CERT_FILE: value.ca,
    SSL_CERT_DIR: trustDirectory,
  };
  const child = spawn(binary, ['login', '--json'], { cwd: connectRoot, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stderr.resume();
  const lines = readline.createInterface({ input: child.stdout });
  let settleChallenge;
  let settleReady;
  const challenge = new Promise((resolve, reject) => { settleChallenge = { resolve, reject }; });
  const ready = new Promise((resolve, reject) => { settleReady = { resolve, reject }; });
  // Either wait is consumed by the test, but startup failure can occur before
  // it reaches the second one. Mark both rejections handled without retaining
  // the child diagnostic stream.
  void challenge.catch(() => {});
  void ready.catch(() => {});
  const fail = () => {
    settleChallenge.reject(new Error('device-cli-exited'));
    settleReady.reject(new Error('device-cli-exited'));
  };
  child.once('error', fail);
  child.once('exit', fail);
  lines.on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (message.verification_uri && message.user_code) {
      settleChallenge.resolve({ verificationURI: message.verification_uri, userCode: message.user_code });
    }
    if (message.status === 'running' && message.local_base_url && message.session_id && message.expires_at) {
      settleReady.resolve({ baseURL: message.local_base_url, sessionID: message.session_id, expiresAt: message.expires_at });
    }
  });
  return { child, challenge, ready };
}

async function waitForDeviceEvent(promise) {
  const error = new Error('device-cli-event-timeout');
  Error.captureStackTrace(error, waitForDeviceEvent);
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(error), 15_000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function stopDeviceCLI(value) {
  if (value?.child?.exitCode === null && value.child.signalCode === null) value.child.kill('SIGINT');
  const result = await waitForExit(value?.child, 8_000);
  if (!cleanExit(result)) throw new Error('device-cli-did-not-stop-cleanly');
}

async function loopbackResponse(baseURL, method, key) {
  const headers = key ? { Authorization: `Bearer ${key}` } : {};
  return fetch(`${baseURL}/models`, { method, headers, redirect: 'error' });
}

async function assertLoopbackReleased(baseURL) {
  const url = new URL(baseURL);
  const port = Number(url.port);
  await new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once('error', reject);
    listener.listen({ host: '127.0.0.1', port }, () => listener.close(error => error ? reject(error) : resolve()));
  });
}

function resolverRules(resolver) {
  return `--host-resolver-rules=MAP ${dashHost}:443 ${resolver},MAP ${authHost}:443 ${resolver}`;
}

function browserEnvironment(home) {
  return { ...process.env, HOME: home, XDG_CONFIG_HOME: join(home, 'config'), XDG_CACHE_HOME: join(home, 'cache'), XDG_DATA_HOME: join(home, 'data') };
}

async function verifyUntrustedCertificate(resolver, home) {
  await mkdir(home, { mode: 0o700 });
  const browser = await chromium.launch({ executablePath: process.env.ANVIL_CONNECT_CHROMIUM || '/usr/bin/google-chrome', headless: true, timeout: 10_000, env: browserEnvironment(home), args: ['--no-proxy-server', '--disable-quic', '--disable-gpu', resolverRules(resolver)] });
  try {
    const page = await browser.newPage();
    let rejected = false;
    try {
      await page.goto(`https://${dashHost}`, { waitUntil: 'commit', timeout: 10_000 });
    } catch (error) {
      const detail = String(error);
      if (/ERR_CERT|certificate/i.test(detail)) {
        rejected = true;
      } else {
        const networkClass = ['net::ERR_CONNECTION_REFUSED', 'net::ERR_CONNECTION_CLOSED', 'net::ERR_NAME_NOT_RESOLVED', 'net::ERR_CONNECTION_TIMED_OUT'].find(value => detail.includes(value));
        throw new Error(networkClass || 'browser-navigation-failed');
      }
    }
    if (!rejected) throw new Error('unknown fixture certificate was accepted by an untrusted Chromium child');
  } finally {
    await browser.close();
  }
}

async function removePrivateRoot(path) {
  if (!path) return;
  await rm(path, { recursive: true, force: true });
  try {
    await lstat(path);
  } catch (error) {
    if (error?.code === 'ENOENT') return;
  }
  throw new Error('fixture-private-temp-cleanup-failed');
}

async function cleanup(value) {
  const outcomes = await Promise.allSettled([value.context?.close(), stopChild(value.child)]);
  const removed = await Promise.allSettled([removePrivateRoot(value.dir), removePrivateRoot(value.fixtureTmp)]);
  const failed = outcomes.find(result => result.status === 'rejected') || removed.find(result => result.status === 'rejected');
  if (failed) throw failed.reason;
}

async function startFixture(testPattern = '^TestBrowserEdgeFixture$', fixtureFlag = 'ANVIL_CONNECT_BROWSER_EDGE_FIXTURE') {
  // Go's t.TempDir adds the test name below TMPDIR and the fixture binds a
  // Unix socket there. Qualification artifact paths are intentionally long, so
  // use a separate short, private, runner-owned root only for fixture children.
  const fixtureTmp = await mkdtemp(join(tmpdir(), 'ace-'));
  await chmod(fixtureTmp, 0o700);
  const value = { dir: await mkdtemp(join(tmpdir(), 'ace-')), fixtureTmp };
  try {
    const binary = join(value.dir, 'fixture.test');
    await new Promise((resolve, reject) => {
      const build = value.build = spawn(process.env.ANVIL_CONNECT_GO || 'go', ['test', '-c', '-o', binary, './test'], { cwd: connectRoot, stdio: ['ignore', 'pipe', 'pipe'] });
      let output = '';
      build.stderr.on('data', data => { output = (output + data).slice(-16384); });
      const timer = setTimeout(() => { build.kill('SIGKILL'); reject(new Error('actual-edge fixture build timed out')); }, 60_000);
      build.on('error', error => { clearTimeout(timer); reject(error); });
      build.on('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error('fixture-build-failed')); });
    });
    const child = value.child = spawn(binary, ['-test.run', testPattern, '-test.v'], {
      env: { PATH: process.env.PATH, TMPDIR: value.fixtureTmp, [fixtureFlag]: '1', ANVIL_CONNECT_EDGE_CADDY: process.env.ANVIL_CONNECT_EDGE_CADDY || '/data/cache/anvil-connect/edge-tools/extract/caddy/caddy', ANVIL_CONNECT_EDGE_AUTHELIA: process.env.ANVIL_CONNECT_EDGE_AUTHELIA || '/data/cache/anvil-connect/edge-tools/extract/authelia/authelia', ANVIL_CONNECT_WSTUNNEL: process.env.ANVIL_CONNECT_WSTUNNEL || '/data/cache/anvil-connect/tools/wstunnel/10.7.1/linux-amd64/wstunnel' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const replies = [];
    let fixtureDiagnostic = '';
    const ready = await new Promise((resolve, reject) => {
      child.stderr.resume();
      const lines = readline.createInterface({ input: child.stdout });
      const timer = setTimeout(() => reject(new Error('actual-edge fixture did not become ready')), 45_000);
      child.on('error', error => { clearTimeout(timer); reject(error); });
      child.on('exit', code => { clearTimeout(timer); lines.close(); reject(new Error(`actual-edge fixture exited (${code})${fixtureDiagnostic ? `: ${fixtureDiagnostic}` : ''}`)); });
      lines.on('line', line => {
        // Go test failures here are fixed fixture-stage labels only. Do not
        // retain OIDC values or fixture JSON in diagnostics.
        if (!fixtureDiagnostic) fixtureDiagnostic = fixtureMarker(line);
        try {
          const message = JSON.parse(line);
          if (message.url) { clearTimeout(timer); resolve(message); return; }
          if (message.ack) { const next = replies.shift(); if (next) next(message); }
        } catch {}
      });
    });
    await verifyUntrustedCertificate(ready.resolver, join(value.dir, 'untrusted-home'));
    // Chromium on Linux reads the shared NSS database at $HOME/.pki/nssdb;
    // a profile-local database is deliberately not treated as trust input.
    const home = join(value.dir, 'trusted-home');
    const nssdb = join(home, '.pki', 'nssdb');
    const profile = join(value.dir, 'chromium-profile');
    await mkdir(nssdb, { recursive: true, mode: 0o700 });
    await mkdir(profile, { mode: 0o700 });
    await run('certutil', ['-N', '-d', `sql:${nssdb}`, '--empty-password']);
    await run('certutil', ['-A', '-d', `sql:${nssdb}`, '-n', 'AnvilConnectFixtureCA', '-t', 'C,,', '-i', ready.ca]);
    value.context = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.ANVIL_CONNECT_CHROMIUM || '/usr/bin/google-chrome',
      headless: true, timeout: 15_000, env: browserEnvironment(home),
      args: ['--no-proxy-server', '--disable-quic', '--disable-gpu', resolverRules(ready.resolver)],
    });
    return {
      ...value, ...ready,
      command(command, timeout = 5_000) {
        const timeoutError = new Error('actual-edge fixture command timed out');
        Error.captureStackTrace(timeoutError, this.command);
        const caller = timeoutError.stack.match(/browser_edge\.spec\.mjs:[1-9][0-9]{0,4}/)?.[0];
        const annotations = test.info().annotations;
        const diagnostic = caller ? { type: 'diagnostic-location', description: caller } : null;
        if (caller) {
          annotations.push(diagnostic);
          timeoutError.message = caller;
          timeoutError.stack = caller;
        }
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(timeoutError), timeout);
          replies.push(message => {
            clearTimeout(timer);
            if (message.ack !== command) reject(new Error('unexpected actual-edge acknowledgement'));
            else if (message.error) reject(new Error('fixture-command-failed'));
            else {
              const index = annotations.indexOf(diagnostic);
              if (index >= 0) annotations.splice(index, 1);
              resolve(message);
            }
          });
          child.stdin.write(`${command}\n`);
        });
      },
    };
  } catch (error) {
    try { await cleanup(value); } catch {}
    // Startup failure is the actionable owner; cleanup may only add a later
    // consequence after a fixture has already exited.
    throw error;
  }
}

async function freshPage(cookieFilter) {
  await fixture.context.clearCookies(cookieFilter);
  for (const page of fixture.context.pages()) await page.close();
  return fixture.context.newPage();
}

async function login(page, identity, expectedCallbackStatus = 303) {
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await page.getByLabel(/username/i).fill(fixture[`${identity}_user`]);
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill(fixture[`${identity}_password`]);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const digits = page.getByRole('textbox', { name: /Please enter verification code|Digit [1-6]/i });
  const accept = page.getByRole('button', { name: 'Accept', exact: true });
  const alert = page.getByRole('alert');
  await expect(digits).toHaveCount(6);
  const callback = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/callback`));
  const enterCode = async () => {
    const { code } = await fixture.command(`totp ${identity}`, 10_000);
    for (const [index, digit] of [...code].entries()) await digits.nth(index).fill(digit);
  };
  const nextTransition = includeAlert => Promise.race([
    callback.then(response => ({ kind: 'callback', response })),
    accept.waitFor({ state: 'visible', timeout: 5_000 }).then(() => ({ kind: 'consent' })).catch(() => null),
    ...(includeAlert ? [alert.waitFor({ state: 'visible', timeout: 5_000 }).then(() => ({ kind: 'retry' })).catch(() => null)] : []),
  ]);
  await enterCode();
  let next = await nextTransition(true);
  if (next?.kind === 'retry') {
    // The provider explicitly rejected a boundary code; request one aligned to
    // the next valid interval and wait only for the resulting auth transition.
    await enterCode();
    next = await nextTransition(false);
  }
  if (!next) throw new Error('Authelia did not finish the stable TOTP transition');
  if (next.kind === 'consent') await accept.click();
  const callbackResponse = next.kind === 'callback' ? next.response : await callback;
  expect(new URL(callbackResponse.url()).searchParams.get('scope')).toBe('openid');
  expect(new URL(callbackResponse.url()).searchParams.get('iss')).toBe(`https://${authHost}`);
  expect(callbackResponse.status()).toBe(expectedCallbackStatus);
  if (expectedCallbackStatus === 303) await expect(page.locator('#dashboard')).toHaveText('native dashboard');
  else await page.waitForURL(callbackResponse.url(), { waitUntil: 'domcontentloaded', timeout: 10_000 });
}

async function resumeGrantedAuthorization(page) {
  const callback = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/callback`));
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  const accept = page.getByRole('button', { name: 'Accept', exact: true });
  const next = await Promise.race([
    callback.then(response => ({ response })),
    accept.waitFor({ state: 'visible', timeout: 5_000 }).then(() => ({ response: null })).catch(() => null),
  ]);
  if (!next) throw new Error('Authelia did not resume the granted authorization');
  if (next.response === null) await accept.click();
  const callbackResponse = next.response || await callback;
  expect(new URL(callbackResponse.url()).searchParams.get('scope')).toBe('openid');
  expect(new URL(callbackResponse.url()).searchParams.get('iss')).toBe(`https://${authHost}`);
  expect(callbackResponse.status()).toBe(303);
  // The callback response precedes the browser's final redirect. Wait for
  // that navigation before observing authenticated connector readiness.
  await page.waitForURL(fixture.url, { waitUntil: 'domcontentloaded' });
  // Connector enrollment returns before its reverse tunnel proves ready. Poll
  // the authenticated gateway response itself, then navigate; this is bounded
  // readiness observation rather than an arbitrary delay.
  await expect.poll(() => page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status)), { timeout: 10_000 }).toBe(200);
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#dashboard')).toHaveText('native dashboard');
}

const browserStreamCountFields = ['events_started', 'events_closed', 'ws_started', 'ws_closed'];

async function browserStreamCounts(timeout = 5_000) {
  const counts = await fixture.command('browser stream counts', timeout);
  expect(Object.keys(counts).sort()).toEqual(['ack', ...browserStreamCountFields].sort());
  expect(counts.ack).toBe('browser stream counts');
  for (const field of browserStreamCountFields) expect(counts[field]).toMatch(/^\d+$/);
  return counts;
}

async function openBrowserStreams(page) {
  await page.evaluate(async () => {
    const events = (async () => {
      const response = await fetch('/events', { redirect: 'error' });
      if (response.status !== 200 || !response.body) throw new Error('browser-stream-events-open-failed');
      const reader = response.body.getReader();
      window.__browserStreamReader = reader;
      const decoder = new TextDecoder();
      let marker = '';
      while (!marker.includes('data: ready\n\n') && marker.length < 64) {
        const first = await reader.read();
        if (first.done || !first.value?.byteLength) throw new Error('browser-stream-events-first-data-failed');
        marker += decoder.decode(first.value, { stream: true });
      }
      if (!marker.includes('data: ready\n\n')) throw new Error('browser-stream-events-marker-failed');
    })();
    const websocket = new Promise((resolve, reject) => {
      const socket = new WebSocket(`wss://${location.host}/ws`);
      window.__browserStreamSocket = socket;
      socket.addEventListener('open', () => resolve(), { once: true });
      socket.addEventListener('error', () => reject(new Error('browser-stream-websocket-open-failed')), { once: true });
    });
    await Promise.all([events, websocket]);
  });
}

function closedBrowserStreams(page) {
  return page.evaluate(() => Promise.all([
    window.__browserStreamReader.closed.then(() => true, () => true),
    new Promise(resolve => {
      const socket = window.__browserStreamSocket;
      if (socket.readyState === WebSocket.CLOSED) {
        resolve(true);
        return;
      }
      socket.addEventListener('close', () => resolve(true), { once: true });
    }),
  ]).then(() => true, () => false)).catch(() => false);
}

async function expectBrowserStreamClosure(started, clientClosed) {
  const withinBound = action => {
    const remaining = 1_000 - (performance.now() - started);
    if (remaining <= 0) throw new Error('browser-stream-closure-exceeded-bound');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('browser-stream-closure-exceeded-bound')), remaining);
      Promise.resolve().then(action).then(
        value => { clearTimeout(timer); resolve(value); },
        error => { clearTimeout(timer); reject(error); },
      );
    });
  };
  expect(await withinBound(async () => clientClosed)).toBe(true);
  await withinBound(async () => {
    await expect.poll(async () => {
      const remaining = Math.max(1, 1_000 - (performance.now() - started));
      const counts = await browserStreamCounts(remaining);
      return browserStreamCountFields.map(field => counts[field]).join(',');
    }, { timeout: Math.max(1, 1_000 - (performance.now() - started)), intervals: [20, 50] }).toBe('1,1,1,1');
  });
  const closureMs = Math.ceil(performance.now() - started);
  expect(closureMs).toBeLessThanOrEqual(1_000);
  return closureMs;
}

async function expectFreshBrowserStreamsDenied(page) {
  expect(await page.evaluate(() => fetch('/events', { redirect: 'manual' }).then(response => response.status, () => 0))).toBe(401);
  const websocket = await page.evaluate(() => new Promise(resolve => {
    const socket = new WebSocket(`wss://${location.host}/ws`);
    const timer = setTimeout(() => resolve('timeout'), 5_000);
    const finish = result => { clearTimeout(timer); resolve(result); };
    socket.addEventListener('open', () => finish('opened'), { once: true });
    socket.addEventListener('error', () => finish('failed'), { once: true });
    socket.addEventListener('close', () => finish('failed'), { once: true });
  }));
  expect(websocket).toBe('failed');
}

// The runtime and device cases own a separate fixture stack. Starting the
// simple edge fixture globally would leave an unused Caddy/Authelia pair alive
// for those cases and exhaust the deliberately small container PID budget.
test.beforeEach(async ({}, testInfo) => {
  if (testInfo.title === defaultFixtureTest) fixture = await startFixture();
});
test.afterEach(async ({}, testInfo) => {
  if (testInfo.title !== defaultFixtureTest || !fixture) return;
  const defaultFixture = fixture;
  fixture = undefined;
  await cleanup(defaultFixture);
});

test(defaultFixtureTest, async () => {
  // Authelia intentionally issues an opaque public UUID for the file user.
  // The first real OIDC response proves Connect default-denies an ungranted
  // subject; the fixture then grants the exported opaque subject exactly.
  let page = await freshPage();
  await login(page, 'allowed', 401);
  await fixture.command('grant allowed');
  await resumeGrantedAuthorization(page);
  await expect(page.locator('#dashboard')).toHaveText('native dashboard');
  const cookies = await fixture.context.cookies(fixture.url);
  for (const name of ['__Host-anvil-connect', 'native_session']) {
    const cookie = cookies.find(value => value.name === name);
    expect(cookie, `${name} cookie`).toBeTruthy();
    expect(cookie.secure).toBe(true);
    expect(cookie.domain).toBe(dashHost);
  }
  const siblingCookies = await fixture.context.cookies(`https://${authHost}`);
  for (const name of ['__Host-anvil-connect', 'native_session']) expect(siblingCookies.some(cookie => cookie.name === name), `${name} is host-only`).toBe(false);
  expect(await page.evaluate(() => fetch('/native-grant', { method: 'POST', headers: { 'X-CSRF-Token': 'fixture-csrf' } }).then(response => response.status))).toBe(204);

  expect(await page.evaluate(() => fetch('/fixture-native-guard', { method: 'POST' }).then(response => ({ status: response.status, native: response.headers.get('X-Native-Guard') })))).toEqual({ status: 403, native: 'reached' });
  await fixture.command('mode native-bypass');
  expect(await page.evaluate(() => fetch('/fixture-native-guard', { method: 'POST' }).then(response => ({ status: response.status, native: response.headers.get('X-Native-Guard') })))).toEqual({ status: 204, native: 'reached' });

  await page.evaluate(() => new Promise((resolve, reject) => { const stream = new EventSource('/events'); stream.onmessage = () => { window.__fixtureStream = stream; resolve(); }; stream.onerror = reject; }));
  await page.evaluate(() => new Promise((resolve, reject) => { const socket = new WebSocket(`wss://${location.host}/ws`); socket.onopen = resolve; socket.onerror = reject; window.__fixtureSocket = socket; }));
  const closed = page.evaluate(() => new Promise(resolve => { window.__fixtureSocket.onclose = () => resolve('closed'); }));
  const streamClosed = page.evaluate(() => new Promise(resolve => { window.__fixtureStream.onerror = () => resolve('closed'); }));
  await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
  await expect.poll(() => closed).toBe('closed');
  await expect.poll(() => streamClosed).toBe('closed');
  expect(await page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status))).toBe(401);

  page = await freshPage();
  await fixture.context.setExtraHTTPHeaders({ Authorization: 'Bearer browser-api-key-must-not-bypass-connect' });
  await login(page, 'denied', 401);
  await expect(page.locator('body')).toContainText('Unauthorized');
  await fixture.context.setExtraHTTPHeaders({});

  await fixture.command('mode session-bypass');
  page = await freshPage();
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#dashboard')).toHaveText('native dashboard');
  await fixture.command('mode session-enforce');

});


test('managed gateway and connector lifecycle retain the real browser edge', async () => {
  const edgeFixture = fixture;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_RUNTIME_EDGE_FIXTURE');
  try {
    const page = await freshPage();
    // The first authentic OIDC result has no Connect human grant. Granting the
    // provider-issued opaque subject occurs only through native admin.sock.
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);
    await expect(page.locator('#dashboard')).toHaveText('native dashboard');
    expect(await page.evaluate(() => fetch('/native-grant', { method: 'POST', headers: { 'X-CSRF-Token': 'fixture-csrf' } }).then(response => response.status))).toBe(204);

    // The declared browser resource allows two concurrent requests. Two live
    // native SSE streams consume those slots; the third request proves the
    // configured admission bound (not a whole-process RSS limit) at the edge.
    await page.evaluate(() => Promise.all([0, 1].map(index => new Promise((resolve, reject) => {
      const stream = new EventSource('/events');
      stream.onmessage = () => { window.__runtimeStreams ??= []; window.__runtimeStreams[index] = stream; resolve(); };
      stream.onerror = reject;
    }))));
    expect(await page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status))).toBe(429);
    const streamsClosed = page.evaluate(() => Promise.all(window.__runtimeStreams.map(stream => new Promise(resolve => { stream.onerror = () => resolve('closed'); }))));
    const logoutResponse = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/logout`) && response.request().method() === 'POST');
    // Browser fetch intentionally exposes a manual redirect as opaque status 0;
    // Playwright observes the actual same-origin HTTP response for this check.
    await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
    expect((await logoutResponse).status()).toBe(303);
    // IdleSeconds is five; closure inside two seconds ties this result to the
    // explicit session revocation instead of the natural stream idle timeout.
    await expect.poll(() => streamsClosed, { timeout: 2_000 }).toEqual(['closed', 'closed']);
    expect(await page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status))).toBe(401);
  } finally {
    const runtimeFixture = fixture;
    fixture = edgeFixture;
    await cleanup(runtimeFixture);
  }
});

test('container-gated CLI device login reaches only its declared API resource', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  const edgeFixture = fixture;
  let loginSession;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE');
  try {
    // The ordinary browser admission remains real: the fixture grants only the
    // opaque subject returned by Authelia, then the device form binds its
    // one-time code to that authenticated browser session.
    const page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    const cli = await buildDeviceCLI(fixture.dir);
    loginSession = await startDeviceCLI(cli, fixture);
    const challenge = await waitForDeviceEvent(loginSession.challenge);
    expect(challenge.verificationURI).toBe(`https://${dashHost}/_anvil-connect/device`);
    expect(challenge.userCode).toMatch(/^[A-Z2-7]{8}$/);

    await page.goto(challenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Approve device' })).toBeVisible();
    await page.getByLabel('Code').fill(challenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect(page.locator('body')).toContainText('Device decision recorded');

    const ready = await waitForDeviceEvent(loginSession.ready);
    expect(ready.baseURL).toBe(fixture.local_base_url);
    expect(ready.sessionID).toMatch(/^[0-9a-f]{32}$/);
    expect(Number.isNaN(Date.parse(ready.expiresAt))).toBe(false);

    // The local credential remains an independent boundary after browser
    // approval. It is loaded only from the owner-only sibling fixture file
    // and never placed in an argv, test message, or retained artifact.
    expect((await loopbackResponse(ready.baseURL, 'GET')).status).toBe(401);
    expect((await loopbackResponse(ready.baseURL, 'GET', 'acl1.invalid')).status).toBe(401);
    const localKey = (await readFile(fixture.local_key, 'utf8')).trim();
    const models = await loopbackResponse(ready.baseURL, 'GET', localKey);
    expect(models.status).toBe(200);
    expect((await models.json()).data).toEqual([{ id: 'fixture-model' }]);
    // The local rule permits POST structurally, but this device credential was
    // issued for GET only. The remote API authority must still reject it.
    expect((await loopbackResponse(ready.baseURL, 'POST', localKey)).status).toBe(401);
    expect((await fixture.command('api post count')).count).toBe('0');

    await stopDeviceCLI(loginSession);
    loginSession = undefined;
    await assertLoopbackReleased(ready.baseURL);
  } finally {
    try { await stopDeviceCLI(loginSession); } finally {
      const deviceFixture = fixture;
      fixture = edgeFixture;
      await cleanup(deviceFixture);
    }
  }
});

test('container-gated CLI device login denies, cancels, and rejects unauthenticated approval', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  const edgeFixture = fixture;
  let deniedLogin;
  let cancelledLogin;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE');
  try {
    const cli = await buildDeviceCLI(fixture.dir);
    deniedLogin = await startDeviceCLI(cli, fixture);
    const deniedChallenge = await waitForDeviceEvent(deniedLogin.challenge);

    // A device code is not browser authority. With no Connect session, the
    // reserved approval path must enter the ordinary OIDC login flow instead
    // of rendering an approval form.
    let page = await freshPage();
    await page.goto(deniedChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await expect(page.getByLabel(/username/i)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Approve device' })).toHaveCount(0);
    // GET /start is reserved but intentionally rejects without an active
    // session. Its dash-origin error document lets this browser make the
    // actual same-origin form POST below without manufacturing an Origin.
    expect((await page.goto(`${deniedChallenge.verificationURI}/start`, { waitUntil: 'domcontentloaded' })).status()).toBe(403);
    const unauthenticated = page.waitForResponse(response => response.url() === deniedChallenge.verificationURI && response.request().method() === 'POST', { timeout: 5_000 });
    await page.evaluate(({ uri, userCode }) => fetch(uri, {
      method: 'POST',
      signal: AbortSignal.timeout(5_000),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ user_code: userCode, decision: 'approve', csrf: '0'.repeat(64) }).toString(),
    }), { uri: deniedChallenge.verificationURI, userCode: deniedChallenge.userCode });
    expect((await unauthenticated).status()).toBe(401);

    // Establish a real browser session only after that denial, then prove a
    // forged same-origin CSRF field cannot make a decision.
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);
    await page.goto(deniedChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Approve device' })).toBeVisible();
    const forged = page.waitForResponse(response => response.url() === deniedChallenge.verificationURI && response.request().method() === 'POST', { timeout: 5_000 });
    await page.getByLabel('Code').fill(deniedChallenge.userCode);
    await page.locator('input[name=csrf]').evaluate(input => { input.value = '0'.repeat(64); });
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    expect((await forged).status()).toBe(400);

    // A dashboard session cookie alone does not make a cross-origin request a
    // valid approval. Submit a real foreign-origin form; fetch response events
    // can be hidden by CORS before Playwright observes the server rejection.
    const foreign = await fixture.context.newPage();
    try {
      // Authelia's own document intentionally has a restrictive connect-src
      // policy. The fixture control host is TLS-covered but has no browser
      // CSP, so this remains a normal browser-originated foreign request.
      await foreign.goto(`https://${controlHost}/`, { waitUntil: 'domcontentloaded', timeout: 10_000 });
      const foreignResponse = foreign.waitForResponse(response => response.url() === deniedChallenge.verificationURI && response.request().method() === 'POST', { timeout: 5_000 });
      await foreign.evaluate(({ uri, userCode }) => {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = uri;
        for (const [name, value] of Object.entries({ user_code: userCode, decision: 'approve', csrf: '0'.repeat(64) })) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = name;
          input.value = value;
          form.append(input);
        }
        document.body.append(form);
        form.requestSubmit();
      }, { uri: deniedChallenge.verificationURI, userCode: deniedChallenge.userCode });
      const rejected = await foreignResponse;
      expect(rejected.request().headers().origin).toBe(`https://${controlHost}`);
      expect(rejected.request().headers()['content-type']).toBe('application/x-www-form-urlencoded');
      expect(rejected.status()).toBe(403);
      await foreign.waitForURL(deniedChallenge.verificationURI, { waitUntil: 'domcontentloaded', timeout: 5_000 });
    } finally {
      await foreign.close();
    }

    // The actual authenticated operator denies this exact pending request.
    await page.goto(deniedChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    const denied = page.waitForResponse(response => response.url() === deniedChallenge.verificationURI && response.request().method() === 'POST', { timeout: 5_000 });
    await page.getByLabel('Code').fill(deniedChallenge.userCode);
    await page.getByRole('button', { name: 'Deny', exact: true }).click();
    expect((await denied).status()).toBe(200);
    const deniedExit = await waitForExit(deniedLogin.child, 10_000);
    expect(deniedExit).toEqual({ done: true, code: 1, signal: null });
    await expect(deniedLogin.ready).rejects.toThrow('device-cli-exited');
    deniedLogin = undefined;
    await assertLoopbackReleased(fixture.local_base_url);

    // The terminal can cancel before the browser makes any decision. Its
    // best-effort cancel request is completed before the CLI exits, so a later
    // authenticated approval is denied and cannot create a listener.
    cancelledLogin = await startDeviceCLI(cli, fixture);
    const cancelledChallenge = await waitForDeviceEvent(cancelledLogin.challenge);
    await stopDeviceCLI(cancelledLogin);
    await expect(cancelledLogin.ready).rejects.toThrow('device-cli-exited');
    cancelledLogin = undefined;
    await assertLoopbackReleased(fixture.local_base_url);
    await page.goto(cancelledChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    const cancelled = page.waitForResponse(response => response.url() === cancelledChallenge.verificationURI && response.request().method() === 'POST', { timeout: 5_000 });
    await page.getByLabel('Code').fill(cancelledChallenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    expect((await cancelled).status()).toBe(401);
  } finally {
    try {
      await stopDeviceCLI(deniedLogin);
      await stopDeviceCLI(cancelledLogin);
    } finally {
      const deviceFixture = fixture;
      fixture = edgeFixture;
      await cleanup(deviceFixture);
    }
  }
});

test('container-gated CLI device login revokes on human disable and browser logout', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  const edgeFixture = fixture;
  let firstLogin;
  let secondLogin;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE');
  try {
    const cli = await buildDeviceCLI(fixture.dir);
    let page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    firstLogin = await startDeviceCLI(cli, fixture);
    const firstChallenge = await waitForDeviceEvent(firstLogin.challenge);
    await page.goto(firstChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(firstChallenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const firstReady = await waitForDeviceEvent(firstLogin.ready);
    const firstKey = (await readFile(fixture.local_key, 'utf8')).trim();
    expect((await loopbackResponse(firstReady.baseURL, 'GET', firstKey)).status).toBe(200);
    expect((await fixture.command('api request count')).count).toBe('1');

    // human-set is the closed native authority path. Changing its generation
    // invalidates the browser session and the device-derived API credential.
    await fixture.command('disable allowed');
    expect((await loopbackResponse(firstReady.baseURL, 'GET', firstKey)).status).toBe(401);
    expect((await fixture.command('api request count')).count).toBe('1');
    await stopDeviceCLI(firstLogin);
    firstLogin = undefined;
    await assertLoopbackReleased(firstReady.baseURL);

    // Re-enable and create a fresh Connect session through ordinary OIDC SSO.
    // Retain the IdP session; repeating TOTP is a separate authentication test.
    await fixture.command('grant allowed');
    page = await freshPage({ domain: dashHost });
    await resumeGrantedAuthorization(page);
    secondLogin = await startDeviceCLI(cli, fixture);
    const secondChallenge = await waitForDeviceEvent(secondLogin.challenge);
    await page.goto(secondChallenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(secondChallenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const secondReady = await waitForDeviceEvent(secondLogin.ready);
    expect(secondReady.sessionID).not.toBe(firstReady.sessionID);
    const secondKey = (await readFile(fixture.local_key, 'utf8')).trim();
    expect((await loopbackResponse(secondReady.baseURL, 'GET', secondKey)).status).toBe(200);
    expect((await fixture.command('api request count')).count).toBe('2');

    // A real top-level browser logout revokes the admitted source session.
    // The existing local forwarder remains up, but its next keyed request is
    // denied before the connector's API origin sees it.
    const loggedOut = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/logout`) && response.request().method() === 'POST');
    await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
    expect((await loggedOut).status()).toBe(303);
    expect((await loopbackResponse(secondReady.baseURL, 'GET', secondKey)).status).toBe(401);
    expect((await fixture.command('api request count')).count).toBe('2');
    await stopDeviceCLI(secondLogin);
    secondLogin = undefined;
    await assertLoopbackReleased(secondReady.baseURL);
  } finally {
    try {
      await stopDeviceCLI(firstLogin);
      await stopDeviceCLI(secondLogin);
    } finally {
      const deviceFixture = fixture;
      fixture = edgeFixture;
      await cleanup(deviceFixture);
    }
  }
});

async function exerciseBrowserStreamRevocation(mutate) {
  const edgeFixture = fixture;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE');
  try {
    const page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    await openBrowserStreams(page);
    expect(await browserStreamCounts()).toMatchObject({
      events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
    });
    const clientClosed = closedBrowserStreams(page);
    const started = performance.now();
    await mutate(page);
    const closureMs = await expectBrowserStreamClosure(started, clientClosed);
    test.info().annotations.push({ type: 'closure_ms', description: String(closureMs) });

    const beforeDenied = await browserStreamCounts();
    await expectFreshBrowserStreamsDenied(page);
    expect(await browserStreamCounts()).toEqual(beforeDenied);
  } finally {
    const deviceFixture = fixture;
    fixture = edgeFixture;
    await cleanup(deviceFixture);
  }
}

test('container-gated browser streams close on human disable', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  await exerciseBrowserStreamRevocation(async () => {
    await fixture.command('disable allowed');
  });
});

test('container-gated browser streams close on logout', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  await exerciseBrowserStreamRevocation(async page => {
    const logout = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/logout`) && response.request().method() === 'POST').catch(() => null);
    await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
    const response = await logout;
    expect(response).not.toBeNull();
    expect(response.status()).toBe(303);
  });
});

const apiStreamCountFields = ['events_started', 'events_closed', 'ws_started', 'ws_closed'];
const localKeyPattern = /^acl1\.[A-Za-z0-9_-]{43}$/;

// The local forwarder defines acl1. + raw-base64url(32 bytes). Decode and
// re-encode rather than treating a shape-only string as a usable local key.
function validFixtureLocalKey(value) {
  if (!localKeyPattern.test(value)) return false;
  try {
    const encoded = value.slice('acl1.'.length);
    return Buffer.from(encoded, 'base64url').length === 32 && Buffer.from(encoded, 'base64url').toString('base64url') === encoded;
  } catch {
    return false;
  }
}

function loopbackStreamOptions(baseURL, path, localKey) {
  let base;
  try {
    base = new URL(baseURL);
  } catch {
    throw new Error('invalid-loopback-stream-configuration');
  }
  const port = Number(base.port);
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || base.username || base.password
    || base.pathname !== '/v1' || base.search || base.hash || !/^[1-9]\d{0,4}$/.test(base.port)
    || port > 65_535 || !['/events', '/ws'].includes(path) || !validFixtureLocalKey(localKey)) {
    throw new Error('invalid-loopback-stream-configuration');
  }
  return {
    protocol: 'http:', hostname: '127.0.0.1', port,
    method: 'GET', path: `/v1${path}`, maxHeaderSize: 8192,
    headers: { Authorization: `Bearer ${localKey}` },
  };
}

function destroyOwned(resources) {
  for (const resource of resources) {
    try { resource.destroy?.(); } catch {}
  }
}

function boundedOpen(open, label, resources) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      destroyOwned(resources);
      reject(new Error(`${label}-open-timeout`));
    }, 5_000);
    open.then(value => {
      clearTimeout(timer);
      resolve(value);
    }, error => {
      clearTimeout(timer);
      destroyOwned(resources);
      reject(error);
    });
  });
}

function openCLISSE(baseURL, localKey, resources) {
  return boundedOpen(new Promise((resolve, reject) => {
    const request = http.request(loopbackStreamOptions(baseURL, '/events', localKey));
    resources.add(request);
    let settled = false;
    const fail = () => {
      if (!settled) {
        settled = true;
        destroyOwned(resources);
        reject(new Error('cli-sse-open-failed'));
      }
    };
    request.once('error', fail);
    request.once('response', response => {
      resources.add(response);
      response.on('error', () => {});
      if (response.statusCode !== 200) {
        response.destroy();
        request.destroy();
        fail();
        return;
      }
      let marker = '';
      const closed = new Promise(done => {
        const finish = () => done({ monotonic: performance.now() });
        response.once('close', finish);
        response.once('end', finish);
        response.once('error', finish);
      });
      response.on('data', chunk => {
        marker = (marker + chunk).slice(-64);
        if (!marker.includes('data: ready\n\n') || settled) return;
        settled = true;
        resolve({ request, response, closed });
      });
      response.once('end', fail);
    });
    request.end();
  }), 'cli-sse', resources);
}

function websocketAccept(key) {
  return createHash('sha1').update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
}

function hasConnectionUpgrade(value) {
  return typeof value === 'string' && value.split(',').some(token => token.trim().toLowerCase() === 'upgrade');
}

function openCLIWebSocket(baseURL, localKey, resources) {
  return boundedOpen(new Promise((resolve, reject) => {
    const key = randomBytes(16).toString('base64');
    const options = loopbackStreamOptions(baseURL, '/ws', localKey);
    options.headers = {
      ...options.headers, Connection: 'Upgrade', Upgrade: 'websocket',
      'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Key': key,
    };
    const request = http.request(options);
    resources.add(request);
    let settled = false;
    const fail = () => {
      if (!settled) {
        settled = true;
        destroyOwned(resources);
        reject(new Error('cli-websocket-open-failed'));
      }
    };
    request.once('error', fail);
    request.once('response', response => {
      resources.add(response);
      response.on('error', () => {});
      response.destroy();
      request.destroy();
      fail();
    });
    request.once('upgrade', (response, socket) => {
      resources.add(response);
      resources.add(socket);
      socket.on('error', () => {});
      const valid = response.statusCode === 101
        && String(response.headers.upgrade || '').toLowerCase() === 'websocket'
        && hasConnectionUpgrade(response.headers.connection)
        && response.headers['sec-websocket-accept'] === websocketAccept(key);
      if (!valid) {
        socket.destroy();
        request.destroy();
        fail();
        return;
      }
      const closed = new Promise(done => {
        const finish = () => done({ monotonic: performance.now() });
        socket.once('close', finish);
        socket.once('end', finish);
        socket.once('error', finish);
      });
      // Drain control frames so the close handshake remains observable without
      // retaining an unread socket buffer during the active-stream interval.
      socket.resume();
      settled = true;
      resolve({ request, socket, closed });
    });
    request.end();
  }), 'cli-websocket', resources);
}

async function streamHTTPStatus(baseURL, path, localKey, upgrade, resources) {
  return boundedOpen(new Promise((resolve, reject) => {
    const options = loopbackStreamOptions(baseURL, path, localKey);
    if (upgrade) {
      const key = randomBytes(16).toString('base64');
      options.headers = {
        ...options.headers, Connection: 'Upgrade', Upgrade: 'websocket',
        'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Key': key,
      };
    }
    const request = http.request(options);
    resources.add(request);
    let settled = false;
    const finish = status => { if (!settled) { settled = true; resolve(status); } };
    const fail = () => {
      if (!settled) {
        settled = true;
        destroyOwned(resources);
        reject(new Error('cli-stream-denial-failed'));
      }
    };
    request.once('error', fail);
    request.once('response', response => {
      resources.add(response);
      response.on('error', () => {});
      const status = response.statusCode;
      response.destroy();
      request.destroy();
      finish(status);
    });
    request.once('upgrade', (response, socket) => {
      resources.add(response);
      resources.add(socket);
      socket.on('error', () => {});
      const status = response.statusCode;
      socket.destroy();
      request.destroy();
      finish(status);
    });
    request.end();
  }), 'cli-stream-denial', resources);
}

function closeCLIStreams(streams, resources) {
  destroyOwned(resources);
  streams?.sse?.request?.destroy();
  streams?.sse?.response?.destroy();
  streams?.websocket?.request?.destroy();
  streams?.websocket?.socket?.destroy();
}

async function apiStreamCounts(timeout = 5_000) {
  const counts = await fixture.command('api stream counts', timeout);
  expect(Object.keys(counts).sort()).toEqual(['ack', ...apiStreamCountFields].sort());
  expect(counts.ack).toBe('api stream counts');
  for (const field of apiStreamCountFields) expect(counts[field]).toMatch(/^\d+$/);
  return counts;
}

async function expectCLIStreamClosure(started, clientClosed) {
  const withinBound = action => {
    const remaining = 1_000 - (performance.now() - started);
    if (remaining <= 0) throw new Error('cli-stream-closure-exceeded-bound');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('cli-stream-closure-exceeded-bound')), remaining);
      Promise.resolve().then(action).then(
        value => { clearTimeout(timer); resolve(value); },
        error => { clearTimeout(timer); reject(error); },
      );
    });
  };
  expect(await withinBound(async () => clientClosed)).toBe(true);
  await withinBound(async () => {
    await expect.poll(async () => {
      const remaining = Math.max(1, 1_000 - (performance.now() - started));
      const counts = await apiStreamCounts(remaining);
      return apiStreamCountFields.map(field => counts[field]).join(',');
    }, { timeout: Math.max(1, 1_000 - (performance.now() - started)), intervals: [20, 50] }).toBe('1,1,1,1');
  });
  const closureMs = Math.ceil(performance.now() - started);
  expect(closureMs).toBeLessThanOrEqual(1_000);
  return closureMs;
}

async function expiryAuthorityDeadline(page, terminalSessionID) {
  const sampleClock = () => {
    const monotonicBefore = performance.now();
    const wall = Date.now();
    const monotonicAfter = performance.now();
    return { wall, monotonicBefore, monotonicAfter, monotonicMidpoint: (monotonicBefore + monotonicAfter) / 2 };
  };
  const before = sampleClock();
  const inventory = await page.evaluate(async () => {
    const response = await fetch('/_anvil-connect/access?kind=sessions&limit=50', { redirect: 'manual' });
    if (response.status !== 200) throw new Error('expiry-access-inventory-denied');
    return response.json();
  });
  const after = sampleClock();
  const wallElapsed = after.wall - before.wall;
  const monotonicElapsed = after.monotonicMidpoint - before.monotonicMidpoint;
  if (!Number.isFinite(wallElapsed) || wallElapsed < 0 || wallElapsed > 500
    || !Number.isFinite(monotonicElapsed) || monotonicElapsed < 0 || monotonicElapsed > 500
    || Math.abs(monotonicElapsed - wallElapsed) > 100) {
    throw new Error('expiry-deadline-sampling-invalid');
  }
  if (!inventory || inventory.schema !== 'anvil-connect.access/v1' || inventory.kind !== 'sessions'
    || inventory.next_cursor !== null || !Array.isArray(inventory.items)
    || typeof inventory.current_session !== 'string') {
    throw new Error('expiry-access-inventory-invalid');
  }
  const terminal = inventory.items.filter(item => item && item.type === 'terminal' && item.id === terminalSessionID);
  if (terminal.length !== 1 || typeof terminal[0].source_session !== 'string') {
    throw new Error('expiry-terminal-session-missing');
  }
  const browser = inventory.items.filter(item => item && item.type === 'browser' && item.id === terminal[0].source_session);
  if (browser.length !== 1 || browser[0].id !== inventory.current_session || browser[0].status !== 'issued'
    || terminal[0].status !== 'issued' || browser[0].principal !== terminal[0].principal || typeof browser[0].expires_at !== 'string') {
    throw new Error('expiry-browser-session-missing');
  }
  const deadlineWall = Date.parse(browser[0].expires_at);
  if (!Number.isFinite(deadlineWall)) throw new Error('expiry-browser-deadline-invalid');
  // Date.now is millisecond-granular. Each sample brackets its wall-clock read
  // with monotonic time, and the lower mapping includes the one-millisecond
  // truncation uncertainty. Early-close checks use this lower bound; timing
  // resolution is milliseconds, not sub-millisecond precision.
  const deadline = Math.min(
    before.monotonicBefore + deadlineWall - before.wall - 1,
    after.monotonicBefore + deadlineWall - after.wall - 1,
  );
  if (!Number.isFinite(deadline) || deadline - after.monotonicMidpoint < 2_000) {
    throw new Error('expiry-browser-deadline-too-soon');
  }
  return { monotonic: deadline, wall: deadlineWall, clock: after };
}

function waitUntilMonotonic(deadline) {
  const remaining = deadline - performance.now();
  if (remaining <= 0) return Promise.resolve();
  return new Promise(resolve => setTimeout(resolve, remaining));
}

function observeStreamClosure(promise) {
  const observed = { closed: false };
  observed.promise = Promise.resolve(promise).then(value => {
    observed.closed = true;
    return value;
  }, () => {
    observed.closed = true;
    return false;
  });
  return observed;
}

function browserStreamClosureTimes(page) {
  return page.evaluate(() => {
    const records = [];
    window.__browserStreamClosureTimes = records;
    const events = window.__browserStreamReader.closed.then(
      () => { records.push({ stream: 'events', wall: Date.now() }); return true; },
      () => { records.push({ stream: 'events', wall: Date.now() }); return true; },
    );
    const websocket = new Promise(resolve => {
      const socket = window.__browserStreamSocket;
      if (socket.readyState === WebSocket.CLOSED) {
        records.push({ stream: 'ws', wall: Date.now() });
        resolve(true);
        return;
      }
      socket.addEventListener('close', () => {
        records.push({ stream: 'ws', wall: Date.now() });
        resolve(true);
      }, { once: true });
    });
    return Promise.all([events, websocket]).then(() => records);
  }).catch(() => null);
}

function browserStreamsStillOpen(page) {
  return page.evaluate(() => Array.isArray(window.__browserStreamClosureTimes) && window.__browserStreamClosureTimes.length === 0).catch(() => false);
}

function assertExpiryClockDrift(reference) {
  const monotonicBefore = performance.now();
  const wall = Date.now();
  const monotonicAfter = performance.now();
  const elapsedWall = wall - reference.wall;
  const elapsedMonotonic = (monotonicBefore + monotonicAfter) / 2 - reference.monotonicMidpoint;
  if (!Number.isFinite(elapsedWall) || elapsedWall < 0 || !Number.isFinite(elapsedMonotonic)
    || elapsedMonotonic < 0 || Math.abs(elapsedWall - elapsedMonotonic) > 100) {
    throw new Error('expiry-clock-drift-invalid');
  }
}

async function expectExpiryStreamClosure(deadline, page, browser, cliSSE, cliWS) {
  const beforeExpiry = deadline.monotonic - 250;
  if (beforeExpiry <= performance.now()) throw new Error('expiry-streams-not-established-in-time');
  await waitUntilMonotonic(beforeExpiry);
  expect(await browserStreamsStillOpen(page)).toBe(true);
  expect(cliSSE.closed).toBe(false);
  expect(cliWS.closed).toBe(false);
  expect(await browserStreamCounts()).toMatchObject({
    events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
  });
  expect(await apiStreamCounts()).toMatchObject({
    events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
  });
  const remaining = 1_000 - (performance.now() - deadline.monotonic);
  if (remaining <= 0) throw new Error('expiry-stream-closure-exceeded-bound');
  const bounded = action => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('expiry-stream-closure-exceeded-bound')), Math.max(1, deadline.monotonic + 1_000 - performance.now()));
    Promise.resolve().then(action).then(
      value => { clearTimeout(timer); resolve(value); },
      error => { clearTimeout(timer); reject(error); },
    );
  });
  const [browserRecords, sse, websocket] = await bounded(async () => Promise.all([browser.promise, cliSSE.promise, cliWS.promise]));
  const browserStreams = Array.isArray(browserRecords) ? new Set(browserRecords.map(record => record?.stream)) : new Set();
  if (!Array.isArray(browserRecords) || browserRecords.length !== 2 || browserStreams.size !== 2 || !browserStreams.has('events') || !browserStreams.has('ws')
    || !browserRecords.every(record => record && Number.isInteger(record.wall) && record.wall >= deadline.wall)
    || !sse || !Number.isFinite(sse.monotonic) || sse.monotonic < deadline.monotonic
    || !websocket || !Number.isFinite(websocket.monotonic) || websocket.monotonic < deadline.monotonic) {
    throw new Error('expiry-stream-closed-early');
  }
  await bounded(async () => {
    await expect.poll(async () => {
      const [browser, api] = await Promise.all([browserStreamCounts(500), apiStreamCounts(500)]);
      return [browser, api].map(counts => browserStreamCountFields.map(field => counts[field]).join(',')).join(';');
    }, { timeout: Math.max(1, deadline.monotonic + 1_000 - performance.now()), intervals: [20, 50] }).toBe('1,1,1,1;1,1,1,1');
  });
  assertExpiryClockDrift(deadline.clock);
  const closureMs = Math.ceil(performance.now() - deadline.monotonic);
  expect(closureMs).toBeGreaterThanOrEqual(0);
  expect(closureMs).toBeLessThanOrEqual(1_000);
  return closureMs;
}

async function exerciseCLIStreamRevocation(mutate) {
  const edgeFixture = fixture;
  let loginSession;
  let streams;
  const streamResources = new Set();
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE');
  try {
    const page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    const cli = await buildDeviceCLI(fixture.dir);
    loginSession = await startDeviceCLI(cli, fixture);
    const challenge = await waitForDeviceEvent(loginSession.challenge);
    await page.goto(challenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(challenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const ready = await waitForDeviceEvent(loginSession.ready);
    const localKey = (await readFile(fixture.local_key, 'utf8')).trim();
    expect(validFixtureLocalKey(localKey)).toBe(true);

    const [sse, websocket] = await Promise.all([openCLISSE(ready.baseURL, localKey, streamResources), openCLIWebSocket(ready.baseURL, localKey, streamResources)]);
    streams = { sse, websocket };
    expect(await apiStreamCounts()).toMatchObject({
      events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
    });
    const clientClosed = Promise.all([sse.closed, websocket.closed]).then(() => true, () => false);
    const started = performance.now();
    await mutate(page);
    const closureMs = await expectCLIStreamClosure(started, clientClosed);
    test.info().annotations.push({ type: 'closure_ms', description: String(closureMs) });

    const beforeDenied = await apiStreamCounts();
    const denialResources = new Set();
    try {
      expect(await streamHTTPStatus(ready.baseURL, '/events', localKey, false, denialResources)).toBe(401);
      expect(await streamHTTPStatus(ready.baseURL, '/ws', localKey, true, denialResources)).toBe(401);
    } finally {
      destroyOwned(denialResources);
    }
    expect(await apiStreamCounts()).toEqual(beforeDenied);

    await stopDeviceCLI(loginSession);
    loginSession = undefined;
    await assertLoopbackReleased(ready.baseURL);
  } finally {
    closeCLIStreams(streams, streamResources);
    try { await stopDeviceCLI(loginSession); } finally {
      const deviceFixture = fixture;
      fixture = edgeFixture;
      await cleanup(deviceFixture);
    }
  }
}

test('container-gated CLI streams close on human disable', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  await exerciseCLIStreamRevocation(async () => {
    await fixture.command('disable allowed');
  });
});

test('container-gated CLI streams close on browser logout', async () => {
  test.setTimeout(90_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE !== '1', 'requires the disposable P02 container loopback DNS and port 443');
  await exerciseCLIStreamRevocation(async page => {
    const logout = page.waitForResponse(response => response.url().startsWith(`https://${dashHost}/_anvil-connect/logout`) && response.request().method() === 'POST').catch(() => null);
    await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
    const response = await logout;
    expect(response).not.toBeNull();
    expect(response.status()).toBe(303);
  });
});

test('container-gated browser and CLI streams close on session expiry', async () => {
  test.setTimeout(210_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_EXPIRY_FIXTURE !== '1', 'requires the disposable 120-second session-expiry fixture');
  const edgeFixture = fixture;
  let loginSession;
  let streams;
  const streamResources = new Set();
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_EXPIRY_FIXTURE');
  try {
    const page = await freshPage();
    await login(page, 'allowed', 401);
    // In this dedicated expiry profile, this ordinary authority grant
    // provisions the same admitted human as the configured Access operator.
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    const cli = await buildDeviceCLI(fixture.dir);
    loginSession = await startDeviceCLI(cli, fixture);
    const challenge = await waitForDeviceEvent(loginSession.challenge);
    await page.goto(challenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(challenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const ready = await waitForDeviceEvent(loginSession.ready);
    const localKey = (await readFile(fixture.local_key, 'utf8')).trim();
    expect(validFixtureLocalKey(localKey)).toBe(true);

    const deadline = await expiryAuthorityDeadline(page, ready.sessionID);
    const cookie = (await fixture.context.cookies(fixture.url)).find(value => value.name === '__Host-anvil-connect');
    expect(cookie).toBeTruthy();

    const [sse, websocket] = await Promise.all([
      openCLISSE(ready.baseURL, localKey, streamResources),
      openCLIWebSocket(ready.baseURL, localKey, streamResources),
    ]);
    streams = { sse, websocket };
    await openBrowserStreams(page);
    expect(await browserStreamCounts()).toMatchObject({
      events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
    });
    expect(await apiStreamCounts()).toMatchObject({
      events_started: '1', events_closed: '0', ws_started: '1', ws_closed: '0',
    });

    const browserClosed = observeStreamClosure(browserStreamClosureTimes(page));
    const cliSSEClosed = observeStreamClosure(sse.closed);
    const cliWSClosed = observeStreamClosure(websocket.closed);
    const closureMs = await expectExpiryStreamClosure(deadline, page, browserClosed, cliSSEClosed, cliWSClosed);
    test.info().annotations.push({ type: 'closure_ms', description: String(closureMs) });

    // Replay only the previous opaque Connect cookie in memory, beyond the
    // browser-managed cookie lifetime. The expired server session must deny it
    // before the native SSE origin is reached.
    const beforeBrowserDenied = await browserStreamCounts();
    await fixture.context.clearCookies({ name: cookie.name, domain: dashHost });
    await fixture.context.addCookies([{
      name: cookie.name, value: cookie.value, url: fixture.url,
      httpOnly: cookie.httpOnly, secure: true, sameSite: cookie.sameSite,
      expires: -1,
    }]);
    const replayed = (await fixture.context.cookies(fixture.url)).find(value => value.name === cookie.name);
    expect(replayed?.value === cookie.value).toBe(true);
    await expectFreshBrowserStreamsDenied(page);
    expect(await browserStreamCounts()).toEqual(beforeBrowserDenied);

    const beforeCLIDenied = await apiStreamCounts();
    const denialResources = new Set();
    try {
      expect(await streamHTTPStatus(ready.baseURL, '/events', localKey, false, denialResources)).toBe(401);
      expect(await streamHTTPStatus(ready.baseURL, '/ws', localKey, true, denialResources)).toBe(401);
    } finally {
      destroyOwned(denialResources);
    }
    expect(await apiStreamCounts()).toEqual(beforeCLIDenied);

    await stopDeviceCLI(loginSession);
    loginSession = undefined;
    await assertLoopbackReleased(ready.baseURL);
  } finally {
    closeCLIStreams(streams, streamResources);
    try { await stopDeviceCLI(loginSession); } finally {
      const expiryFixture = fixture;
      fixture = edgeFixture;
      await cleanup(expiryFixture);
    }
  }
});


async function virtualAuthenticator(page, credential, overrides = {}) {
  const session = await fixture.context.newCDPSession(page);
  await session.send('WebAuthn.enable');
  const { authenticatorId } = await session.send('WebAuthn.addVirtualAuthenticator', { options: {
    protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
    ...overrides,
  } });
  if (credential) await session.send('WebAuthn.addCredential', { authenticatorId, credential });
  return { session, authenticatorId };
}

async function rejectedPasskeyLogin(page, credential, overrides) {
  let assertions = 0;
  let callbacks = 0;
  const countRequest = request => {
    if (new URL(request.url()).pathname === '/api/firstfactor/passkey' && request.method() === 'POST') assertions++;
  };
  const countResponse = response => {
    if (new URL(response.url()).pathname === '/_anvil-connect/callback') callbacks++;
  };
  page.on('request', countRequest);
  page.on('response', countResponse);
  try {
    await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
    const authenticator = await virtualAuthenticator(page, credential, overrides);
    const options = page.waitForResponse(response => new URL(response.url()).pathname === '/api/firstfactor/passkey' && response.request().method() === 'GET', { timeout: 10_000 });
    await page.getByRole('button', { name: 'Sign in with a passkey', exact: true }).click();
    const response = await options;
    expect(response.status()).toBe(200);
    expect((await response.json()).data.publicKey.userVerification).toBe('required');
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10_000 });
    expect(assertions).toBe(0);
    expect(callbacks).toBe(0);
    await expect(page.locator('#dashboard')).toHaveCount(0);
    return authenticator;
  } finally {
    page.off('request', countRequest);
    page.off('response', countResponse);
  }
}

async function registerPasskey(page, description) {
  await page.goto(`https://${authHost}/settings/two-factor-authentication`, { waitUntil: 'domcontentloaded' });
  const enrolled = await virtualAuthenticator(page);
  await page.locator('#webauthn-credential-add').click();
  const codeDialog = page.locator('#dialog-verify-one-time-code');
  const descriptionField = page.locator('#webauthn-credential-description');
  const emailMethod = page.getByRole('button', { name: 'Email One-Time Code', exact: true });
  let stage;
  const waitForEnrollmentStage = async () => {
    await expect.poll(async () => {
      stage = await descriptionField.isVisible() ? 'description'
        : await codeDialog.isVisible() ? 'code'
          : await emailMethod.isVisible() ? 'email' : 'pending';
      return stage;
    }, { timeout: 10_000 }).not.toBe('pending');
  };
  await waitForEnrollmentStage();
  if (stage === 'email') {
    await emailMethod.click();
    await expect(codeDialog).toBeVisible();
    stage = 'code';
  }
  if (stage === 'code') {
    const { code } = await fixture.command('elevation code');
    await codeDialog.getByRole('textbox', { name: 'One-Time Code', exact: true }).fill(code);
    await codeDialog.getByRole('button', { name: 'Verify', exact: true }).click();
  }
  await expect(descriptionField).toBeVisible();
  await descriptionField.fill(description);
  const options = page.waitForResponse(response => new URL(response.url()).pathname === '/api/secondfactor/webauthn/credential/register' && response.request().method() === 'PUT', { timeout: 10_000 });
  const registered = page.waitForResponse(response => new URL(response.url()).pathname === '/api/secondfactor/webauthn/credential/register' && response.request().method() === 'POST', { timeout: 15_000 }).catch(() => null);
  await page.getByRole('dialog').filter({ has: descriptionField }).getByRole('button', { name: 'Next', exact: true }).click();
  const creation = await (await options).json();
  expect(creation.data.publicKey.authenticatorSelection.residentKey).toBe('required');
  expect(creation.data.publicKey.authenticatorSelection.userVerification).toBe('required');
  const registrationResponse = await registered;
  expect(registrationResponse).not.toBeNull();
  expect([200, 201]).toContain(registrationResponse.status());
  await expect(descriptionField).not.toBeVisible();
  return enrolled;
}

async function passkeyLogin(page, credential, expectedCallbackStatus = 303) {
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  const authenticator = await virtualAuthenticator(page, credential);
  const options = page.waitForResponse(response => new URL(response.url()).pathname === '/api/firstfactor/passkey' && response.request().method() === 'GET', { timeout: 10_000 }).then(response => response.json()).catch(() => null);
  const signedIn = page.waitForResponse(response => new URL(response.url()).pathname === '/api/firstfactor/passkey' && response.request().method() === 'POST', { timeout: 15_000 }).catch(() => null);
  const callback = page.waitForResponse(response => new URL(response.url()).pathname === '/_anvil-connect/callback', { timeout: 15_000 }).catch(() => null);
  await page.getByRole('button', { name: 'Sign in with a passkey', exact: true }).click();
  const assertion = await options;
  expect(assertion).not.toBeNull();
  expect(assertion.data.publicKey.userVerification).toBe('required');
  const signedInResponse = await signedIn;
  expect(signedInResponse).not.toBeNull();
  expect(signedInResponse.status()).toBe(200);
  const accept = page.getByRole('button', { name: 'Accept', exact: true });
  const transition = await Promise.race([
    callback.then(response => ({ kind: 'callback', response })),
    accept.waitFor({ state: 'visible', timeout: 5_000 }).then(() => ({ kind: 'consent' })).catch(() => ({ kind: 'timeout' })),
  ]);
  expect(transition.kind).not.toBe('timeout');
  if (transition.kind === 'consent') await accept.click();
  const admitted = await callback;
  expect(admitted).not.toBeNull();
  expect(admitted.status()).toBe(expectedCallbackStatus);
  expect(new URL(admitted.url()).searchParams.get('iss')).toBe(`https://${authHost}`);
  expect(new URL(admitted.url()).searchParams.get('scope')).toBe('openid');
  if (expectedCallbackStatus === 303) {
    await page.waitForURL(fixture.url, { waitUntil: 'domcontentloaded', timeout: 10_000 });
    await expect(page.locator('#dashboard')).toHaveText('native dashboard');
  } else {
    await page.waitForURL(url => url.pathname === '/_anvil-connect/callback', { waitUntil: 'domcontentloaded', timeout: 10_000 });
    await expect(page.locator('#dashboard')).toHaveCount(0);
  }
  return authenticator;
}

test('container-gated UV passkey registers and approves CLI device login', async () => {
  test.setTimeout(120_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE !== '1', 'requires the disposable virtual WebAuthn fixture');
  const edgeFixture = fixture;
  let loginSession;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE');
  try {
    let page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);
    const enrolled = await registerPasskey(page, 'Synthetic primary passkey');
    const { credentials } = await enrolled.session.send('WebAuthn.getCredentials', { authenticatorId: enrolled.authenticatorId });
    expect(credentials.length).toBe(1);
    expect(credentials[0].rpId).toBe(authHost);
    expect(credentials[0].isResidentCredential).toBe(true);

    // The synthetic private key stays only in this fixture process. A new
    // page/authenticator models using the enrolled key after browser logout.
    page = await freshPage();
    await passkeyLogin(page, credentials[0]);
    const cli = await buildDeviceCLI(fixture.dir);
    loginSession = await startDeviceCLI(cli, fixture);
    const challenge = await waitForDeviceEvent(loginSession.challenge);
    await page.goto(challenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(challenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const ready = await waitForDeviceEvent(loginSession.ready);
    const localKey = (await readFile(fixture.local_key, 'utf8')).trim();
    const models = await loopbackResponse(ready.baseURL, 'GET', localKey);
    expect(models.status).toBe(200);
    expect((await models.json()).data).toEqual([{ id: 'fixture-model' }]);
    expect((await fixture.command('api request count')).count).toBe('1');
    await stopDeviceCLI(loginSession);
    loginSession = undefined;
    await assertLoopbackReleased(ready.baseURL);
  } finally {
    try { await stopDeviceCLI(loginSession); } finally {
      const passkeyFixture = fixture;
      fixture = edgeFixture;
      await cleanup(passkeyFixture);
    }
  }
});

test('container-gated spare passkey restores least-privilege access', async () => {
  test.setTimeout(120_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE !== '1', 'requires the disposable virtual WebAuthn fixture');
  const edgeFixture = fixture;
  let loginSession;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE');
  try {
    let page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);

    const primary = await registerPasskey(page, 'Synthetic primary passkey');
    const primaryKeys = await primary.session.send('WebAuthn.getCredentials', { authenticatorId: primary.authenticatorId });
    expect(primaryKeys.credentials.length).toBe(1);
    const primaryID = primaryKeys.credentials[0].credentialId;
    // Unplug the primary while enrolling an independent spare authenticator.
    // Retain its key only in memory until spare enrollment is complete.
    await primary.session.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId: primary.authenticatorId });
    await primary.session.detach();
    const spare = await registerPasskey(page, 'Synthetic spare passkey');
    const spareKeys = await spare.session.send('WebAuthn.getCredentials', { authenticatorId: spare.authenticatorId });
    expect(spareKeys.credentials.length).toBe(1);
    expect(spareKeys.credentials[0].credentialId).not.toBe(primaryID);
    expect(spareKeys.credentials[0].rpId).toBe(authHost);

    // Model losing the primary only after the spare has been pre-enrolled.
    primaryKeys.credentials.length = 0;
    page = await freshPage();
    await passkeyLogin(page, spareKeys.credentials[0]);
    spareKeys.credentials.length = 0;
    const cli = await buildDeviceCLI(fixture.dir);
    loginSession = await startDeviceCLI(cli, fixture);
    const challenge = await waitForDeviceEvent(loginSession.challenge);
    await page.goto(challenge.verificationURI, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Code').fill(challenge.userCode);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const ready = await waitForDeviceEvent(loginSession.ready);
    const localKey = (await readFile(fixture.local_key, 'utf8')).trim();
    expect((await loopbackResponse(ready.baseURL, 'GET', localKey)).status).toBe(200);
    expect((await fixture.command('api request count')).count).toBe('1');

    await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
    const denied = await page.evaluate(async () => {
      const path = '/_anvil-connect/access';
      const inventory = await fetch(`${path}?kind=users&limit=10`, { redirect: 'manual' });
      const mutation = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', redirect: 'manual' });
      return [inventory.status, mutation.status];
    });
    expect(denied).toEqual([403, 403]);
    // Reuse the original sessions after the forbidden admin requests. A
    // successful human-generation change would invalidate these admissions.
    expect(await page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status))).toBe(200);
    expect((await loopbackResponse(ready.baseURL, 'GET', localKey)).status).toBe(200);
    expect((await fixture.command('api request count')).count).toBe('2');
    await stopDeviceCLI(loginSession);
    loginSession = undefined;
    await assertLoopbackReleased(ready.baseURL);
  } finally {
    try { await stopDeviceCLI(loginSession); } finally {
      const passkeyFixture = fixture;
      fixture = edgeFixture;
      await cleanup(passkeyFixture);
    }
  }
});

test('container-gated passkey rejects UV, RP, expiry, replay, and disabled subject', async () => {
  test.setTimeout(180_000);
  test.skip(process.env.ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE !== '1', 'requires the disposable virtual WebAuthn fixture');
  const edgeFixture = fixture;
  fixture = await startFixture('^TestBrowserRuntimeEdgeFixture$', 'ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE');
  try {
    // Register once through the provider's real UI, then retain the virtual
    // credential only in this test process. Every assertion case uses a fresh
    // browser page and exactly one authenticator.
    let page = await freshPage();
    await login(page, 'allowed', 401);
    await fixture.command('grant allowed');
    await resumeGrantedAuthorization(page);
    const enrolled = await registerPasskey(page, 'Synthetic negative passkey');
    const { credentials } = await enrolled.session.send('WebAuthn.getCredentials', { authenticatorId: enrolled.authenticatorId });
    expect(credentials).toHaveLength(1);
    let credential = credentials[0];
    const retainCredential = async authenticator => {
      const snapshot = await authenticator.session.send('WebAuthn.getCredentials', { authenticatorId: authenticator.authenticatorId });
      expect(snapshot.credentials).toHaveLength(1);
      // Carry the actual authenticator counter forward across fresh pages.
      // Resetting it would test cloned-key rejection instead of the next case.
      credential = snapshot.credentials[0];
    };

    page = await freshPage();
    await rejectedPasskeyLogin(page, credential, { isUserVerified: false });
    page = await freshPage();
    await retainCredential(await passkeyLogin(page, credential));

    // A credential scoped to another RP must not be selected for the actual
    // provider options. This is a browser/RP boundary check, not a forged
    // assertion signature test.
    page = await freshPage();
    await rejectedPasskeyLogin(page, { ...credential, rpId: 'wrong.example.test' });
    page = await freshPage();
    await retainCredential(await passkeyLogin(page, credential));

    // Hold a real browser-produced assertion beyond the fixture-only five
    // second server timeout, then continue the original request. No route
    // fetch or copied transport is used.
    page = await freshPage();
    let expiryCallbacks = 0;
    const countExpiryCallback = response => {
      if (new URL(response.url()).pathname === '/_anvil-connect/callback') expiryCallbacks++;
    };
    page.on('response', countExpiryCallback);
    try {
      await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
      const expiringAuthenticator = await virtualAuthenticator(page, credential);
      const options = page.waitForResponse(response => new URL(response.url()).pathname === '/api/firstfactor/passkey' && response.request().method() === 'GET', { timeout: 10_000 }).catch(() => null);
      const assertion = page.waitForResponse(response => new URL(response.url()).pathname === '/api/firstfactor/passkey' && response.request().method() === 'POST', { timeout: 15_000 }).catch(() => null);
      await page.route('**/api/firstfactor/passkey', async route => {
        if (route.request().method() !== 'POST') {
          await route.continue();
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 6_000));
        await route.continue();
      });
      await page.getByRole('button', { name: 'Sign in with a passkey', exact: true }).click();
      const optionResponse = await options;
      expect(optionResponse).not.toBeNull();
      expect((await optionResponse.json()).data.publicKey.userVerification).toBe('required');
      const expired = await assertion;
      expect(expired).not.toBeNull();
      expect(expired.status()).toBe(403);
      await expect(page.getByRole('alert')).toBeVisible({ timeout: 10_000 });
      expect(expiryCallbacks).toBe(0);
      await expect(page.locator('#dashboard')).toHaveCount(0);
      await retainCredential(expiringAuthenticator);
    } finally {
      await page.unroute('**/api/firstfactor/passkey');
      page.off('response', countExpiryCallback);
    }

    // Capture the first successful browser assertion only in memory. Replay
    // its exact bytes from a separately loaded auth-origin page. The resulting
    // authenticated session must reject replay without a new Connect callback;
    // this does not isolate challenge consumption or concurrent replay safety.
    page = await freshPage();
    const firstAssertion = page.waitForRequest(request => new URL(request.url()).pathname === '/api/firstfactor/passkey' && request.method() === 'POST', { timeout: 15_000 }).catch(() => null);
    await retainCredential(await passkeyLogin(page, credential));
    const firstAssertionRequest = await firstAssertion;
    expect(firstAssertionRequest).not.toBeNull();
    let assertionBody = firstAssertionRequest.postData();
    expect(assertionBody).toBeTruthy();
    const replayPage = await fixture.context.newPage();
    try {
      await replayPage.goto(`https://${authHost}/`, { waitUntil: 'domcontentloaded' });
      let replayCallbacks = 0;
      const countReplayCallback = response => {
        if (new URL(response.url()).pathname === '/_anvil-connect/callback') replayCallbacks++;
      };
      replayPage.on('response', countReplayCallback);
      const replayed = await replayPage.evaluate(async body => {
        const response = await fetch('/api/firstfactor/passkey', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
        });
        return response.status;
      }, assertionBody);
      expect(replayed).toBe(403);
      expect(replayCallbacks).toBe(0);
      replayPage.off('response', countReplayCallback);
    } finally {
      assertionBody = undefined;
      await replayPage.close();
    }

    // Connect authorization remains distinct from the IdP passkey. A valid
    // provider assertion may complete, but the disabled opaque human cannot
    // obtain a dashboard or device approval session.
    await fixture.command('disable allowed');
    page = await freshPage();
    await passkeyLogin(page, credential, 401);
    expect(await page.evaluate(() => fetch('/_anvil-connect/device', { redirect: 'manual' }).then(response => response.status))).toBe(401);
  } finally {
    const passkeyFixture = fixture;
    fixture = edgeFixture;
    await cleanup(passkeyFixture);
  }
});
