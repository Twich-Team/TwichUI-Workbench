import {artifactPath} from './output';
import {test,expect,type Page} from '@playwright/test';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {fixture,overflow} from './helpers';

const definition = JSON.parse(readFileSync('fixtures/intake/admission-rehearsal.json','utf8'));
test.beforeEach(()=>fixture('reset'));

async function prepare(page:Page) {
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Required').fill(definition.request.title);
  await page.getByLabel('Description Required').fill(definition.request.description);
  await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('planning');
  await page.getByRole('button',{name:'Approve this plan',exact:true}).click();
  fixture('apply-planning');fixture('planning');
  const approval = page.getByRole('region',{name:'Approval check'});
  await approval.getByRole('button',{name:'Check this task’s approval',exact:true}).click();
  await expect(approval.getByRole('heading',{name:'Waiting for approval check'})).toBeVisible();
  fixture('worker');
  await expect(approval.getByRole('heading',{name:'Approval check passed',exact:true})).toBeVisible();
  const box = page.getByRole('region',{name:'Sample implementation'});
  await expect(box.getByRole('button',{name:'Make sample',exact:true})).toBeEnabled();
  const intakeId = page.url().split('/intake/')[1].split('?')[0];
  const intake = await (await page.request.get('/api/intake/'+intakeId)).json();
  const task = intake.tasks.find((t:{kind:string})=>t.kind==='task');
  const path = `/api/intake/${intakeId}/tasks/${task.id}/implementation`;
  return {box,path};
}

async function capture(page:Page,name:string) {
  mkdirSync(artifactPath('m5-implementation/screenshots'),{recursive:true});
  await overflow(page);
  const box = page.getByRole('region',{name:'Sample implementation'});
  await box.screenshot({path:artifactPath(`m5-implementation/screenshots/${name}.png`)});
  if(name==='candidate-1440' || name==='passed-390') {
    await box.evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:artifactPath(`m5-implementation/screenshots/${name}-context.png`)});
  }
}

async function make(page:Page) {
  const result = await prepare(page);
  await result.box.getByRole('button',{name:'Make sample',exact:true}).click();
  await expect(result.box.getByRole('heading',{name:'Sample request saved',exact:true})).toBeVisible();
  fixture('worker');
  await expect(result.box.getByRole('heading',{name:'Sample prepared',exact:true})).toBeVisible();
  return result;
}

for(const width of [1440,390]) test(`approved sample to simulated candidate to explicit verification at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {box,path} = await prepare(page);
  const action = box.getByRole('button',{name:'Make sample',exact:true});
  await box.getByText('Sample content and technical details',{exact:true}).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(action).toBeFocused();
  expect(await action.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none');
  await capture(page,`ready-keyboard-${width}`);
  await page.keyboard.press('Enter');
  await expect(box.getByRole('heading',{name:'Sample request saved',exact:true})).toBeVisible();
  await expect(box.getByText('Your request is saved and waiting for the worker. No simulated attempt is recorded yet.',{exact:true})).toBeVisible();
  await capture(page,`pending-${width}`);
  await page.reload();
  await expect(box.getByRole('heading',{name:'Sample request saved',exact:true})).toBeVisible();
  fixture('crash');
  expect((await (await page.request.get(path)).json()).receipts[0].attempt).toBeNull();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample prepared',exact:true})).toBeVisible();
  await expect(box.getByRole('heading',{name:'Sample has not been checked',exact:true})).toBeVisible();
  await expect(box.getByText('Real execution denied · Publication held',{exact:true})).toBeVisible();
  const candidate = (await (await page.request.get(path)).json()).receipts[0].candidate;
  expect(candidate.verification).toBeNull();
  expect(candidate.execution_allowed).toBe(false);expect(candidate.publication_allowed).toBe(false);
  await capture(page,`candidate-${width}`);
  await box.getByRole('button',{name:'Check this sample',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting for sample check',exact:true})).toBeVisible();
  await page.reload();
  await expect(box.getByRole('heading',{name:'Waiting for sample check',exact:true})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample check passed',exact:true})).toBeVisible();
  await expect(box.getByText('These checks compare the saved sample with the expected label examples. They are not addon verification or human acceptance.',{exact:true})).toBeVisible();
  await capture(page,`passed-${width}`);
  const result = (await (await page.request.get(path)).json()).receipts[0].candidate.verification;
  expect(result.verification).toBe('passed');expect(result.execution_allowed).toBe(false);expect(result.publication_allowed).toBe(false);
  writeFileSync(artifactPath(`m5-implementation/workflow-${width}.json`),JSON.stringify({source:'automated-disposable-fixture',path,context:await (await page.request.get(path)).json(),real_addon_verification:false},null,2)+'\n');
  await box.getByText('Sample content and technical details',{exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(box.locator('pre').first()).toContainText('Two  inner spaces');
  await capture(page,`details-${width}`);
  if(width===390){
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    await capture(page,'details-390-200');
    await page.keyboard.press('Enter');
    await capture(page,'passed-390-200');
  }
});

test('lost sample and check responses recover the exact commands without extra work',async({page})=>{
  const {box,path} = await prepare(page);
  let sampleBody = '',checkBody = '';
  await page.route('**/implementation',async route=>{
    if(route.request().method()==='POST'){sampleBody=route.request().postData()!;await route.fetch();await route.abort();}
    else await route.continue();
  });
  await box.getByRole('button',{name:'Make sample',exact:true}).click();
  await expect(box.getByRole('alert')).toBeVisible();
  await page.unroute('**/implementation');
  await box.getByText('Sample content and technical details',{exact:true}).click();
  const requests:string[]=[];
  page.on('request',r=>{if(r.method()==='POST' && (r.url().endsWith('/implementation')||r.url().endsWith('/verification'))) requests.push(r.postData()!);});
  await box.getByRole('button',{name:'Recover saved sample request',exact:true}).click();
  await expect.poll(()=>requests).toEqual([sampleBody]);
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample prepared',exact:true})).toBeVisible();
  await page.route('**/candidates/*/verification',async route=>{checkBody=route.request().postData()!;await route.fetch();await route.abort();});
  await box.getByRole('button',{name:'Check this sample',exact:true}).click();
  await expect(box.getByRole('alert')).toBeVisible();
  await page.unroute('**/candidates/*/verification');
  await box.getByRole('button',{name:'Recover saved sample check',exact:true}).click();
  await expect.poll(()=>requests).toEqual([sampleBody,checkBody,checkBody]);
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample check passed',exact:true})).toBeVisible();
  const data = (await (await page.request.get(path)).json()).receipts;
  expect(data).toHaveLength(1);expect(data[0].candidate.verification.id).toBe(JSON.parse(checkBody).command_id);
});

for(const change of ['revoke','stale']) test(`changed authority ${change} after save prevents a simulated attempt`,async({page})=>{
  const {box,path} = await prepare(page);
  await box.getByRole('button',{name:'Make sample',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Sample request saved',exact:true})).toBeVisible();
  fixture('admission-'+change);fixture('worker');
  await expect(box.getByRole('heading',{name:change==='revoke'?'Sample needs an approved plan':'Sample request is out of date',exact:true})).toBeVisible();
  const receipt = (await (await page.request.get(path)).json()).receipts[0];
  expect(receipt.attempt).toBeNull();expect(receipt.candidate).toBeNull();
  await expect(box.getByRole('button',{name:'Make sample',exact:true})).toHaveCount(0);
  await capture(page,`authority-${change}`);
});

test('historical successful sample and check remain distinct from current approval',async({page})=>{
  const {box,path} = await make(page);
  await box.getByRole('button',{name:'Check this sample',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting for sample check',exact:true})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample check passed',exact:true})).toBeVisible();
  const before = (await (await page.request.get(path)).json()).receipts[0].candidate;
  await page.getByText('Read the plan',{exact:true}).click();
  await page.getByText('Change what this plan may cover',{exact:true}).click();
  await page.getByLabel('Why are you changing the scope?').fill('A new human scope revision must not inherit historical candidate authority.');
  await page.getByRole('button',{name:'Save and review the updated plan',exact:true}).click();
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await expect(box.getByRole('heading',{name:'Earlier sample prepared',exact:true})).toBeVisible();
  await expect(box.getByRole('heading',{name:'Earlier sample check passed',exact:true})).toBeVisible();
  await expect(box.getByText('A current approval check is needed',{exact:true})).toBeVisible();
  const after = (await (await page.request.get(path)).json()).receipts[0].candidate;
  expect({...after,current:true}).toEqual(before);
  await capture(page,'historical-success-current-invalid');
});

test('sample refresh errors preserve results and hold new actions',async({page})=>{
  const {box} = await make(page);
  await page.route('**/implementation',route=>route.fulfill({status:503,json:{detail:'Synthetic unavailable'}}));
  await expect(box.getByRole('alert')).toBeVisible({timeout:15000});
  await expect(box.getByRole('heading',{name:'Sample prepared',exact:true})).toBeVisible();
  await expect(box.getByRole('button',{name:'Check this sample',exact:true})).toBeDisabled();
  await page.unroute('**/implementation');
  await box.getByRole('button',{name:'Reload samples',exact:true}).click();
  await expect(box.getByRole('button',{name:'Check this sample',exact:true})).toBeEnabled();
});

test('missing sample content remains visible as history and holds checking on a phone',async({page})=>{
  await page.setViewportSize({width:390,height:1000});
  const {box,path} = await make(page);
  fixture('sample-artifact-missing');
  await expect(box.getByText('Sample integrity could not be confirmed. Checking is held.',{exact:true})).toBeVisible();
  await expect(box.getByText('Checking is held because the sample or its current approval could not be confirmed. Your saved history is kept.',{exact:true})).toBeVisible();
  await expect(box.getByRole('button',{name:'Check this sample',exact:true})).toHaveCount(0);
  const data = (await (await page.request.get(path)).json()).receipts[0];
  expect(data.status).toBe('completed');expect(data.candidate.artifact_text).toBeNull();expect(data.candidate.current).toBe(false);
  await capture(page,'missing-content-390');
});

test('approval changes after a sample check is saved stop verification',async({page})=>{
  const {box,path} = await make(page);
  await box.getByRole('button',{name:'Check this sample',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting for sample check',exact:true})).toBeVisible();
  fixture('admission-stale');fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample check is out of date',exact:true})).toBeVisible();
  const check = (await (await page.request.get(path)).json()).receipts[0].candidate.verification;
  expect(check.started_at).toBeNull();expect(check.verification).toBe('unknown');expect(check.checks).toEqual([]);
  await capture(page,'check-stale-after-save');
});
