import {artifactPath} from './output';
import {test, expect, type Page} from '@playwright/test';
import {readFileSync, mkdirSync} from 'node:fs';
import {fixture, overflow} from './helpers';

const example = JSON.parse(readFileSync('fixtures/intake/admission-rehearsal.json','utf8'));
test.beforeEach(()=>fixture('reset'));

async function ready(page:Page) {
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Required').fill(example.request.title);
  await page.getByLabel('Description Required').fill(example.request.description);
  await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('planning');
  const approve = page.getByRole('button',{name:'Approve this plan',exact:true});
  await expect(approve).toBeEnabled();
  mkdirSync(artifactPath('m5-plain-language/screenshots'),{recursive:true});
  await page.screenshot({path:artifactPath(`m5-plain-language/screenshots/plan-${page.viewportSize()!.width}.png`),fullPage:true});
  await approve.click();
  await expect(page.getByRole('region',{name:'Latest plan decision'}).getByText('Saved · Pending application',{exact:true})).toBeVisible();
  await expect(page.getByText('Plan approved',{exact:true})).toHaveCount(0);
  fixture('apply-planning');
  await expect(page.getByRole('region',{name:'Latest plan decision'}).getByText('Plan approved',{exact:true})).toBeVisible();
  fixture('planning');
  const box = page.getByRole('region',{name:'Approval check'});
  await expect(box.getByText('This task has a current approved plan',{exact:true})).toBeVisible();
  const identity = page.url().split('/intake/')[1].split('?')[0];
  const data = await (await page.request.get('/api/intake/'+identity)).json();
  const task = data.tasks.find((t:{kind:string})=>t.kind==='task');
  return {box, path:`/api/intake/${identity}/tasks/${task.id}/admission`};
}

async function capture(page:Page, name:string) {
  mkdirSync(artifactPath('m5-plain-language/screenshots'),{recursive:true});
  await overflow(page);
  await page.getByRole('region',{name:'Approval check'}).screenshot({path:artifactPath(`m5-plain-language/screenshots/${name}.png`)});
  if(name.startsWith('admitted-')) {
    await page.getByRole('region',{name:'Approval check'}).evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:artifactPath(`m5-plain-language/screenshots/${name}-context.png`)});
  }
}

for (const width of [1440,390]) test(`ordinary approval to durable admission with keyboard at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {box,path} = await ready(page);
  const action = box.getByRole('button',{name:'Check this task’s approval',exact:true});
  await box.getByText('Technical details and previous checks',{exact:true}).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(action).toBeFocused();
  expect(await action.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none');
  await capture(page,`ready-keyboard-${width}`);
  await page.keyboard.press('Enter');
  await expect(box.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  await capture(page,`pending-${width}`);
  await page.reload();
  await expect(box.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  fixture('crash');
  expect((await (await page.request.get(path)).json()).receipts[0].status).toBe('pending');
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Approval check passed'})).toBeVisible();
  await expect(box.getByText('No addon changes',{exact:true})).toBeVisible();
  await expect(box.getByText('This sample flow does not build or run anything, test the addon, or publish changes.',{exact:false})).toBeVisible();
  await capture(page,`admitted-${width}`);
  if(width===390) {
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    await capture(page,'admitted-390-200');
  }
});

test('lost admission response reconciles the exact request and one result',async({page})=>{
  const {box,path} = await ready(page);
  let sent = '';
  await page.route('**/admission',async route=>{
    if(route.request().method()==='POST') {sent=route.request().postData()!;await route.fetch();await route.abort();}
    else await route.continue();
  });
  await box.getByRole('button',{name:'Check this task’s approval',exact:true}).click();
  await expect(box.getByRole('alert')).toBeVisible();
  await page.unroute('**/admission');
  await page.reload();
  await expect(box.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  const requests:string[]=[];
  page.on('request',r=>{if(r.method()==='POST' && r.url().endsWith('/admission')) requests.push(r.postData()!);});
  await box.getByRole('button',{name:'Recover saved check'}).click();
  await expect.poll(()=>requests).toEqual([sent]);
  await expect(box.getByRole('button',{name:'Recover saved check'})).toBeEnabled();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Approval check passed'})).toBeVisible();
  await box.getByRole('button',{name:'Recover saved check'}).click();
  await expect.poll(()=>requests).toEqual([sent,sent]);
  expect((await (await page.request.get(path)).json()).receipts).toHaveLength(1);
});

for (const change of ['revoke','stale']) test(`authority ${change} after save prevents admission`,async({page})=>{
  const {box} = await ready(page);
  await box.getByRole('button',{name:'Check this task’s approval',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  fixture('admission-'+change);fixture('worker');
  if (change==='revoke') await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await expect(box.getByRole('heading',{name:change==='revoke'?'Approval check couldn’t pass':'This check is out of date'})).toBeVisible();
  await expect(box.getByText('This task needs attention',{exact:true})).toBeVisible();
  await expect(box.getByText('Previous check · Does not confirm approval of the current task.',{exact:true})).toBeVisible();
  await expect(box.locator('.outcome').getByText(change==='revoke'?'A current approved plan is needed. Review the latest plan and approve it if you agree.':'The plan or task changed after this check was saved. Review the latest plan before checking again.',{exact:true})).toBeVisible();
  await capture(page,change==='revoke'?'blocked-1440':'stale-1440');
});

test('an admitted historical receipt does not represent current authority after a human scope revision',async({page})=>{
  const {box,path} = await ready(page);
  await box.getByRole('button',{name:'Check this task’s approval',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Approval check passed'})).toBeVisible();
  const before = (await (await page.request.get(path)).json()).receipts[0];
  await page.getByText('Read the plan',{exact:true}).click();
  await page.getByText('In-game scope and attachments',{exact:true}).click();
  await page.getByText('Change what this plan may cover',{exact:true}).click();
  await page.getByLabel('Why are you changing the scope?').fill('A new explicit human scope revision needs a new approval.');
  await page.getByRole('button',{name:'Save and review the updated plan',exact:true}).click();
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await expect(box.getByText('This task needs attention',{exact:true})).toBeVisible();
  await expect(box.getByRole('heading',{name:'Previous check passed'})).toBeVisible();
  await expect(box.getByText('This earlier check passed, but it does not confirm approval of the task as it is now.',{exact:true})).toBeVisible();
  expect((await (await page.request.get(path)).json()).receipts[0]).toEqual(before);
  await capture(page,'historical-admitted-1440');
});

test('missing applied approval prevents the action without removing receipt history',async({page})=>{
  const {box,path} = await ready(page);
  fixture('admission-revoke');
  await expect(box.getByText('This task needs attention',{exact:true})).toBeVisible();
  await expect(box.getByRole('button',{name:'Check this task’s approval',exact:true})).toHaveCount(0);
  expect((await (await page.request.get(path)).json()).receipts).toEqual([]);
  await capture(page,'authority-required-1440');
});

test('plain wording preserves an unfamiliar revised plan sentence',async({page})=>{
  const {path}=await ready(page);
  const intakePath=path.split('/tasks/')[0];
  await page.route('**'+intakePath,async route=>{
    const response=await route.fetch();
    const data=await response.json();
    data.proposals[0].content.scope=['A revised plan sentence must remain visible exactly.'];
    await route.fulfill({response,json:data});
  });
  await page.reload();
  await page.getByText('Read the plan',{exact:true}).click();
  await expect(page.getByText('A revised plan sentence must remain visible exactly.',{exact:true})).toBeVisible();
});
