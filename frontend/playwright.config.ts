import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 1,
  use: {
    baseURL: 'http://127.0.0.1:4201',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run start -- --port 4201 --host 127.0.0.1',
    url: 'http://127.0.0.1:4201',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
