import { test, expect, chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';

let fixture;
const dashHost = 'dash.example.test';
const connectRoot = join(fileURLToPath(new URL('.', import.meta.url)), '..');

async function waitForExit(child, milliseconds) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  return new Promise(resolve => {
    const finish = value => { clearTimeout(timer); child.off('exit', exited); child.off('error', exited); resolve(value); };
    const exited = () => finish(true);
    const timer = setTimeout(() => finish(false), milliseconds);
    child.once('exit', exited);
    child.once('error', exited);
  });
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const stopped = waitForExit(child, 3000);
  child.kill('SIGTERM');
  if (await stopped) return;
  const killed = waitForExit(child, 3000);
  child.kill('SIGKILL');
  if (!await killed) throw new Error('owned fixture process did not exit');
}

async function cleanup(value) {
  const outcomes = await Promise.allSettled([
    value.browser?.close(), stopChild(value.child), stopChild(value.build),
  ]);
  await rm(value.dir, { recursive: true, force: true });
  const failed = outcomes.find(result => result.status === 'rejected');
  if (failed) throw failed.reason;
}

async function startFixture() {
  const value = { dir: await mkdtemp(join(tmpdir(), 'anvil-connect-browser-')) };
  try {
    const binary = join(value.dir, 'fixture.test');
    await new Promise((resolve, reject) => {
      const build = value.build = spawn(process.env.ANVIL_CONNECT_GO || 'go', ['test', '-c', '-o', binary, './test'], { cwd: connectRoot, stdio: ['ignore', 'pipe', 'pipe'] });
      const timer = setTimeout(() => reject(new Error('browser fixture build timed out')), 60_000);
      let output = '';
      build.stderr.on('data', data => { output = (output + data).slice(-16384); });
      build.on('error', error => { clearTimeout(timer); reject(error); });
      build.on('exit', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`browser fixture build failed (${code}): ${output}`)); });
    });
    const child = value.child = spawn(binary, ['-test.run', '^TestBrowserFixture$', '-test.v'], { env: { PATH: process.env.PATH, ANVIL_CONNECT_BROWSER_FIXTURE: '1' }, stdio: ['pipe', 'pipe', 'pipe'] });
    const acknowledgements = [];
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('browser fixture did not become ready')), 10_000);
      const lines = readline.createInterface({ input: child.stdout });
      let output = '';
      child.stderr.on('data', data => { output = (output + data).slice(-16384); });
      child.on('error', error => { clearTimeout(timer); reject(error); });
      child.on('exit', code => { clearTimeout(timer); lines.close(); reject(new Error(`browser fixture exited (${code}): ${output}`)); });
      lines.on('line', line => {
        try {
          const message = JSON.parse(line);
          if (message.url) { clearTimeout(timer); resolve(message); return; }
          if (message.ack) { const next = acknowledgements.shift(); if (next) next(message.ack); }
        } catch {}
      });
    });
    value.browser = await chromium.launch({
      headless: true, timeout: 10_000,
      args: [
        '--no-proxy-server', '--disable-quic',
        `--host-resolver-rules=MAP dash.example.test:443 ${ready.resolver},MAP idp.example.test:443 ${ready.resolver}`,
        `--ignore-certificate-errors-spki-list=${ready.spki}`,
      ],
    });
    return {
      ...value, ...ready,
      command(command) {
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('fixture command timed out')), 5000);
          acknowledgements.push(ack => { clearTimeout(timer); ack === command ? resolve() : reject(new Error('unexpected fixture acknowledgement')); });
          child.stdin.write(`${command}\n`);
        });
      },
    };
  } catch (error) {
    await cleanup(value);
    throw error;
  }
}

test.beforeAll(async () => { fixture = await startFixture(); });
test.afterAll(async () => { if (fixture) await cleanup(fixture); });

test('real Chromium login preserves native controls and host-only cookies', async () => {
  const context = await fixture.browser.newContext();
  const page = await context.newPage();
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#dashboard')).toHaveText('native dashboard');
  const cookies = await context.cookies(fixture.url);
  for (const name of ['__Host-anvil-connect', 'native_session']) {
    const cookie = cookies.find(value => value.name === name);
    expect(cookie, `${name} cookie`).toBeTruthy();
    expect(cookie.secure).toBe(true);
    expect(cookie.domain).toBe(dashHost);
  }
  expect(await page.evaluate(() => fetch('/native-grant', { method: 'POST', headers: { 'X-CSRF-Token': 'fixture-csrf' } }).then(response => response.status))).toBe(204);
  await context.close();
});

test('unprovisioned OIDC subject is denied in Chromium', async () => {
  await fixture.command('subject denied');
  const context = await fixture.browser.newContext({ extraHTTPHeaders: { Authorization: 'Bearer browser-api-key-must-not-bypass-connect' } });
  const page = await context.newPage();
  const response = await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  expect(response.status()).toBe(401);
  await expect(page.locator('body')).toContainText('Unauthorized');
  await context.close();
  await fixture.command('subject allowed');
});

test('logout cancels SSE and WebSocket through the real session manager', async () => {
  const context = await fixture.browser.newContext();
  const page = await context.newPage();
  await page.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => new Promise((resolve, reject) => { const stream = new EventSource('/events'); stream.onmessage = () => { window.__fixtureStream = stream; resolve(); }; stream.onerror = reject; }));
  await page.evaluate(() => new Promise((resolve, reject) => { const socket = new WebSocket(`wss://${location.host}/ws`); socket.onopen = resolve; socket.onerror = reject; window.__fixtureSocket = socket; }));
  const closed = page.evaluate(() => new Promise(resolve => { window.__fixtureSocket.onclose = () => resolve('closed'); }));
  const streamClosed = page.evaluate(() => new Promise(resolve => { window.__fixtureStream.onerror = () => resolve('closed'); }));
  await page.evaluate(() => fetch('/_anvil-connect/logout', { method: 'POST', redirect: 'manual' }));
  await expect.poll(() => closed).toBe('closed');
  await expect.poll(() => streamClosed).toBe('closed');
  expect(await page.evaluate(() => fetch('/', { redirect: 'manual' }).then(response => response.status))).toBe(401);
  await context.close();
});

test('negative controls expose dropped Connect and native checks', async () => {
  await fixture.command('subject denied');
  const denied = await fixture.browser.newContext();
  const deniedPage = await denied.newPage();
  expect((await deniedPage.goto(fixture.url, { waitUntil: 'domcontentloaded' })).status()).toBe(401);
  await fixture.command('mode session-bypass');
  const bypassed = await fixture.browser.newContext();
  const bypassPage = await bypassed.newPage();
  await bypassPage.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  await expect(bypassPage.locator('#dashboard')).toHaveText('native dashboard');
  await fixture.command('mode session-enforce');
  await fixture.command('subject allowed');
  const native = await fixture.browser.newContext();
  const nativePage = await native.newPage();
  await nativePage.goto(fixture.url, { waitUntil: 'domcontentloaded' });
  expect(await nativePage.evaluate(() => fetch('/fixture-native-guard', { method: 'POST' }).then(response => ({status: response.status, native: response.headers.get('X-Native-Guard')})))).toEqual({status: 403, native: 'reached'});
  await fixture.command('mode native-bypass');
  expect(await nativePage.evaluate(() => fetch('/fixture-native-guard', { method: 'POST' }).then(response => ({status: response.status, native: response.headers.get('X-Native-Guard')})))).toEqual({status: 204, native: 'reached'});
  await denied.close(); await bypassed.close(); await native.close();
});
