import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'browser*.spec.mjs',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: { browserName: 'chromium', headless: true },
});
