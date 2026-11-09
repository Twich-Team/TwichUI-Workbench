import { test, expect } from '@playwright/test';
import { fixture, answerAll, overflow, capture } from './helpers';

test.beforeEach(() => fixture('reset'));
for (const width of [1440, 1024, 768, 390, 320]) {
  test(`work, detail and long review fit ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: width < 500 ? 844 : 1000 });
    await page.goto('/?source=fixture');
    await expect(page.getByRole('link', { name: /Keep your place in a guide/ })).toBeVisible();
    await overflow(page);
    if ([1440, 390].includes(width)) { await capture(page, info, `work-${width}`); await expect(page).toHaveScreenshot(`work-${width}.png`, { fullPage: true }); }
    await page.getByRole('link', { name: /Keep your place in a guide/ }).click();
    await expect(page.getByRole('link', { name: 'Open review' })).toBeVisible();
    await overflow(page);
    if ([1440, 390].includes(width)) { await capture(page, info, `detail-${width}`); await expect(page).toHaveScreenshot(`detail-${width}.png`, { fullPage: true }); }
    fixture('long');
    await expect(page.getByRole('heading', { level: 1 })).toContainText('very long adventure');
    await page.getByRole('link', { name: 'Open review' }).click();
    await overflow(page);
    await answerAll(page);
    await page.getByLabel(/Anything to add/).fill('A long note with player feedback. '.repeat(45));
    await overflow(page);
    await page.getByRole('dialog').evaluate(el => { el.scrollTop = 0; });
    await capture(page, info, `long-review-${width}`);
    if ([1440, 390].includes(width)) await expect(page).toHaveScreenshot(`long-review-${width}.png`);
  });
}

test('loading, failed and empty states', async ({ page }, info) => {
  let release: () => void = () => {};
  const hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/tasks', async route => { await hold; await route.continue(); });
  await page.goto('/?source=fixture');
  await expect(page.getByText('Opening your work…')).toBeVisible();
  await capture(page, info, 'loading');
  release();
  await expect(page.getByRole('link', { name: /Keep your place in a guide/ })).toBeVisible();
  await page.unroute('**/api/tasks');
  await page.route('**/api/tasks', route => route.abort('failed'));
  await page.reload();
  await expect(page.getByText('Your work couldn’t be loaded.')).toBeVisible();
  await capture(page, info, 'failed');
  await page.unroute('**/api/tasks');
  fixture('empty');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByText('All clear for now.')).toBeVisible();
  await capture(page, info, 'empty'); await overflow(page);
});

test('pending application stays explicit on a phone', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/tasks/cozy-step-review/review');
  await expect(page.getByRole('dialog')).toBeVisible();
  await answerAll(page);
  await page.getByRole('button', { name: 'Save review decisions' }).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  await page.getByRole('dialog').evaluate(el => { el.scrollTop = 0; });
  await capture(page, info, 'phone-pending');
  await overflow(page);
  await page.getByRole('button', { name: 'Close review' }).click();
  await page.getByRole('link', { name: 'All work' }).click();
  await expect(page.locator('.task-card').filter({has:page.getByRole('link', { name: /Keep your place in a guide/ })})).toContainText('Decision saved · Waiting to apply');
  await expect(page.getByText('Needs your review', { exact: true })).not.toBeVisible();
  await capture(page, info, 'phone-pending-work');
});

test('missing descriptive metadata has honest fallbacks', async ({ page }, info) => {
  fixture('missing');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?source=fixture');
  await expect(page.getByText('Area not recorded')).toBeVisible();
  await expect(page.getByText('No summary was recorded for this task.')).toBeVisible();
  await overflow(page);
  await capture(page, info, 'missing-metadata');
});

for (const width of [390,1440]) {
  test(`catalog regression views at ${width}px`, async ({page},info) => {
    await page.setViewportSize({width,height:1000});
    await page.goto('/backlog?source=fixture');
    await page.getByRole('button',{name:'Expand Make guide progress easier to follow'}).click();
    await page.getByRole('button',{name:'Expand Clear, predictable step selection'}).click();
    await expect(page.getByRole('link',{name:/Keep your place in a guide/})).toBeVisible();
    await capture(page,info,`m2-backlog-${width}`);
    await expect(page).toHaveScreenshot(`catalog-backlog-${width}.png`,{fullPage:true});
    await page.getByRole('link',{name:/Keep your place in a guide/}).click();
    await expect(page.getByRole('heading',{name:'Keep your place in a guide'})).toBeVisible();
    await capture(page,info,`m2-detail-${width}`);
    await expect(page).toHaveScreenshot(`catalog-detail-${width}.png`,{fullPage:true});
    await page.goto('/history');
    await expect(page.getByRole('heading',{name:'Guide inspection found no additional changes'})).toBeVisible();
    await capture(page,info,`m2-history-${width}`);
    await expect(page).toHaveScreenshot(`catalog-history-${width}.png`,{fullPage:true});
  });
}

for(const width of [1440,390])test(`selected workshop dashboard and everyday entry ${width}px`,async({page},info)=>{
 await page.setViewportSize({width,height:width===390?844:1000});
 await page.goto('/');await expect(page.getByText('No work running',{exact:true})).toBeVisible();
 await overflow(page);await capture(page,info,`selected-dashboard-${width}`);
 await expect(page).toHaveScreenshot(`selected-dashboard-${width}.png`,{fullPage:true,mask:[page.locator('.freshness-note')]});
 await page.getByRole('link',{name:'Create a task',exact:true}).filter({visible:true}).click();
 await expect(page.getByLabel('Description',{exact:true})).toBeVisible();await overflow(page);
 await expect(page).toHaveScreenshot(`selected-task-entry-${width}.png`,{fullPage:true});
});
