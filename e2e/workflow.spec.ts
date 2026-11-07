import { spawn } from 'node:child_process';
import { test, expect } from '@playwright/test';
import { fixture, openReview, answerAll, overflow, capture } from './helpers';

test.beforeEach(async ({ page }) => {
  fixture('reset');
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.hostname === '127.0.0.1' ? route.continue() : route.abort();
  });
});

test('navigate, save, recover a crashed worker, and display the actual outcome', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await openReview(page);
  await answerAll(page);
  await page.getByLabel(/Anything to add/).fill('The selected step should stay put.');
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('crash');
  expect(fixture('count')).toBe('0');
  await page.reload();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('worker');
  await expect(page.getByRole('dialog').getByText('Applied', { exact: true })).toBeVisible();
  fixture('worker');
  expect(fixture('count')).toBe('1');
  await page.getByRole('button', { name: 'Close review' }).click();
  await expect(page.getByText('Review confirmed', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Not published', { exact: true })).toBeVisible();
  await overflow(page);
  await capture(page, info, 'desktop-applied');
  await page.getByRole('link', { name: 'All work' }).click();
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Keep your place in a guide' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('a lost submission response recovers the same durable receipt after retry', async ({ page }) => {
  let submitted: string | null = null;
  let calls = 0;
  await page.route('**/api/tasks/*/decisions', async route => {
    calls++;
    submitted = route.request().postData();
    await route.fetch(); // The actual API commits, then the browser loses the response.
    await route.abort('connectionreset');
  });
  await openReview(page);
  await answerAll(page);
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  await page.unroute('**/api/tasks/*/decisions');
  const retry = await page.evaluate(async body => {
    const session = await (await fetch('/api/session')).json();
    return (await fetch('/api/tasks/cozy-step-review/decisions', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body })).json();
  }, submitted);
  expect(retry.id).toBe(JSON.parse(submitted!).command_id);
  expect(calls).toBe(1);
  fixture('worker'); fixture('worker');
  await expect(page.getByRole('dialog').getByText('Applied', { exact: true })).toBeVisible();
  expect(fixture('count')).toBe('1');
});

test('contention preserves choices and retries the exact command', async ({ page }) => {
  await openReview(page); await answerAll(page);
  const lock = spawn('.venv/bin/python', ['scripts/fixture_ctl.py', 'lock-until-release'], { env: { ...process.env, PYTHONPATH: 'backend' } });
  await new Promise<void>(resolve => lock.stdout.once('data', () => resolve()));
  const released = new Promise<void>(resolve => lock.once('exit', () => resolve()));
  let draft: string | null = null;
  try {
    await page.getByRole('button', { name: 'Save review decisions' }).click();
    await expect(page.getByRole('alert')).toContainText('busy');
    draft = await page.evaluate(() => localStorage.getItem('review:cozy-step-review'));
  } finally {
    lock.stdin.end('release\n');
    await released;
  }
  await page.getByRole('button', { name: 'Retry same decision' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('review:cozy-step-review'))).toBe(draft);
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('worker');
  await expect(page.getByRole('dialog').getByText('Applied', { exact: true })).toBeVisible();
});

test('phone changes request retains hold and supports accessible drawer navigation', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openReview(page); await answerAll(page, 'Needs changes');
  await page.getByLabel(/Anything to add/).fill('Please clarify the fallback behavior.');
  await overflow(page); await capture(page, info, 'phone-review-form');
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('worker');
  await expect(page.getByRole('dialog').getByText('Applied', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('link', { name: 'View saved review' })).toBeFocused();
  await expect(page.getByText('Changes requested', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('The contract hold is still in place.')).toBeVisible();
  await overflow(page);
});

test('stale saved decisions are visible and can only be replaced after refreshed review', async ({ page }, info) => {
  await openReview(page); await answerAll(page);
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('advance'); fixture('worker');
  await expect(page.getByRole('dialog').getByText('Needs another look', { exact: true })).toBeVisible();
  expect(fixture('count')).toBe('0');
  await capture(page, info, 'desktop-stale');
  await page.getByRole('button', { name: 'Open refreshed review' }).click();
  await expect(page.getByRole('dialog')).toContainText('The refreshed contract');
  for (const radio of await page.getByRole('radio', { name: 'Confirm this behavior' }).all()) await expect(radio).not.toBeChecked();
  await answerAll(page); await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  fixture('worker');
  await expect(page.getByRole('dialog').getByText('Applied', { exact: true })).toBeVisible();
  expect(fixture('count')).toBe('1');
});

test('background evidence refresh never silently rebinds an open form', async ({ page }) => {
  await openReview(page); await answerAll(page);
  fixture('advance');
  await expect(page.getByRole('dialog').getByText('The evidence has changed.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save review decisions' })).not.toBeVisible();
  await expect(page.getByRole('radio', { name: 'Confirm this behavior' }).first()).toBeChecked();
});

test('resizing and a failed background refresh preserve an open review', async ({ page }) => {
  await openReview(page);
  await page.getByRole('radio', { name: 'Confirm this behavior' }).first().check();
  await page.getByLabel(/Anything to add/).fill('Keep this note while resizing.');
  for (const width of [768, 320, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await overflow(page);
    await expect(page.getByRole('radio', { name: 'Confirm this behavior' }).first()).toBeChecked();
    await expect(page.getByLabel(/Anything to add/)).toHaveValue('Keep this note while resizing.');
  }
  await page.route('**/api/tasks/cozy-step-review', route => route.abort('failed'));
  await expect(page.getByText('Refresh is unavailable. Showing the last loaded task. Your choices are kept.')).toBeAttached();
  await expect(page.getByLabel(/Anything to add/)).toHaveValue('Keep this note while resizing.');
  await page.unroute('**/api/tasks/cozy-step-review');
  await answerAll(page);
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
});
