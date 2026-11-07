import {artifactPath} from './output';
import {test,expect,type Page} from '@playwright/test';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {fixture,overflow} from './helpers';

const definition = JSON.parse(readFileSync('fixtures/intake/admission-rehearsal.json','utf8'));
const folder = artifactPath('m5-pr-preparation');
test.beforeEach(()=>fixture('reset'));

async function ready(page:Page) {
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Required').fill(definition.request.title);
  await page.getByLabel('Description Required').fill(definition.request.description);
  await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('planning');
  await page.getByRole('button',{name:'Approve this plan',exact:true}).click();
  fixture('apply-planning');fixture('planning');
  await page.getByRole('region',{name:'Approval check'}).getByRole('button',{name:'Check this task’s approval',exact:true}).click();
  fixture('worker');
  const implementation = page.getByRole('region',{name:'Sample implementation'});
  await implementation.getByRole('button',{name:'Make sample',exact:true}).click();
  fixture('worker');
  await implementation.getByRole('button',{name:'Check this sample',exact:true}).click();
  fixture('worker');
  const box = page.getByRole('region',{name:'Change review and sample PR'});
  await box.getByRole('button',{name:'Review changes',exact:true}).click();
  await expect(box.getByText('Current approval and sample checks confirmed.',{exact:true})).toBeVisible();
  const updated=box.getByRole('button',{name:'Review updated changes',exact:true});
  if(await updated.isVisible()) await updated.click();
  await expect(box.getByRole('button',{name:'Prepare sample PR',exact:true})).toBeEnabled();
  const intakeId = page.url().split('/intake/')[1].split('?')[0];
  const intake = await (await page.request.get('/api/intake/'+intakeId)).json();
  const task = intake.tasks.find((t:{kind:string})=>t.kind==='task');
  const path = `/api/intake/${intakeId}/tasks/${task.id}/implementation`;
  const candidate = (await (await page.request.get(path)).json()).receipts[0].candidate;
  const root = path.replace(/implementation$/,`candidates/${candidate.id}`);
  return {box,root,candidate};
}
async function shot(page:Page,name:string) {
  mkdirSync(folder+'/screenshots',{recursive:true});
  await overflow(page);
  const box = page.getByRole('region',{name:'Change review and sample PR'});
  await box.screenshot({path:`${folder}/screenshots/${name}.png`});
  if(name.startsWith('review-') || name.startsWith('prepared-')) {
    await box.evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:`${folder}/screenshots/${name}-context.png`});
  }
}

for(const width of [1440,390]) test(`review to explicit sample PR through existing worker at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {box,root,candidate} = await ready(page);
  await expect(box.getByLabel('Unified diff for sample/label-examples.json')).toContainText('Sample label');
  await expect(box.getByText('modified · +4 / −4',{exact:true})).toBeVisible();
  await shot(page,`review-${width}`);
  await box.getByLabel('Sample PR title').fill('Sample: label spacing review');
  await box.getByLabel('Primary category').selectOption('Reliability');
  await expect(box.getByLabel('Sample PR body preview')).toContainText('Category: Reliability');
  const bodyScroll=box.getByLabel('Sample PR body preview');
  await bodyScroll.focus();await page.keyboard.press('PageDown');
  await expect.poll(()=>bodyScroll.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  const action = box.getByRole('button',{name:'Prepare sample PR',exact:true});
  await box.getByText('Controlled publisher test settings',{exact:true}).focus();
  await page.keyboard.press('Tab');
  await expect(action).toBeFocused();
  expect(await action.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none');
  await shot(page,`keyboard-${width}`);
  const preview = await box.getByLabel('Sample PR body preview').textContent();
  await page.keyboard.press('Enter');
  await expect(box.getByRole('heading',{name:'Waiting',exact:true})).toBeVisible();
  await expect(box.locator('.outcome').getByText('Request saved. Waiting for the worker to prepare a sample PR. Nothing was published.',{exact:true})).toBeVisible();
  await shot(page,`waiting-${width}`);
  await page.reload();
  await expect(box.getByRole('heading',{name:'Waiting',exact:true})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  const result = (await (await page.request.get(root+'/review')).json()).history[0];
  expect(result.candidate_id).toBe(candidate.id);expect(result.preview.body).toBe(preview);
  expect(result.preview.title).toBe('Sample: label spacing review');
  expect(result.settings.category).toBe('Reliability');expect(result.result.url).toBeNull();
  expect(result.result.real_pr_created).toBe(false);expect(result.publication_allowed).toBe(false);
  mkdirSync(folder,{recursive:true});writeFileSync(`${folder}/workflow-${width}.json`,JSON.stringify(result,null,2));
  await shot(page,`prepared-${width}`);
  await box.getByRole('button',{name:'Review changes',exact:true}).click();
  await expect(box.getByLabel('Sample PR title')).toHaveValue('Sample: label spacing review');
  await box.getByText('Saved preview and preparation evidence',{exact:true}).click();
  await shot(page,`evidence-${width}`);
  if(width===390) {
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    await shot(page,'prepared-390-200');
  }
});

test('lost HTTP response recovers exact request; fake lost-response reconciles one result',async({page})=>{
  const {box,root} = await ready(page);
  await box.getByText('Controlled publisher test settings',{exact:true}).click();
  await box.getByLabel('Simulated publisher outcome').selectOption('lost-response');
  let original='';
  await page.route('**/sample-pr',async route=>{original=route.request().postData()!;await route.fetch();await route.abort();});
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  await expect(box.getByRole('alert')).toBeVisible();
  await page.unroute('**/sample-pr');
  const posts:string[]=[];
  page.on('request',r=>{if(r.url().endsWith('/sample-pr')&&r.method()==='POST')posts.push(r.postData()!);});
  await box.getByRole('button',{name:'Recover saved sample PR request',exact:true}).click();
  await expect.poll(()=>posts).toEqual([original]);
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Checking the saved sample result',exact:true})).toBeVisible();
  await shot(page,'reconciling');
  await page.reload();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  const context = await (await page.request.get(root+'/review')).json();
  expect(context.history).toHaveLength(1);expect(context.history[0].result.reconciled).toBe(true);
  await box.getByRole('button',{name:'Recover saved sample PR request',exact:true}).click();
  await expect.poll(()=>posts).toEqual([original,original]);
  await shot(page,'recovered');
});

test('rejection, changed settings and historical success remain separate from current eligibility',async({page})=>{
  const {box,root} = await ready(page);
  await box.getByText('Controlled publisher test settings',{exact:true}).click();
  await box.getByLabel('Simulated publisher outcome').selectOption('reject');
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR rejected',exact:true})).toBeVisible();
  await shot(page,'rejected');
  await box.getByRole('button',{name:'Start another sample preview',exact:true}).click();
  await box.getByLabel('Simulated publisher outcome').selectOption('success');
  await box.getByLabel('Sample PR title').fill('Second explicit sample preview');
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  fixture('admission-revoke');
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await box.getByRole('button',{name:'Review changes',exact:true}).click();
  await expect(box.getByText('Historical result only.',{exact:false})).toBeVisible();
  await shot(page,'historical-current-invalid');
  await box.getByText('Sample PR history (2)',{exact:true}).click();
  await shot(page,'history');
  const history=(await (await page.request.get(root+'/review')).json()).history;
  expect(history.map((r:{status:string})=>r.status)).toEqual(['prepared','rejected']);
  expect(history.every((r:{current_eligible:boolean})=>!r.current_eligible)).toBe(true);
});

test('changed approval after save holds preparation; missing comparison is explicit',async({page})=>{
  await page.setViewportSize({width:390,height:1000});
  const {box} = await ready(page);
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  fixture('admission-revoke');fixture('worker');
  await expect(page.getByRole('heading',{name:'Review the current plan again'})).toBeVisible();
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  const review = box.getByRole('button',{name:'Review changes',exact:true});
  await review.click();
  await expect(review).toHaveAttribute('aria-expanded','true');
  await expect(box.getByRole('heading',{name:'Sample PR needs attention',exact:true})).toBeVisible();
  await shot(page,'approval-held-390');
  fixture('sample-artifact-missing');
  await expect(box.getByText('Comparison inputs are unavailable; no diff was invented.',{exact:true})).toBeVisible();
  await shot(page,'comparison-unavailable-390');
});

test('failed refresh holds action; controlled renderer handles large, rename, deletion, binary and untrusted content',async({page})=>{
  await page.setViewportSize({width:390,height:1000});
  const {box,root} = await ready(page);
  await page.route('**/candidates/*/review',route=>route.fulfill({status:503,json:{detail:'Fixture unavailable'}}));
  await expect(box.getByRole('alert')).toBeVisible({timeout:15000});
  await expect(box.getByText('Last loaded approval and checks are shown; current validity could not be refreshed.',{exact:true})).toBeVisible();
  await expect(box.getByRole('button',{name:'Prepare sample PR',exact:true})).toBeDisabled();
  await shot(page,'refresh-failed-390');
  await page.unroute('**/candidates/*/review');
  const context=await (await page.request.get(root+'/review')).json();
  context.review.eligible=false;
  context.review.fingerprint='f'.repeat(64);
  context.review.message='Controlled rendering fixture. Preparation held.';
  context.review.files=[
    {path:'new-name.txt',old_path:'old-name.txt',kind:'renamed',additions:0,deletions:0,diff:'',truncated:false},
    {path:'deleted.txt',kind:'deleted',additions:0,deletions:1,diff:'--- a/deleted.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old line\n',truncated:false},
    {path:'binary.dat',kind:'binary',reason:'Binary content; text comparison unavailable.',truncated:false},
    {path:'missing-base.txt',kind:'unavailable',reason:'Base content unavailable; no diff was invented.',truncated:false},
    {path:'large.txt',kind:'modified',additions:2000,deletions:2000,diff:'<img src=x onerror="window.injected=true">\n'+('+long-untrusted-line '.repeat(100)+'\n').repeat(12),truncated:true,reason:'Diff truncated; preparation is held.'},
  ];
  await page.route('**/candidates/*/review',route=>route.fulfill({json:context}));
  await page.reload();
  await box.getByRole('button',{name:'Review changes',exact:true}).click();
  await expect(box.getByText('This candidate or its evidence differs from the comparison previously opened.',{exact:false})).toBeVisible();
  await expect(box.getByRole('heading',{name:'old-name.txt → new-name.txt',exact:true})).toBeVisible();
  await expect(box.getByText('The path changed; text is unchanged.',{exact:true})).toBeVisible();
  await expect(box.getByLabel('Unified diff for large.txt')).toContainText('<img src=x');
  expect(await page.evaluate(()=>('injected' in window))).toBe(false);
  await expect(box.locator('img')).toHaveCount(0);
  await expect(box.getByRole('button',{name:'Prepare sample PR',exact:true})).toBeDisabled();
  await shot(page,'unsupported-large-untrusted-390');
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await shot(page,'unsupported-large-untrusted-390-200');
});


test('persisted preparing stage survives worker exit and browser reload',async({page})=>{
  await page.setViewportSize({width:390,height:1000});
  const {box,root} = await ready(page);
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting',exact:true})).toBeVisible();
  fixture('sample-pr-start-crash');
  await expect(box.getByRole('heading',{name:'Preparing',exact:true})).toBeVisible();
  await shot(page,'preparing-390');
  await page.reload();
  await expect(box.getByRole('heading',{name:'Preparing',exact:true})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  expect((await (await page.request.get(root+'/review')).json()).history).toHaveLength(1);
});


test('definite request rejection can be set aside explicitly; latest inputs use a new request ID',async({page})=>{
  const {box,root} = await ready(page);
  let rejected='';
  await page.route('**/sample-pr',async route=>{rejected=route.request().postData()!;await route.fulfill({status:409,json:{detail:'The comparison changed. Review changes again.'}});});
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  await expect(box.getByText('The request was rejected.',{exact:false})).toBeVisible();
  await expect(box.getByRole('button',{name:'Recover saved sample PR request',exact:true})).toBeVisible();
  await box.getByRole('button',{name:'Review latest inputs for a new request',exact:true}).click();
  await page.unroute('**/sample-pr');
  await box.getByLabel('Sample PR title').fill('Fresh explicit sample request');
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting',exact:true})).toBeVisible();
  fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  const history=(await (await page.request.get(root+'/review')).json()).history;
  expect(history).toHaveLength(1);expect(history[0].id).not.toBe(JSON.parse(rejected).command_id);
});


test('evidence changed during open review requires explicit updated comparison before preparation',async({page})=>{
  const {box,root}=await ready(page);
  fixture('sample-check-change');
  await expect(box.getByText('This candidate or its evidence differs from the comparison previously opened.',{exact:false})).toBeVisible();
  await expect(box.getByRole('button',{name:'Prepare sample PR',exact:true})).toBeDisabled();
  await box.getByRole('button',{name:'Review updated changes',exact:true}).click();
  await expect(box.getByRole('button',{name:'Prepare sample PR',exact:true})).toBeEnabled();
  await shot(page,'evidence-changed-review');
  await box.getByRole('button',{name:'Prepare sample PR',exact:true}).click();
  await expect(box.getByRole('heading',{name:'Waiting',exact:true})).toBeVisible();fixture('worker');
  await expect(box.getByRole('heading',{name:'Sample PR prepared',exact:true})).toBeVisible();
  expect((await (await page.request.get(root+'/review')).json()).history).toHaveLength(1);
});


test('diff presentation preserves JSON text across desktop, phone, enlarged text and keyboard scrolling',async({page})=>{
  const {box,root}=await ready(page);
  const context=await (await page.request.get(root+'/review')).json();
  const diff=box.getByLabel('Unified diff for sample/label-examples.json');
  expect(await diff.textContent()).toBe(context.review.files[0].diff);
  for(const kind of ['added','removed','context','hunk','metadata'])
    expect(await diff.locator('.diff-'+kind).count()).toBeGreaterThan(0);
  for(const kind of ['key','string'])
    expect(await diff.locator('.json-'+kind).count()).toBeGreaterThan(0);
  await expect(diff.locator('.diff-added').first()).toHaveText(/^\+/);
  await expect(diff.locator('.diff-removed').first()).toHaveText(/^-/);
  for(const [width,scale] of [[1440,100],[390,100],[390,200]]) {
    await page.setViewportSize({width,height:1000});
    await page.evaluate(s=>document.documentElement.style.fontSize=s+'%',scale);
    await diff.evaluate(el=>el.scrollLeft=0);
    await diff.focus();
    await page.keyboard.press('ArrowRight');
    await expect(diff).toBeFocused();
    expect(await diff.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none');
    await diff.screenshot({path:artifactPath(`diff-presentation/json-${width}-${scale}.png`)});
    await shot(page,`review-diff-${width}-${scale}`);
    if(width===390) {
      expect(await diff.evaluate(el=>el.scrollWidth>el.clientWidth)).toBe(true);
      await page.keyboard.press('ArrowRight');
      await expect.poll(()=>diff.evaluate(el=>el.scrollLeft)).toBeGreaterThan(0);
    }
    expect(await diff.textContent()).toBe(context.review.files[0].diff);
  }
  await page.keyboard.press('Tab');
  await expect(diff).not.toBeFocused();
});

for(const width of [1440,390]) test(`real execution review remains distinct and read only at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const view={id:'controlled-real-review',title:'Controlled machinery review: Chronicle',status:'blocked',
    message:'Activation has not passed. No inference ran.',base:'f48b2e0bbdf966293695de8b1435b0e7e4372224',
    current_grant:false,grant_id:'controlled-no-real-grant',expires_at:'2026-11-04T00:00:00Z',
    publication_allowed:false,source:'real',result:{inference_ran:false,
      gates:[{name:'Exclusive model ownership',status:'blocked',evidence:'Controlled test evidence.'}]}};
  await page.route('**/api/real-executions/*',route=>route.fulfill({json:view}));
  const writes:string[]=[];
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.url());});
  await page.goto('/executions/controlled-real-review');
  const box=page.getByRole('region',{name:'Run result and checks'});
  await expect(box.getByText('No model implementation ran.',{exact:true})).toBeVisible();
  await expect(box.getByText('No changes were saved for review.',{exact:true})).toBeVisible();
  await expect(box.getByRole('button',{name:/prepare|publish|implement/i})).toHaveCount(0);
  await overflow(page);
  await box.screenshot({path:artifactPath(`chronicle-real/screenshots/blocked-${width}.png`)});
  await box.getByText('Technical results and run details',{exact:true}).click();
  await page.keyboard.press('Tab');
  if(width===390){
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');
    await overflow(page);
    expect(await page.locator('h1').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await box.screenshot({path:artifactPath('chronicle-real/screenshots/blocked-390-200.png')});
  }
  expect(writes).toEqual([]);
});

test('real review renders frozen Lua changes, historical validity and escaped evidence',async({page})=>{
  await page.setViewportSize({width:390,height:1000});
  const view={id:'controlled-real-candidate',title:'Controlled real-candidate rendering fixture',status:'checked',
    message:'Controlled UI evidence only; no actual inference.',base:'f48b2e0bbdf966293695de8b1435b0e7e4372224',
    current_grant:false,grant_id:'fixture-only',expires_at:'2026-11-04T00:00:00Z',publication_allowed:false,source:'real',
    result:{inference_ran:false,verification:'passed',candidate_id:'fixture-only',
      files:[{path:'example.lua',kind:'modified',additions:1,deletions:1,truncated:false,
        diff:'--- a/example.lua\n+++ b/example.lua\n@@ -1 +1 @@\n-local value = 1\n+local value = "<img src=x onerror=alert(1)>"\n'}],
      checks:[{status:'pass',rc:0,out:'<script>untrusted evidence</script>'}]}};
  await page.route('**/api/real-executions/*',route=>route.fulfill({json:view}));
  await page.goto('/executions/controlled-real-candidate');
  const box=page.getByRole('region',{name:'Run result and checks'});
  await box.getByText('Technical results and run details',{exact:true}).click();
  await expect(box.getByText(/expires .* Historical/)).toBeVisible();
  await box.getByText('See the changes in code',{exact:true}).click();
  const diff=box.getByLabel('Unified diff for example.lua');
  await expect(diff.locator('.diff-added')).toContainText('<img src=x');
  await expect(box.locator('img')).toHaveCount(0);
  await expect(diff.locator('[class^="json-"]')).toHaveCount(0);
  await diff.focus();await page.keyboard.press('ArrowRight');
  await expect(diff).toBeFocused();
  await box.screenshot({path:artifactPath('chronicle-real/screenshots/controlled-candidate-390.png')});
  for(const status of ['pending','checking','failed','interrupted']){
    view.status=status;
    await page.reload();
    await expect(box).toBeVisible();
    await box.screenshot({path:artifactPath(`chronicle-real/screenshots/controlled-${status}-390.png`)});
  }
});

for(const width of [1440,390])test(`durable real runs are reachable from Work and retain completion after refresh at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const completed={id:'non-model-handoff',title:'Controlled handoff result — not a real model run',status:'checked',message:'Verified completion recovered. Nothing was rerun or published.',created_at:'2026-11-04T12:00:00Z',finished_at:'2026-11-04T12:01:00Z',verification:'passed',has_candidate:true};
  const active={...completed,id:'non-model-working',title:'Controlled active run',status:'running',message:'Working in isolation.',finished_at:null,verification:null,has_candidate:false};
  const writes:string[]=[];page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.url());});
  await page.route('**/api/real-executions?*',route=>{
    const url=new URL(route.request().url()),group=url.searchParams.get('group');
    const items=group==='active'?[active]:group==='checked'?[completed]:[active,completed];
    return route.fulfill({json:{available:true,items,total:items.length,page:1,page_size:12,publication_allowed:false}});
  });
  await page.route('**/api/real-executions/non-model-handoff',route=>route.fulfill({json:{...completed,base:'frozen-test-base',current_grant:false,grant_id:'NON-MODEL',expires_at:'2026-11-04T12:00:00Z',source:'real',publication_allowed:false,result:{inference_ran:false,verification:'passed',files:[],checks:[{status:'pass',rc:0,out:'NON-MODEL check evidence'}]}}}));
  await page.goto('/');
  await page.getByRole('link',{name:'Run results',exact:true}).click();
  const runs=page.getByRole('region',{name:'Recorded real task runs'});
  await expect(runs.getByRole('list').getByText('Checks passed',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Run results',exact:true})).toBeVisible();
  await page.getByLabel('Filter task runs').selectOption('checked');
  await expect(page.getByRole('link',{name:active.title})).toHaveCount(0);
  await page.getByRole('link',{name:completed.title}).click();
  const result=page.getByRole('region',{name:'Run result and checks'});
  for(const reload of [false,true]){
    if(reload) await page.reload();
    await expect(result.getByRole('heading',{name:'Changes checked',exact:true})).toBeVisible();
    await result.getByText('Technical results and run details',{exact:true}).click();
    await expect(result.getByText(completed.message,{exact:true})).toBeVisible();
  }
  await page.getByRole('link',{name:'All run results',exact:true}).click();
  await overflow(page);
  if(width===390){await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);}
  await page.getByLabel('Filter task runs').focus();await page.keyboard.press('Tab');
  await page.getByRole('region',{name:'Recorded real task runs'}).screenshot({path:artifactPath(`m5-handoff/screenshots/runs-${width}.png`)});
  expect(writes).toEqual([]);
});

test('running model activity is unknown rather than falsely reported absent; stale evidence stays visible',async({page})=>{
  let failed=false;
  await page.route('**/api/real-executions/non-model-active',route=>failed?route.abort():route.fulfill({json:{id:'non-model-active',title:'Controlled active evidence',status:'running',message:'Working in isolation.',base:'controlled',current_grant:true,grant_id:'NON-MODEL',expires_at:'2099-01-01T00:00:00Z',source:'real',publication_allowed:false,result:{inference_ran:false}}}));
  await page.goto('/executions/non-model-active');
  const result=page.getByRole('region',{name:'Run result and checks'});
  await expect(result.getByRole('heading',{name:'Working in a separate copy',exact:true})).toBeVisible();
  await result.getByText('Technical results and run details',{exact:true}).click();
  await expect(result.getByText('Model execution was not reported for this result.',{exact:true})).toBeVisible();
  await expect(result.getByText('No model implementation ran.',{exact:true})).toHaveCount(0);
  failed=true;
  await expect(result.getByRole('alert').filter({hasText:'Refresh unavailable. Showing the last loaded result.'})).toBeVisible({timeout:15000});
  await expect(result.getByText('Model execution was not reported for this result.',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Controlled active evidence'})).toBeVisible();
});

for(const width of [1440,390])test(`task progress connects the submitted task to retained result at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  // UI-only response fixtures. The separate actual-worker control proves integration.
  const identity='non-model-task-request',task='intake-non-model-task-request-1-example';
  const source={human_scope:{revision:1,visual_requirement:'nonvisual',stage:'implementation',origin:'human',note:'NON-MODEL',created_at:'2026-11-04T12:00:00Z'},scope_history:[],id:identity,
    original:{request_id:identity,kind:'feature',title:'Controlled task delivery',description:'NON-MODEL browser state fixture',visual_requirement:'nonvisual',lua_error:''},status:'tasks_ready',revision:1,created_at:'2026-11-04T12:00:00Z',clarifications:[],jobs:[],proposals:[],decisions:[],result:null,evidence:[],priority:{severity:'Feature request',priority:4,reason:'Controlled',execution:'Not authorized',manual:false},
    tasks:[{id:task,proposal_id:null,parent:'intake-'+identity,kind:'task',title:'Controlled task result',purpose:'<script>Untrusted task purpose</script>',acceptance:['NON-MODEL check'],model:'NON-MODEL',scope_review_required:false,dependencies:[]}]};
  const row={id:'controlled-linked-run',status:'checked',message:'Verified completion recovered. Nothing was rerun or published.',verification:'passed',candidate_id:'NON-MODEL',current_task:true};
  const progress={intake_id:identity,task_id:task,stage:'checked',message:row.message,current_approval:true,execution_configured:false,runs:[row],publication_allowed:false,accepted:false};
  let unavailable=false;
  await page.route('**/api/intake/'+identity,route=>route.fulfill({json:source}));
  await page.route('**/api/intake/'+identity+'/tasks/**/admission',route=>route.fulfill({json:{supported:false,current_authority:{eligible:false,message:'Not a controlled sample.'},receipts:[]}}));
  await page.route('**/api/intake/'+identity+'/tasks/**/implementation',route=>route.fulfill({json:{supported:false,current_authority:{eligible:false,message:'Not a controlled sample.'},receipts:[]}}));
  await page.route('**/api/intake/'+identity+'/tasks/**/execution',route=>unavailable?route.fulfill({status:503,json:{detail:'Controlled refresh interruption'}}):route.fulfill({json:progress}));
  await page.route('**/api/real-executions/controlled-linked-run',route=>route.fulfill({json:{id:row.id,title:'Controlled task result',status:row.status,message:row.message,base:'frozen-base',current_grant:false,grant_id:'NON-MODEL',expires_at:'2026-11-04T12:00:00Z',publication_allowed:false,source:'real',workflow_task:{intake_id:identity,task_id:task,binding:'NON-MODEL'},current_task:true,result:{inference_ran:false,verification:'passed',candidate_id:'NON-MODEL',files:[],checks:[]}}}));
  const writes:string[]=[];page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.url());});
  await page.goto('/intake/'+identity);const box=page.getByRole('region',{name:'Task implementation progress'});
  await expect(box.getByRole('heading',{name:'Checks passed',exact:true})).toBeVisible();
  await box.getByRole('link',{name:'Review changes and checks'}).focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'Controlled task result',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'Back to the task'}).click();await expect(box).toBeVisible();
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await overflow(page);mkdirSync(artifactPath('m5-task-execution/screenshots'),{recursive:true});
  await box.screenshot({path:artifactPath(`m5-task-execution/screenshots/task-complete-${width}.png`)});
  for(const stage of ['pending','running','checking','recovering','failed','cancel_requested','cancelled','interrupted']){
    progress.stage=stage;progress.message='Controlled '+stage+' — no model was run';row.status=stage;row.verification=stage==='checking'?'unknown':'not_completed';row.candidate_id='';
    await page.reload();await expect(box.getByText(progress.message,{exact:true})).toBeVisible();await overflow(page);
    await box.screenshot({path:artifactPath(`m5-task-execution/screenshots/task-${stage}-${width}.png`)});
  }
  progress.current_approval=false;row.current_task=false;progress.stage='checked';row.status='checked';row.candidate_id='NON-MODEL';
  await page.reload();await expect(box.getByText('These results are from an earlier version of the task.',{exact:false})).toBeVisible();
  unavailable=true;await page.waitForTimeout(3300);await expect(box.getByText('Progress could not be refreshed.',{exact:false})).toBeVisible();
  await expect(box.getByRole('link',{name:'Review changes and checks'})).toBeVisible();
  await box.screenshot({path:artifactPath(`m5-task-execution/screenshots/task-stale-history-${width}.png`)});
  await expect(page.locator('script').filter({hasText:'Untrusted task purpose'})).toHaveCount(0);expect(writes).toEqual([]);
});

test('task progress saves an explicit stop once and keeps its returned state',async({page})=>{
  const identity='non-model-stop',task='non-model-stop-task';
  // Reuse the real issue/triage path to get a task; only its execution state is controlled.
  await page.goto('/intake/new?kind=issue');await page.getByLabel('Title Required').fill('Controlled stop request');await page.getByLabel('Description Required').fill('NON-MODEL: exercise the task stop state with controlled transport only.');await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');await page.getByRole('button',{name:'Submit request',exact:true}).click();fixture('planning');
  await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
  const progress={intake_id:identity,task_id:task,stage:'running',message:'Controlled active operation; no inference.',current_approval:true,execution_configured:false,runs:[{id:'controlled-stop-run',status:'running',message:'Controlled active operation',verification:null,candidate_id:null,current_task:true}],publication_allowed:false,accepted:false};
  await page.route('**/api/intake/*/tasks/*/execution',route=>route.fulfill({json:progress}));let saved:string|undefined;
  await page.route('**/api/real-executions/controlled-stop-run/stop',route=>{saved=route.request().postDataJSON().command_id;progress.stage='cancel_requested';progress.message='Stopping. Waiting for confirmed cleanup.';progress.runs[0].status='cancel_requested';return route.fulfill({json:{id:'controlled-stop-run',status:'cancel_requested'}});});
  await page.reload();const box=page.getByRole('region',{name:'Task implementation progress'});await box.getByRole('button',{name:'Stop this run'}).click();await expect(box.getByText('Stopping. Waiting for confirmed cleanup.',{exact:true})).toBeVisible();await expect(box.getByRole('button',{name:'Stop this run'})).toHaveCount(0);expect(saved).toMatch(/^[0-9a-f-]{36}$/);
});

for(const width of [1440,390])test(`unattended recovery explains the held lane and retained outcome at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const row={id:'non-model-recovery',title:'Controlled recovery — no model operation',status:'running',recovery_pending:true,
    message:'Waiting for confirmed cleanup. Saved evidence is retained; no new execution can start.',created_at:'2026-11-05T02:00:00Z',finished_at:null as string|null,verification:'unknown',has_candidate:false};
  const writes:string[]=[];page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))writes.push(r.url());});
  await page.route('**/api/real-executions?*',route=>route.fulfill({json:{available:true,items:[row],total:1,page:1,page_size:12,publication_allowed:false}}));
  await page.route('**/api/real-executions/non-model-recovery',route=>route.fulfill({json:{...row,base:'NON-MODEL',current_grant:false,grant_id:'NON-MODEL',expires_at:'2026-11-05T02:00:00Z',source:'real',publication_allowed:false,result:{inference_ran:false,verification:row.verification,error:row.status==='failed'?'<script>controlled failure</script>':undefined,
    check_failure:row.status==='failed'?{name:'focused',diagnostic:'test_normalize.lua:4: assertion failed <script>controlled output</script>',context_hint:{path:'tests/test_normalize.lua',start:1,end:8}}:undefined}}}));
  await page.goto('/executions');
  const list=page.getByRole('region',{name:'Recorded real task runs'});
  await expect(list.getByText('Waiting for safe recovery',{exact:true})).toBeVisible();
  await list.getByRole('link',{name:row.title}).focus();await page.keyboard.press('Enter');
  const box=page.getByRole('region',{name:'Run result and checks'});
  await expect(box.getByRole('heading',{name:'Waiting for safe recovery'})).toBeVisible();
  await expect(box.getByText(/waiting to confirm cleanup/)).toBeVisible();
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await overflow(page);mkdirSync(artifactPath('m5-unattended/screenshots'),{recursive:true});
  await box.screenshot({path:artifactPath(`m5-unattended/screenshots/recovery-${width}.png`)});
  await page.reload();await expect(box.getByRole('heading',{name:'Waiting for safe recovery'})).toBeVisible();
  row.recovery_pending=false;row.status='failed';row.verification='unknown';row.finished_at='2026-11-05T02:01:00Z';
  row.message='Failure outcome recovered. Evidence and known usage retained; no attempt will restart.';
  await page.reload();
  await expect(box.getByRole('heading',{name:'Work could not finish',exact:true})).toBeVisible();
  await expect(box.locator(':scope > p').and(box.getByText(row.message,{exact:true}))).toBeVisible();
  await box.getByText('Technical results and run details',{exact:true}).click();
  await expect(box.getByText('<script>controlled failure</script>',{exact:true})).toBeVisible();
  const failedCheck=box.getByRole('region',{name:'Last failed check'});
  await expect(failedCheck).toContainText('focused · tests/test_normalize.lua');
  await failedCheck.getByText('See the failure output',{exact:true}).click();
  await expect(failedCheck).toContainText('test_normalize.lua:4: assertion failed <script>controlled output</script>');
  expect(await failedCheck.locator('pre').evaluate(el=>el.scrollWidth <= el.clientWidth)).toBe(true);
  await expect(box.locator('script')).toHaveCount(0);
  await expect(box.getByText('Saved evidence is retained.',{exact:false})).toHaveCount(0);
  await page.getByText('Technical results and run details',{exact:true}).focus();await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');await overflow(page);
  await box.screenshot({path:artifactPath(`m5-unattended/screenshots/failure-delivered-${width}.png`)});
  expect(writes).toEqual([]);
});
