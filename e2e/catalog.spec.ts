import {artifactPath} from './output';
import { test, expect } from '@playwright/test';
import { fixture, capture, overflow, openReview, answerAll } from './helpers';

test.beforeEach(() => fixture('reset'));

test('navigate hierarchy, detail, search and browser Back/Forward', async ({ page }, info) => {
  await page.goto('/backlog?source=fixture');
  await page.getByRole('button', { name: 'Expand Make guide progress easier to follow' }).click();
  await page.getByRole('button', { name: 'Expand Clear, predictable step selection' }).click();
  await page.getByRole('link', { name: /Keep your place in a guide/ }).click();
  await expect(page.getByRole('heading', { name: 'Keep your place in a guide' })).toBeVisible();
  await expect(page.getByText('Recorded attempt outcome')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open fixture review' })).toBeVisible();
  await capture(page, info, 'm2-record-desktop');
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Backlog' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Collapse Clear, predictable step selection' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Keep your place in a guide/ })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', { name: 'Keep your place in a guide' })).toBeVisible();
  await page.getByRole('link', { name: 'Backlog', exact:true }).last().click();
  await page.getByLabel('Find a record').fill('keep your place');
  await page.getByRole('button', { name:'Search', exact:true }).click();
  await expect(page.getByText('Matching records · ancestor context')).toBeVisible();
  await expect(page.getByRole('link', { name:/Keep your place in a guide/ })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Find a record')).toHaveValue('keep your place');
  await page.getByLabel('Find a record').fill('no-such-record');
  await page.getByRole('button', {name:'Search',exact:true}).click();
  await expect(page.getByText('No matching records.')).toBeVisible();
});

test('pilot stale evidence is read only, paged and never claims live activity', async ({page},info) => {
  await page.goto('/?source=pilot');
  await expect(page.getByText('Maintenance · stale observations')).toBeVisible();
  await expect(page.getByText('Current count unavailable')).toBeVisible();
  await expect(page.getByText('Current work isn’t confirmed.')).toBeVisible();
  await page.goto('/backlog?source=pilot');
  await page.getByRole('button',{name:'Expand Recorded guide improvement'}).click();
  await expect(page.getByText('28 recorded items · Page 1 of 3')).toBeVisible();
  await page.getByRole('button',{name:'Next',exact:true}).first().click();
  await expect(page.getByText('28 recorded items · Page 2 of 3')).toBeVisible();
  await page.getByRole('link',{name:/Recorded guide check 13/}).click();
  await expect(page.getByRole('heading',{name:'Recorded guide check 13'})).toBeVisible();
  await expect(page.getByRole('link',{name:'Open fixture review'})).toHaveCount(0);
  await expect(page.getByText(/Review, retry, scheduling and publication actions are unavailable/)).toBeVisible();
  await capture(page,info,'m2-pilot-stale-desktop');
  await page.goto('/history?source=pilot');
  await expect(page.getByLabel('Workspace source')).toHaveValue('pilot');
  await expect(page.getByText('29 recorded items · Page 1 of 2')).toBeVisible();
  await page.getByRole('button',{name:'Next',exact:true}).click();
  await expect(page.getByText('29 recorded items · Page 2 of 2')).toBeVisible();
});

test('saved review leaves actionable count and creates truthful history', async ({page}) => {
  await openReview(page); await answerAll(page);
  await page.getByRole('button',{name:'Save review decisions'}).click();
  await expect(page.getByRole('dialog').getByText('Saved · Pending application')).toBeVisible();
  await page.getByRole('button',{name:'Close review'}).click();
  await page.getByRole('link',{name:'All work'}).click();
  await expect(page.getByRole('heading',{name:'0 decisions'})).toBeVisible();
  await expect(page.getByText('1 saved decision waiting to apply.')).toBeVisible();
  fixture('worker');
  await page.goto('/history');
  await expect(page.getByRole('heading',{name:'Review applied',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Review decision saved',exact:true})).toBeVisible();
});

for(const width of [320,390,768,1440]) {
  test(`catalog layouts, text enlargement and forms fit ${width}px`,async ({page},info) => {
    await page.setViewportSize({width,height:900});
    await page.goto('/backlog?source=fixture');
    await page.getByRole('button',{name:'Expand Make guide progress easier to follow'}).click();
    await page.getByRole('button',{name:'Expand Clear, predictable step selection'}).click();
    await overflow(page);
    await capture(page,info,`m2-backlog-${width}`);
    await page.getByRole('link',{name:/Keep your place in a guide/}).click();
    await expect(page.getByRole('heading',{name:'Keep your place in a guide'})).toBeVisible();
    await overflow(page);
    if(width===390 || width===1440) await capture(page,info,`m2-detail-${width}`);
    await page.goto('/history');
    await expect(page.getByRole('heading',{name:'History',exact:true})).toBeVisible();
    await overflow(page);
    await capture(page,info,`m2-history-${width}`);
    await page.evaluate(()=>{ document.documentElement.style.fontSize='200%'; });
    await overflow(page);
    await page.getByLabel('Workspace source').selectOption('pilot');
    await expect(page.getByText('Maintenance · stale observations')).toBeVisible();
    await overflow(page);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:artifactPath(`screenshots/m2-enlarged-${width}.png`)});
  });
}

test('failed catalog requests can recover without inventing records', async ({page},info) => {
  await page.route('**/api/backlog*',route=>route.abort('failed'));
  await page.goto('/backlog?source=fixture');
  await expect(page.getByText('This view couldn’t be loaded.')).toBeVisible();
  await capture(page,info,'m2-backlog-error');
  await page.unroute('**/api/backlog*');
  await page.getByRole('button',{name:'Try again'}).click();
  await expect(page.getByRole('button',{name:'Expand Make guide progress easier to follow'})).toBeVisible();
});

test('fresh observations show recorded waiting reasons without implying inference', async ({page},info) => {
  fixture('pilot-fresh');
  await page.route('**/api/tasks', route => route.abort('failed'));
  await page.goto('/?source=pilot');
  await expect(page.getByText('Maintenance · fresh observation')).toBeVisible();
  await expect(page.getByRole('heading',{name:'Recorded waiting work'})).toBeVisible();
  await expect(page.getByText('Waiting automatically: Recorded fixture capacity gate is full.')).toBeVisible();
  await expect(page.getByText('Recorded working items; active inference is not confirmed.')).toBeVisible();
  await expect(page.getByRole('link',{name:'Open review',exact:true})).toHaveCount(0);
  await expect(page.locator('.quiet-count')).toHaveCount(0);
  await page.getByText('Workspace limits and evidence').click();
  await expect(page.getByText(/Maintenance observations are read only. Live review actions are unavailable/)).toBeVisible();
  await capture(page,info,'m2-fresh-waiting');
});

test('development file serving excludes backend and private runtime files', async ({page}) => {
  for (const path of ['backend/workbench/api.py','.local/e2e-db-path']) {
    const file=new URL('../'+path,import.meta.url);
    const response=await page.request.get('/@fs'+file.pathname);
    expect(response.status()).toBe(403);
  }
});

test('selectable demos exercise real pagination, hierarchy, search and mode separation', async ({page},info) => {
  fixture('pilot-fresh');
  await page.goto('/backlog?source=fixture');
  await page.getByLabel('Demo scenario').selectOption('mixed');
  await page.getByLabel('Backlog view').selectOption('all');
  await expect(page.getByText('16 top-level records · Page 1 of 2')).toBeVisible();
  await capture(page,info,'iteration-mixed-collapsed-desktop');
  await page.getByRole('button',{name:'Next',exact:true}).click();
  await expect(page.getByText('16 top-level records · Page 2 of 2')).toBeVisible();
  await page.getByRole('button',{name:'Previous',exact:true}).click();
  await expect(page.getByText('16 top-level records · Page 1 of 2')).toBeVisible();
  if (!await page.getByRole('button',{name:'Expand Readable guide progress'}).count()) {
    await page.getByRole('button',{name:'Next',exact:true}).click();
    await expect(page.getByText('16 top-level records · Page 2 of 2')).toBeVisible();
  }
  await page.getByRole('button',{name:'Expand Readable guide progress'}).click();
  const children = page.locator('.tree-item').filter({has:page.getByRole('button',{name:'Collapse Readable guide progress',exact:true})}).last().locator(':scope > .tree-children');
  await expect(children.getByText('14 recorded items · Page 1 of 2')).toBeVisible();
  await children.getByRole('button',{name:'Next',exact:true}).click();
  await expect(children.getByText('14 recorded items · Page 2 of 2')).toBeVisible();
  await page.getByRole('button',{name:/Expand Long player-facing/}).click();
  await overflow(page);
  await capture(page,info,'iteration-mixed-expanded-desktop');
  await page.setViewportSize({width:390,height:844});
  await overflow(page);
  await capture(page,info,'iteration-mixed-expanded-phone');
  await page.evaluate(()=>{document.documentElement.style.fontSize='200%';});
  await overflow(page);
  await capture(page,info,'iteration-mixed-enlarged-phone');
  await page.evaluate(()=>{document.documentElement.style.fontSize='';});
  await page.getByLabel('Find a record').fill('missing metadata');
  await page.getByRole('button',{name:'Search',exact:true}).click();
  await page.getByRole('link',{name:/Long player-facing/}).click();
  await expect(page.getByRole('heading',{name:/Long player-facing/})).toBeVisible();
  await page.goto('/history');
  await capture(page,info,'iteration-history-phone');
  await page.getByLabel('Workspace source').selectOption('pilot');
  await expect(page.getByLabel('Demo scenario')).toHaveCount(0);
  await expect(page.getByText('Maintenance · fresh observation')).toBeVisible();
  await page.getByLabel('Workspace source').selectOption('fixture');
  await page.getByLabel('Demo scenario').selectOption('empty');
  await page.goto('/backlog?source=fixture');
  await expect(page.getByText('No records to show.')).toBeVisible();
});


test('decision summary opens the relevant real review', async ({page}) => {
  await page.goto('/?source=fixture');
  await page.getByRole('link',{name:'Open decision queue'}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('radio')).toHaveCount(6);
});

test('combined filters preserve descendant context and restore unfiltered expansion', async ({page},info) => {
  await page.goto('/backlog?source=fixture');
  await page.getByLabel('Demo scenario').selectOption('mixed');
  await page.getByText('Filters and sorting', {exact:true}).click();
  await page.getByRole('group',{name:'Type',exact:true}).getByLabel('Task',{exact:true}).click();
  await expect(page.getByRole('group',{name:'Type',exact:true}).getByLabel('Task',{exact:true})).toBeChecked();
  await page.getByRole('group',{name:'State',exact:true}).getByLabel('Waiting for clarification',{exact:true}).click();
  await expect(page.getByRole('group',{name:'State',exact:true}).getByLabel('Waiting for clarification',{exact:true})).toBeChecked();
  await expect(page.locator('.context-row').first()).toBeVisible();
  await expect(page.getByText(/matching records \(ancestors are context\)/)).toBeVisible();
  await capture(page,info,'refinement-filtered-desktop');
  await page.setViewportSize({width:390,height:844}); await overflow(page);
  await capture(page,info,'refinement-filtered-phone');
  await page.evaluate(()=>{document.documentElement.style.fontSize='200%';}); await overflow(page);
  await page.evaluate(()=>{document.documentElement.style.fontSize='';});
  const original=page.url();
  await page.locator('.record-row:not(.context-row) .record-link').first().click();
  await expect(page.getByRole('heading',{name:'What this record tells us'})).toBeVisible();
  await page.getByRole('link',{name:'Backlog',exact:true}).last().click();
  await expect(page).toHaveURL(original);
  await page.getByRole('button',{name:'Clear all',exact:true}).click();
  await expect(page.getByRole('button',{name:'Expand Readable guide progress'})).toBeVisible();
});
