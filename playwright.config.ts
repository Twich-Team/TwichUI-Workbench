import { defineConfig } from '@playwright/test';
import {createRunOutput} from './e2e/output';
import {join} from 'node:path';

const runRoot=createRunOutput(process.cwd());
process.env.WORKBENCH_E2E_RUN=runRoot;
const compactEvidence = process.env.WORKBENCH_E2E_COMPACT === '1';

export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1, timeout: 30000,
  expect: { timeout: 7000 },
  reporter: compactEvidence ? [['line']] : [['list'], ['html', { open: 'never', outputFolder: join(runRoot,'report') }]],
  outputDir: join(runRoot,'results'),
  use: {
    baseURL: 'http://127.0.0.1:5380',
    trace: compactEvidence ? { mode: 'retain-on-failure', screenshots: false, attachments: false } : 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: compactEvidence ? 'off' : 'retain-on-failure',
    reducedMotion: 'reduce',
  },
  projects: [
    { name: 'chromium', testMatch: /(?:workshop|workflow|catalog|operating|intake|models|verification|admission|implementation|preparation|regression-preparation|task-to-pr|task-session|bug-workflow|output-isolation)\.spec\.ts/, use: { browserName: 'chromium', viewport: { width: 1440, height: 1000 } } },
    { name: 'visual', testMatch: /visual\.spec\.ts/, use: { browserName: 'chromium' } },
  ],
  webServer: { command: '.venv/bin/python scripts/dev.py --e2e', url: 'http://127.0.0.1:5380/api/session', reuseExistingServer: false, timeout: 30000 },
});
