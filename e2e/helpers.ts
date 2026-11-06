import {artifactPath} from './output';
import { execFileSync } from 'node:child_process';
import { expect, type Page, type TestInfo } from '@playwright/test';
import { appendFileSync, mkdirSync } from 'node:fs';

export const fixture = (action: string) => execFileSync('.venv/bin/python', ['scripts/fixture_ctl.py', action], { env: { ...process.env, PYTHONPATH: 'backend' }, encoding: 'utf8' }).trim();
export async function openReview(page: Page) {
  await page.goto('/?source=fixture');
  await page.getByRole('link', { name: /Keep your place in a guide/ }).click();
  await page.getByRole('link', { name: 'Open review', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
}
export async function answerAll(page: Page, choice = 'Confirm this behavior') {
  const radios = page.getByRole('radio', { name: choice, exact: true });
  await expect(radios).toHaveCount(3);
  for (const radio of await radios.all()) await radio.check();
  for (const radio of await radios.all()) await expect(radio).toBeChecked();
}
export async function overflow(page: Page) {
  const result = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll('body *')].filter(el => {
      const box = el.getBoundingClientRect();
      return box.width > 0 && box.right > innerWidth + 1 && getComputedStyle(el).position !== 'fixed';
    }).map(el => `${el.tagName}.${el.className}`).slice(0, 15) }));
  appendFileSync(artifactPath('overflow.jsonl'), JSON.stringify({ at: new Date().toISOString(), url: page.url(), ...result }) + '\n');
  expect(result, JSON.stringify(result)).toMatchObject({ scroll: result.width });
}
export async function capture(page: Page, info: TestInfo, name: string) {
  mkdirSync(artifactPath('screenshots'), { recursive: true });
  const path = artifactPath(`screenshots/${name}.png`);
  if (!(await page.getByRole('dialog').isVisible())) await page.evaluate(() => window.scrollTo(0,0));
  await page.screenshot({ path, fullPage: !(await page.getByRole('dialog').isVisible()) });
  await info.attach(name, { path, contentType: 'image/png' });
}
