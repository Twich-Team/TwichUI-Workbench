import { test, expect, type Page } from '@playwright/test';
import { fixture, overflow, capture } from './helpers';
import type { components } from '../frontend/src/api.generated';
import { Buffer } from 'node:buffer';

type Intake = components['schemas']['IntakeView'];
async function snapshot(page:Page):Promise<Intake> { return page.request.get('/api/intake/'+page.url().split('/intake/')[1].split('?')[0]).then(r=>r.json()); }
async function submit(page:Page,kind='feature',title='Guide navigation',description='Keep existing keyboard controls and remember the selected guide step after reload.',lua='') {
  await page.goto(`/intake/new?kind=${kind}`);
  await page.getByLabel('Title Optional').fill(title);
  await page.getByLabel('Description', {exact:true}).fill(description);
  if(lua) {await page.getByLabel('Add a Lua error').check();await page.getByLabel('Lua error Plain text').fill(lua);}
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  await expect(page.getByText('Waiting to start',{exact:true}).first()).toBeVisible();
}
// Generate a valid, plainly synthetic PNG in-browser for attachment tests.
async function synthetic(page:Page) { return Buffer.from(await page.evaluate(()=>{const c=document.createElement('canvas');c.width=500;c.height=250;const x=c.getContext('2d')!;x.fillStyle='#34271e';x.fillRect(0,0,500,250);x.fillStyle='#eee5d5';x.font='22px sans-serif';x.fillText('SYNTHETIC WORKFLOW TEST',40,95);x.fillText('Not game or simulator evidence',40,150);return c.toDataURL('image/png').split(',')[1];}),'base64'); }

test.beforeEach(()=>fixture('reset'));

test('A · issue without Lua saves, investigates, and links an actionable triage task',async({page},info)=>{
  await submit(page,'issue','Guide selection resets');
  fixture('planning');
  await expect(page.getByRole('heading',{name:'Investigation findings'})).toBeVisible();
  await expect(page.getByText('Demo triage: the report is retained; the cause is unverified.')).toBeVisible();
  await expect(page.getByRole('heading',{name:'What approval means',exact:true})).not.toBeVisible();
  await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:/Investigate: Guide selection resets/})).toBeVisible();
  await page.reload();
  expect((await snapshot(page)).original.lua_error).toBe('');
  await overflow(page);await capture(page,info,'m3-issue-desktop');
  await page.getByRole('link',{name:/Investigate: Guide selection resets/}).click();
  await expect(page.getByRole('link',{name:'Open request and proposal'})).toBeVisible();
});

test('B · untrusted Lua stays literal at phone width; clarification remains in the app',async({page},info)=>{
  await page.setViewportSize({width:390,height:844});
  const lua='Interface/Guide.lua:42: attempt to index nil\n<script>window.pastedExecuted=true</script>\n'+ 'LongLiteralEvidence'.repeat(30);
  await submit(page,'issue','A long error report','Clicking the guide step after a reload loses selection and produces this error.',lua);
  fixture('planning');
  await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await expect(page.getByText(lua,{exact:true})).toHaveText(lua);
  expect(await page.evaluate(()=>('pastedExecuted' in window))).toBe(false);
  await page.reload();expect((await snapshot(page)).original.lua_error).toBe(lua);
  await overflow(page);await capture(page,info,'m3-issue-phone');
  fixture('reset');
  await submit(page,'issue','Intermittent issue','It breaks.');fixture('planning');
  await expect(page.getByText('Workbench needs your help')).toBeVisible();
  await page.getByLabel('Your answer').fill('Open a guide, pick a step, reload, then selection disappears.');
  await page.getByRole('button',{name:'Send information'}).click();
  await expect(page.getByText('Waiting to start',{exact:true}).first()).toBeVisible();fixture('planning');await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
  expect((await snapshot(page)).original.description).toBe('It breaks.');
});

test('C D E · revised proposal, exact approval receipt, one hierarchy, stale rejection',async({page},info)=>{
  await submit(page);fixture('planning');
  await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
  const old=await snapshot(page); const oldProposal=old.proposals[0];
  await page.getByText('Add a note or request changes',{exact:true}).click();
  await page.getByLabel('Note Required when requesting changes').fill('Preserve keyboard navigation and keep research bounded.');
  await page.getByRole('button',{name:'Request changes',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('apply-planning');fixture('planning');
  await page.getByRole('button',{name:'Open current proposal'}).click();
  await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
  expect((await snapshot(page)).proposals[0].revision).toBe(2);
  const session=await page.request.get('/api/session').then(r=>r.json());
  const stale=await page.request.post('/api/intake/'+old.id+'/decisions',{headers:{origin:'http://127.0.0.1:5380','x-csrf-token':session.csrf_token},data:{command_id:crypto.randomUUID(),proposal_id:oldProposal.id,binding:oldProposal.binding,scope_revision:oldProposal.scope_revision,action:'approve',note:''}});
  expect(stale.status()).toBe(409);
  // Explicitly restrict to research instead of waiving missing visual acceptance.
  await page.getByText('Revise to a research stage',{exact:true}).click();
  await page.getByLabel('Research scope explanation').fill('Inspect relevant addon evidence; return before implementation.');
  await page.getByRole('button',{name:'Create research revision'}).click();
  await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Approve plan',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  expect((await snapshot(page)).tasks).toHaveLength(0);
  await capture(page,info,'m3-approval-pending-desktop');
  fixture('apply-planning');await expect(page.getByRole('region',{name:'Task progress',exact:true}).getByText('Plan approved',{exact:true})).toBeVisible();
  fixture('planning');await expect(page.getByText('Plan ready',{exact:true})).toBeVisible();
  const complete=await snapshot(page);expect(complete.tasks).toHaveLength(2);
  fixture('planning');await page.reload();expect((await snapshot(page)).tasks).toHaveLength(2);
  await page.setViewportSize({width:390,height:844});await overflow(page);await capture(page,info,'m3-approved-phone');
  await page.getByRole('link',{name:'Open in Backlog'}).click();
  await expect(page.getByRole('button',{name:'Collapse Guide navigation',exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:/Assess feasibility — Guide navigation/})).toBeVisible();
  await overflow(page);await capture(page,info,'m3-hierarchy-phone');
});

test('F · missing mockups block approval; synthetic attachments bind the revision',async({page},info)=>{
  await submit(page,'feature','Guide panel layout','Show a clear guide panel layout with a compact step button.');fixture('planning');
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeDisabled();
  await page.getByText('Attach a picture or supporting note',{exact:false}).click();
  const image=await synthetic(page);
  for(const [index,role] of ['mockup','game_context'].entries()) {
    await page.getByLabel('Artifact role').selectOption(role);
    await page.getByLabel('Artifact label').fill(`Synthetic ${role} · not in-game evidence`);
    await page.getByLabel('Private attachment').setInputFiles({name:`synthetic-${role}.png`,mimeType:'image/png',buffer:image});
    await page.getByRole('button',{name:'Attach to this plan'}).click();
    await expect(page.getByText('This review is out of date.')).toBeVisible();
    await page.getByRole('button',{name:'Open refreshed review'}).click();
    await expect(page.locator('.proposal-artifacts figure')).toHaveCount(index+1);
    if(index===0) await page.getByText('Attach a picture or supporting note',{exact:false}).click();
  }
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeEnabled();
  await overflow(page);await capture(page,info,'m3-mockups-desktop');
  await page.getByRole('button',{name:'Approve plan',exact:true}).click();
  await expect(page.getByRole('region',{name:'Latest plan decision'}).getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('apply-planning');
  fixture('fail-planning');await expect(page.getByText('Approved work could not be prepared',{exact:true})).toBeVisible();
  const failed = await snapshot(page);
  expect(failed.status).toBe('decomposition_failed');
  expect(failed.decisions).toHaveLength(1);
  expect(failed.decisions[0].status).toBe('applied');
  expect(failed.tasks).toHaveLength(0);
  const decomposition = failed.jobs.filter(job => job.kind === 'decomposition');
  expect(decomposition).toHaveLength(1);
  expect(decomposition[0].status).toBe('failed');
  await capture(page,info,'m3-decomposition-failed-desktop',{keepInCompact:true});

  const retryResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/intake/${failed.id}/retry`) && response.request().method() === 'POST');
  await page.getByRole('button',{name:'Retry planning'}).click();
  const response = await retryResponse;
  expect(response.status()).toBe(200);
  const queued: Intake = await response.json();
  expect(queued.status).toBe('approved');
  expect(queued.decisions).toEqual(failed.decisions);
  expect(queued.tasks).toHaveLength(0);
  expect(queued.jobs.filter(job => job.kind === 'decomposition')).toEqual([
    expect.objectContaining({id: decomposition[0].id, status: 'queued', attempts: decomposition[0].attempts}),
  ]);
  const progress = page.getByRole('region',{name:'Task progress'});
  await expect(progress.getByText('Plan approved',{exact:true})).toBeVisible();
  await expect(progress.getByText('Workbench is preparing the agreed work.',{exact:true})).toBeVisible();

  fixture('planning');
  await expect(progress.getByText('Plan ready',{exact:true})).toBeVisible();
  const finished = await snapshot(page);
  expect(finished.status).toBe('tasks_ready');
  expect(finished.decisions).toEqual(failed.decisions);
  expect(finished.jobs.filter(job => job.kind === 'decomposition')).toEqual([
    expect.objectContaining({id: decomposition[0].id, status: 'completed', attempts: decomposition[0].attempts + 1}),
  ]);
  expect(finished.tasks.length).toBeGreaterThan(0);
});

test('unavailable planning, response-loss intake retry, empty/loading/error states and enlarged phone text',async({page},info)=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/intake');await expect(page.getByText('No requests yet.')).toBeVisible();await capture(page,info,'m3-empty-phone');
  let hold:(()=>void)|undefined;
  const held=new Promise<void>(resolve=>{hold=resolve;});
  await page.route('**/api/intake?page=*',async route=>{await held;await route.continue();});
  await page.reload();await expect(page.getByText('Opening your tasks…')).toBeVisible();await capture(page,info,'m3-loading-phone');hold!();await expect(page.getByText('No requests yet.')).toBeVisible();await page.unrouteAll({behavior:'wait'});
  await page.route('**/api/intake',async route=>{if(route.request().method()==='POST'){await route.fetch();await route.abort();}else await route.continue();});
  await page.goto('/intake/new?kind=issue');await page.getByLabel('Title Optional').fill('A retained report');await page.getByLabel('Description', {exact:true}).fill('Repeated behavior with original input that must survive a lost response.');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();
  await page.unroute('**/api/intake');await page.reload();await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  expect(await page.request.get('/api/intake').then(r=>r.json()).then(d=>d.total)).toBe(1);
  fixture('off-planning');await expect(page.getByText('Planning could not finish',{exact:true})).toBeVisible();
  await page.evaluate(()=>{document.documentElement.style.fontSize='24px';});await overflow(page);await capture(page,info,'m3-failed-enlarged-phone');
  await page.getByRole('button',{name:'Retry planning'}).click();await expect(page.getByText('Waiting to start',{exact:true}).first()).toBeVisible();fixture('planning');await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
  await overflow(page);
});

test('required forms, optional Lua input and enlarged text fit desktop and phone',async({page},info)=>{
  for(const width of [1440,390]) {
    await page.setViewportSize({width,height:width===390?844:1000});
    await page.goto('/intake/new?kind=issue');
    await page.getByLabel('Description',{exact:true}).fill('');
    await expect(page.getByRole('button',{name:'Submit request',exact:true})).toBeDisabled();
    await page.getByLabel('Title Optional').fill('Guide navigation '.repeat(9));
    await page.getByLabel('Description', {exact:true}).fill('After reloading a guide, the selected step should stay in place. '.repeat(25));
    await page.getByLabel('Add a Lua error').check();
    await page.getByLabel('Lua error Plain text').fill('Interface/Guide.lua:42: literal synthetic error\n'+ 'UnbrokenEvidence'.repeat(30));
    await expect(page.getByRole('button',{name:'Submit request',exact:true})).toBeEnabled();
    if(width===390) await page.evaluate(()=>{document.documentElement.style.fontSize='24px';});
    await overflow(page);await capture(page,info,`m3-form-${width}`,{keepInCompact:true});
  }
});

test('representative synthetic proposal leads with experience and produces concise distinct work',async({page},info)=>{
  const {readFileSync}=await import('node:fs');
  const example=JSON.parse(readFileSync('fixtures/intake/guide-status.json','utf8'));
  await submit(page,'feature',example.request.title,example.request.description);fixture('planning');
  await expect(page.getByRole('heading',{name:'Know which step the guide is tracking',exact:true})).toBeVisible();
  await expect(page.locator('.planning-context')).not.toHaveAttribute('open');
  await expect(page.getByRole('heading',{name:'Planning progress',exact:true})).not.toBeVisible();
  await expect(page.getByText('Technical plan details',{exact:true})).toBeVisible();
  const approve=page.getByRole('button',{name:'Approve plan',exact:true});
  await expect(approve).toBeDisabled();await expect(approve).toHaveAttribute('aria-describedby','approval-prerequisites');
  await page.setViewportSize({width:390,height:844});await overflow(page);await capture(page,info,'closeout-missing-mockups-390');await page.setViewportSize({width:1440,height:1000});
  await page.getByText('Attach a picture or supporting note',{exact:true}).click();
  for(const [index,[role,name]] of [['mockup','mockup'],['game_context','context']].entries()) {
    await page.getByLabel('Artifact role').selectOption(role);
    await page.getByLabel('Artifact label').fill(`Synthetic ${name}; not in-game or simulator evidence`);
    await page.getByLabel('Private attachment').setInputFiles(`fixtures/intake/guide-status-${name}.png`);
    await page.getByRole('button',{name:'Attach to this plan'}).click();
    await expect(page.getByText('This review is out of date.')).toBeVisible();
    await page.getByRole('button',{name:'Open refreshed review'}).click();
    if(index===0)await page.getByText('Attach a picture or supporting note',{exact:true}).click();
  }
  await expect(approve).toBeEnabled();
  for(const width of [1440,390]){await page.setViewportSize({width,height:width===390?844:1000});await overflow(page);await capture(page,info,`closeout-proposal-${width}`);}
  await approve.click();await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();fixture('apply-planning');fixture('planning');
  await expect(page.getByText('Plan ready',{exact:true})).toBeVisible();
  const data=await snapshot(page);expect(data.tasks).toHaveLength(4);
  expect(new Set(data.tasks.map(t=>t.title)).size).toBe(4);
  fixture('planning');expect((await snapshot(page)).tasks).toHaveLength(4);
  await page.getByRole('link',{name:'Open in Backlog',exact:true}).click();
  for(const task of example.decomposition.tasks)await expect(page.getByRole('link',{name:new RegExp(task.title)})).toBeVisible();
  for(const width of [1440,390]){await page.setViewportSize({width,height:width===390?844:1000});await overflow(page);await capture(page,info,`closeout-backlog-${width}`);}
  await page.goto('/?source=fixture');await expect(page.getByRole('link',{name:new RegExp(example.request.title)})).toBeVisible();await capture(page,info,'closeout-work-390');
});

for(const width of [1440,390]) {
  test(`WB-16 · human scope, contradictory planner, blockers and stale approval at ${width}px`,async({page,context},info)=>{
    await page.setViewportSize({width,height:width===390?844:1000});
    await page.goto('/intake/new?kind=feature');
    const scope=page.getByLabel('Does this change how the addon looks?');
    await expect(scope).toHaveValue('unclassified');
    await page.getByLabel('Title Optional').fill('Synthetic scope authority review');
    await page.getByLabel('Description', {exact:true}).fill('A human requests an interface change; the synthetic planner will contradict that requirement.');
    await scope.selectOption('visual');
    await overflow(page);await capture(page,info,`wb16-intake-scope-${width}`);
    await page.getByRole('button',{name:'Submit request',exact:true}).click();
    await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
    fixture('nonvisual-planning');
    await expect(page.getByText('In-game scope and attachments',{exact:true})).toBeVisible();
    await expect(page.locator('.scope-conflict')).toContainText('Your visual requirement still applies.');
    await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeDisabled();
    await expect(page.locator('#approval-prerequisites')).toContainText('proposed mockup');
    await expect(page.locator('#approval-prerequisites')).toContainText('in-game-context reference');
    await overflow(page);await capture(page,info,`wb16-conflict-missing-${width}`);
    const old=await snapshot(page),p=old.proposals[0];
    const session=await page.request.get('/api/session').then(r=>r.json());
    const headers={origin:'http://127.0.0.1:5380','x-csrf-token':session.csrf_token};
    const body={command_id:crypto.randomUUID(),proposal_id:p.id,binding:p.binding,scope_revision:p.scope_revision,action:'approve',note:''};
    expect((await page.request.post(`/api/intake/${old.id}/decisions`,{headers,data:body})).status()).toBe(422);
    const other=await context.newPage();
    await other.setViewportSize({width,height:width===390?844:1000});
    await other.goto(page.url());
    await other.getByText('Change what this plan may cover',{exact:true}).click();
    await other.getByLabel('Does this change how the addon looks?').selectOption('unclassified');
    await other.getByLabel('Why are you changing the scope?').fill('Human scope remains uncertain. Keep both reference requirements.');
    await overflow(other);await capture(other,info,`wb16-review-control-${width}`);
    await other.getByRole('button',{name:'Save and review the updated plan'}).click();
    await expect(other.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
    await expect(page.getByText('This review is out of date.',{exact:true})).toBeVisible();
    await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeDisabled();
    expect((await page.request.post(`/api/intake/${old.id}/decisions`,{headers,data:body})).status()).toBe(409);
    await overflow(page);await capture(page,info,`wb16-stale-scope-${width}`,{keepInCompact:true});
    await page.getByRole('button',{name:'Open current proposal'}).click();
    await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeDisabled();
    const current=await snapshot(page);expect(current.human_scope.visual_requirement).toBe('unclassified');
    expect(current.original.visual_requirement).toBe('visual');
    await other.close();
  });
}

test('declining a plan records the choice and produces no work',async({page})=>{
  await submit(page);
  fixture('planning');
  await page.getByRole('button',{name:'Decline',exact:true}).click();
  await expect(page.getByRole('region',{name:'Latest plan decision'}).getByText('Saved · Pending application')).toBeVisible();
  fixture('apply-planning');
  await expect(page.getByRole('region',{name:'Task progress',exact:true}).getByText('Plan declined',{exact:true})).toBeVisible();
  const saved=await snapshot(page);
  expect(saved.decisions[0]).toMatchObject({status:'applied',payload:{action:'decline'}});
  expect(saved.tasks).toHaveLength(0);
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toHaveCount(0);
});
