import { test, expect, chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { chmod, lstat, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';

const dashHost = 'dash.example.test';
const authHost = 'auth.example.test';
const connectRoot = join(fileURLToPath(new URL('.', import.meta.url)), '..');
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
  const match = /\bbrowser_edge_fixture_test\.go:(\d+):/.exec(line);
  return match ? `browser_edge_fixture_test.go:${match[1]}` : '';
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

function run(command, args, timeout = 10_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
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
    try { await page.goto(`https://${dashHost}`, { waitUntil: 'commit', timeout: 10_000 }); } catch (error) { rejected = /ERR_CERT|certificate/i.test(String(error)); }
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
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('actual-edge fixture command timed out')), timeout);
          replies.push(message => { clearTimeout(timer); if (message.ack !== command) reject(new Error('unexpected actual-edge acknowledgement')); else if (message.error) reject(new Error('fixture-command-failed')); else resolve(message); });
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

async function freshPage() {
  await fixture.context.clearCookies();
  for (const page of fixture.context.pages()) await page.close();
  return fixture.context.newPage();
}

async function login(page, identity, expectedCallbackStatus = 303) {
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await page.getByLabel(/username/i).fill(fixture[`${identity}_user`]);
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill(fixture[`${identity}_password`]);
  await page.getByRole('button', { name: /sign in|login/i }).click();
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

test.beforeAll(async () => { fixture = await startFixture(); });
test.afterAll(async () => { if (fixture) await cleanup(fixture); });

test('pinned Caddy and Authelia browser edge retains Connect and native controls', async () => {
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
